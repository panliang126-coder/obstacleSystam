"""Deterministic radar simulator implementing the shared SensorDriver port."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from uuid import UUID

from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.ports.drivers import SensorDescriptor
from low_altitude_ai.simulator.clock import SimClock
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory, RandomStream
from low_altitude_ai.simulator.scenario import RadarScenario, RadarTargetScenario


class DriverNotConnectedError(RuntimeError):
    """Samples were requested before the simulator driver was connected."""


def _position(target: RadarTargetScenario, elapsed_s: float) -> tuple[float, float, float]:
    return tuple(
        target.initial_enu_m[index] + target.velocity_enu_m_s[index] * elapsed_s
        for index in range(3)
    )  # type: ignore[return-value]


class RadarSimulatorDriver:
    """A basic ENU radar model with stable component-scoped noise streams."""

    def __init__(self, scenario: RadarScenario, clock: SimClock) -> None:
        self._scenario = scenario
        self._clock = clock
        prefix = f"sensor/{scenario.sensor_id}"
        self._noise = RandomStream(scenario.seed, f"{prefix}/noise")
        self._dropout = RandomStream(scenario.seed, f"{prefix}/dropout")
        self._event_ids = DeterministicUuid7Factory(
            RandomStream(scenario.seed, f"{prefix}/event-id")
        )
        self._session_ids = DeterministicUuid7Factory(
            RandomStream(scenario.seed, f"{prefix}/session-id")
        )
        self._health_ids = DeterministicUuid7Factory(
            RandomStream(scenario.seed, f"{prefix}/health-id")
        )
        self._run_id = DeterministicUuid7Factory(
            RandomStream(scenario.seed, "simulation/run-id")
        ).new(scenario.start_at)
        self._session_id: UUID | None = None
        self._connected = False
        self._closed = False

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def descriptor(self) -> SensorDescriptor:
        return SensorDescriptor(
            sensor_id=self._scenario.sensor_id,
            sensor_type="RADAR",
            frame_id=self._scenario.frame_id,
            nominal_rate_hz=self._scenario.rate_hz,
        )

    async def connect(self) -> None:
        if self._connected:
            return
        self._session_id = self._session_ids.new(self._clock.now())
        self._connected = True
        self._closed = False

    async def samples(self) -> AsyncIterator[Envelope]:
        if not self._connected or self._session_id is None:
            raise DriverNotConnectedError("radar simulator is not connected")
        period_s = 1.0 / self._scenario.rate_hz
        for sequence in range(self._scenario.sample_count):
            if not self._connected:
                return
            if sequence:
                await self._clock.sleep(period_s)
            yield self._sample(sequence, sequence * period_s)

    async def health(self) -> Envelope:
        now = self._clock.now()
        status = "HEALTHY" if self._connected else "STOPPED"
        event_id = self._health_ids.new(now)
        return Envelope(
            schema="health/1.0",
            event_id=event_id,
            trace_id=event_id,
            source=Source(
                service="simulator-service",
                instance_id=self._scenario.scenario_id,
                plugin="radar-sim",
                plugin_version="1.0.0",
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=self._run_id,
            mode=RuntimeMode.SIM,
            vehicle_id=self._scenario.vehicle_id,
            sequence=0,
            quality=Quality(valid=True, confidence=1.0),
            payload={
                "component_id": self._scenario.sensor_id,
                "component_type": "SENSOR",
                "status": status,
                "checked_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "dependencies": [],
                "data_freshness_ms": 0.0 if self._connected else None,
                "faults": [],
            },
        )

    async def close(self) -> None:
        self._connected = False
        self._closed = True

    def _sample(self, sequence: int, elapsed_s: float) -> Envelope:
        now = self._clock.now()
        event_id = self._event_ids.new(now)
        detections = [
            detection
            for target in sorted(self._scenario.targets, key=lambda item: item.target_id)
            if (detection := self._detect(target, elapsed_s)) is not None
        ]
        return Envelope(
            schema="sensor/1.0",
            event_id=event_id,
            trace_id=event_id,
            source=Source(
                service="simulator-service",
                instance_id=self._scenario.scenario_id,
                plugin="radar-sim",
                plugin_version="1.0.0",
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=self._run_id,
            mode=RuntimeMode.SIM,
            vehicle_id=self._scenario.vehicle_id,
            sequence=sequence,
            quality=Quality(
                valid=True,
                confidence=self._scenario.detection_probability,
                flags=(),
                clock_uncertainty_ms=0.0,
            ),
            payload={
                "sensor_id": self._scenario.sensor_id,
                "sensor_type": "RADAR",
                "source_session_id": str(self._session_id),
                "frame_id": self._scenario.frame_id,
                "calibration_id": self._scenario.calibration_id,
                "sample_format": "radar_detections_v1",
                "sample": {"scan_id": sequence, "detections": detections},
                "raw_ref": None,
                "diagnostics": {
                    "truth_target_count": len(self._scenario.targets),
                    "detection_count": len(detections),
                },
            },
        )

    def _detect(
        self,
        target: RadarTargetScenario,
        elapsed_s: float,
    ) -> dict[str, float] | None:
        east_m, north_m, up_m = _position(target, elapsed_s)
        range_m = math.sqrt(east_m**2 + north_m**2 + up_m**2)
        if range_m == 0 or range_m > self._scenario.range_m:
            return None
        azimuth_deg = math.degrees(math.atan2(north_m, east_m))
        elevation_deg = math.degrees(math.asin(up_m / range_m))
        if abs(azimuth_deg) > self._scenario.azimuth_fov_deg / 2:
            return None
        if abs(elevation_deg) > self._scenario.elevation_fov_deg / 2:
            return None
        if self._dropout.random() > self._scenario.detection_probability:
            return None
        east_m_s, north_m_s, up_m_s = target.velocity_enu_m_s
        radial_velocity_m_s = (
            east_m * east_m_s + north_m * north_m_s + up_m * up_m_s
        ) / range_m
        return {
            "range_m": round(
                max(0.0, range_m + self._noise.gauss(0, self._scenario.range_noise_std_m)),
                6,
            ),
            "azimuth_deg": round(
                azimuth_deg + self._noise.gauss(0, self._scenario.angle_noise_std_deg),
                6,
            ),
            "elevation_deg": round(
                elevation_deg + self._noise.gauss(0, self._scenario.angle_noise_std_deg),
                6,
            ),
            "radial_velocity_m_s": round(
                radial_velocity_m_s
                + self._noise.gauss(0, self._scenario.velocity_noise_std_m_s),
                6,
            ),
            "snr_db": round(30.0 - 20.0 * math.log10(max(range_m, 1.0) / 10.0), 3),
        }
