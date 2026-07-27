"""Driver-compatible deterministic local weather observation model."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.simulator.clock import SimClock
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory, RandomStream


@dataclass(frozen=True, slots=True)
class WeatherTruth:
    wind_enu_m_s: tuple[float, float, float]
    gust_m_s: float
    temperature_deg_c: float
    relative_humidity_pct: float
    precipitation_mm_h: float
    visibility_m: float
    pressure_pa: float


class WeatherSensorModel:
    """Produce normalized weather observations without exposing truth fields."""

    def __init__(
        self,
        *,
        seed: int,
        sensor_id: str,
        frame_id: str,
        vehicle_id: str,
        run_id: UUID,
        clock: SimClock,
    ) -> None:
        self._sensor_id = sensor_id
        self._frame_id = frame_id
        self._vehicle_id = vehicle_id
        self._run_id = run_id
        self._clock = clock
        prefix = f"sensor/{sensor_id}"
        self._noise = RandomStream(seed, f"{prefix}/noise")
        self._event_ids = DeterministicUuid7Factory(
            RandomStream(seed, f"{prefix}/event-id")
        )
        self._session_id = DeterministicUuid7Factory(
            RandomStream(seed, f"{prefix}/session-id")
        ).new(clock.now())

    def sample(self, truth: WeatherTruth, *, sequence: int) -> Envelope:
        now = self._clock.now()
        event_id = self._event_ids.new(now)
        wind = [
            truth.wind_enu_m_s[index] + self._noise.gauss(0.0, 0.2)
            for index in range(3)
        ]
        return Envelope(
            schema="sensor/1.0",
            event_id=event_id,
            trace_id=event_id,
            source=Source(
                service="simulator-service",
                instance_id="weather-model",
                plugin="weather-sensor-sim",
                plugin_version="1.0.0",
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=self._run_id,
            mode=RuntimeMode.SIM,
            vehicle_id=self._vehicle_id,
            sequence=sequence,
            quality=Quality(
                valid=True,
                confidence=0.95,
                clock_uncertainty_ms=0.0,
            ),
            payload={
                "sensor_id": self._sensor_id,
                "sensor_type": "WEATHER",
                "source_session_id": str(self._session_id),
                "frame_id": self._frame_id,
                "calibration_id": "weather-sim-cal@1.0.0",
                "sample_format": "weather_observation_v1",
                "sample": {
                    "wind_enu_m_s": wind,
                    "gust_m_s": max(0.0, truth.gust_m_s + self._noise.gauss(0.0, 0.2)),
                    "temperature_deg_c": truth.temperature_deg_c
                    + self._noise.gauss(0.0, 0.15),
                    "relative_humidity_pct": min(
                        100.0,
                        max(
                            0.0,
                            truth.relative_humidity_pct + self._noise.gauss(0.0, 0.5),
                        ),
                    ),
                    "precipitation_mm_h": max(
                        0.0,
                        truth.precipitation_mm_h + self._noise.gauss(0.0, 0.05),
                    ),
                    "visibility_m": max(
                        0.0,
                        truth.visibility_m + self._noise.gauss(0.0, 10.0),
                    ),
                    "pressure_pa": max(
                        0.0,
                        truth.pressure_pa + self._noise.gauss(0.0, 5.0),
                    ),
                },
                "raw_ref": None,
                "diagnostics": {},
            },
        )
