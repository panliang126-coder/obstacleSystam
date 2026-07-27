"""Deterministic local weather fusion baseline with conservative unknown handling."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from low_altitude_ai.domain import Envelope, Quality, Source
from low_altitude_ai.ports.plugins import PluginContext, PluginManifest
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory

Vector3 = tuple[float, float, float]


class WeatherNotInitializedError(RuntimeError):
    """The weather plugin was used before initialization or after shutdown."""


@dataclass(frozen=True, slots=True)
class WeatherEstimatorConfig:
    frame_id: str
    coverage_min_enu_m: Vector3
    coverage_max_enu_m: Vector3
    freshness_s: float = 5.0
    valid_for_s: float = 10.0
    max_wind_m_s: float = 60.0
    heavy_precipitation_mm_h: float = 20.0
    good_visibility_m: float = 5_000.0

    def __post_init__(self) -> None:
        if not self.frame_id.strip():
            raise ValueError("weather frame_id must be non-empty")
        if any(
            self.coverage_min_enu_m[index] >= self.coverage_max_enu_m[index]
            for index in range(3)
        ):
            raise ValueError("weather coverage bounds are invalid")
        if min(
            self.freshness_s,
            self.valid_for_s,
            self.max_wind_m_s,
            self.heavy_precipitation_mm_h,
            self.good_visibility_m,
        ) <= 0:
            raise ValueError("weather thresholds must be positive")


@dataclass(frozen=True, slots=True)
class _Observation:
    event: Envelope
    sensor_id: str
    wind: Vector3
    gust_m_s: float
    temperature_deg_c: float
    relative_humidity_pct: float
    precipitation_mm_h: float
    visibility_m: float
    pressure_pa: float
    weight: float


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


class WeatherEstimatorPlugin:
    def __init__(
        self,
        *,
        config: WeatherEstimatorConfig,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        self._config = config
        self._event_ids = event_ids
        self._context: PluginContext | None = None
        self._sequence = 0

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="local_weather_weighted_fusion",
            version="1.0.0",
            kind="WEATHER",
            api_version="1.0",
            input_schema="sensor/1.0",
            output_schema="environment/1.0",
            capabilities=("LOCAL_WEATHER", "QC", "CONSERVATIVE_UNKNOWN", "DETERMINISTIC"),
        )

    async def initialize(self, context: PluginContext) -> None:
        self._context = context

    async def shutdown(self, deadline_s: float) -> None:
        if deadline_s < 0:
            raise ValueError("deadline_s cannot be negative")
        self._context = None

    async def health(self) -> Envelope:
        context = self._require_context()
        now = context.clock.now()
        event_id = self._event_ids.new(now)
        return Envelope(
            schema="health/1.0",
            event_id=event_id,
            trace_id=event_id,
            source=Source(
                service="weather-service",
                instance_id="local-weather",
                plugin=self.manifest.name,
                plugin_version=self.manifest.version,
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=context.clock.monotonic_ns(),
            run_id=UUID(context.run_id),
            mode=context.mode,
            sequence=self._sequence,
            quality=Quality(valid=True, confidence=1.0),
            payload={
                "component_id": self.manifest.name,
                "component_type": "PLUGIN",
                "status": "HEALTHY",
                "checked_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "dependencies": [],
                "data_freshness_ms": None,
                "faults": [],
            },
        )

    async def estimate(self, events: Sequence[Envelope]) -> Envelope:
        context = self._require_context()
        now = context.clock.now()
        flags: set[str] = set()
        if list(events) != sorted(events, key=lambda event: event.observed_at):
            flags.add("OUT_OF_ORDER_INPUT")
        observations: list[_Observation] = []
        for event in events:
            try:
                observation = self._parse(event, now)
            except ValueError:
                flags.add("INVALID_OBSERVATION")
                continue
            if observation is None:
                flags.add("STALE_OBSERVATION")
                continue
            observations.append(observation)

        if observations:
            payload, confidence = self._fuse(observations, now)
            trace_id = observations[-1].event.trace_id
            causation_id = observations[-1].event.event_id
            run_id = observations[-1].event.run_id
            mode = observations[-1].event.mode
            vehicle_id = observations[-1].event.vehicle_id
            observed_at = max(item.event.observed_at for item in observations)
            valid = "OUT_OF_ORDER_INPUT" not in flags
        else:
            flags.add("WEATHER_UNAVAILABLE")
            payload = self._unknown_payload(now)
            confidence = 0.0
            event_id_for_context = self._event_ids.new(now)
            trace_id = event_id_for_context
            causation_id = None
            run_id = UUID(context.run_id)
            mode = context.mode
            vehicle_id = None
            observed_at = now
            valid = False

        event_id = self._event_ids.new(now)
        output = Envelope(
            schema="environment/1.0",
            event_id=event_id,
            trace_id=trace_id,
            causation_id=causation_id,
            source=Source(
                service="weather-service",
                instance_id="local-weather",
                plugin=self.manifest.name,
                plugin_version=self.manifest.version,
            ),
            observed_at=observed_at,
            received_at=now,
            monotonic_ns=context.clock.monotonic_ns(),
            run_id=run_id,
            mode=mode,
            vehicle_id=vehicle_id,
            sequence=self._sequence,
            quality=Quality(
                valid=valid,
                confidence=confidence,
                flags=tuple(sorted(flags)),
            ),
            payload=payload,
        )
        self._sequence += 1
        return output

    def _parse(self, event: Envelope, now: datetime) -> _Observation | None:
        if event.schema != "sensor/1.0" or event.payload.get("sensor_type") != "WEATHER":
            raise ValueError("weather estimator requires WEATHER sensor/1.0 events")
        age_s = (now - event.observed_at).total_seconds()
        if age_s > self._config.freshness_s:
            return None
        if age_s < 0:
            raise ValueError("weather observation is from the future")
        sample = event.payload.get("sample")
        if not isinstance(sample, Mapping):
            raise ValueError("weather sample must be an object")
        wind_value = sample.get("wind_enu_m_s")
        if not isinstance(wind_value, list) or len(wind_value) != 3:
            raise ValueError("wind_enu_m_s must be a vector3")
        wind = tuple(float(component) for component in wind_value)
        gust = float(sample["gust_m_s"])
        temperature = float(sample["temperature_deg_c"])
        humidity = float(sample["relative_humidity_pct"])
        precipitation = float(sample["precipitation_mm_h"])
        visibility = float(sample["visibility_m"])
        pressure = float(sample["pressure_pa"])
        if (
            any(not math.isfinite(value) for value in (*wind, gust, temperature, humidity))
            or not 0 <= humidity <= 100
            or min(gust, precipitation, visibility, pressure) < 0
            or math.sqrt(sum(value**2 for value in wind)) > self._config.max_wind_m_s
        ):
            raise ValueError("weather observation failed physical QC")
        sensor_id = event.payload.get("sensor_id")
        if not isinstance(sensor_id, str) or not sensor_id.strip():
            raise ValueError("weather sensor_id is invalid")
        weight = event.quality.confidence if event.quality.valid else 0.0
        if weight <= 0:
            raise ValueError("weather observation quality is invalid")
        return _Observation(
            event=event,
            sensor_id=sensor_id,
            wind=wind,  # type: ignore[arg-type]
            gust_m_s=gust,
            temperature_deg_c=temperature,
            relative_humidity_pct=humidity,
            precipitation_mm_h=precipitation,
            visibility_m=visibility,
            pressure_pa=pressure,
            weight=weight,
        )

    def _fuse(
        self,
        observations: Sequence[_Observation],
        now: datetime,
    ) -> tuple[dict[str, Any], float]:
        total_weight = sum(item.weight for item in observations)

        def weighted(values: Sequence[float]) -> float:
            return sum(
                values[index] * observations[index].weight
                for index in range(len(observations))
            ) / total_weight

        wind = tuple(
            weighted([item.wind[index] for item in observations]) for index in range(3)
        )
        gust = max(item.gust_m_s for item in observations)
        temperature = weighted([item.temperature_deg_c for item in observations])
        humidity = weighted([item.relative_humidity_pct for item in observations])
        precipitation = max(item.precipitation_mm_h for item in observations)
        visibility = min(item.visibility_m for item in observations)
        pressure = weighted([item.pressure_pa for item in observations])
        residuals = [
            math.sqrt(sum((item.wind[index] - wind[index]) ** 2 for index in range(3)))
            for item in observations
        ]
        uncertainty = max(0.2, weighted(residuals))
        confidence = _clamp(weighted([item.event.quality.confidence for item in observations]))
        wind_speed = math.sqrt(sum(value**2 for value in wind))
        risk_factors = {
            "wind": _clamp(max(wind_speed, gust) / self._config.max_wind_m_s),
            "precipitation": _clamp(
                precipitation / self._config.heavy_precipitation_mm_h
            ),
            "visibility": _clamp(1.0 - visibility / self._config.good_visibility_m),
            "icing": 0.8 if -10 <= temperature <= 5 and humidity >= 80 else 0.0,
            "convective": _clamp(
                max(abs(wind[2]) / 5.0, max(0.0, gust - wind_speed) / 15.0)
            ),
            "uncertainty": _clamp(1.0 - confidence + uncertainty / self._config.max_wind_m_s),
        }
        return (
            {
                "environment_id": str(self._event_ids.new(now)),
                "valid_from": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "valid_to": (now + timedelta(seconds=self._config.valid_for_s))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "coverage": {
                    "type": "BOX3D",
                    "frame_id": self._config.frame_id,
                    "min": list(self._config.coverage_min_enu_m),
                    "max": list(self._config.coverage_max_enu_m),
                },
                "grid_ref": None,
                "wind": {
                    "east_m_s": wind[0],
                    "north_m_s": wind[1],
                    "up_m_s": wind[2],
                    "gust_m_s": gust,
                    "uncertainty_m_s": uncertainty,
                },
                "temperature_deg_c": temperature,
                "relative_humidity_pct": humidity,
                "precipitation_mm_h": precipitation,
                "visibility_m": visibility,
                "pressure_pa": pressure,
                "risk_factors": risk_factors,
                "provenance": [
                    {"source_id": item.sensor_id, "weight": item.weight / total_weight}
                    for item in observations
                ],
            },
            confidence,
        )

    def _unknown_payload(self, now: datetime) -> dict[str, Any]:
        return {
            "environment_id": str(self._event_ids.new(now)),
            "valid_from": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "valid_to": (now + timedelta(seconds=self._config.valid_for_s))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "coverage": {
                "type": "BOX3D",
                "frame_id": self._config.frame_id,
                "min": list(self._config.coverage_min_enu_m),
                "max": list(self._config.coverage_max_enu_m),
            },
            "grid_ref": None,
            "wind": {
                "east_m_s": 0.0,
                "north_m_s": 0.0,
                "up_m_s": 0.0,
                "gust_m_s": 0.0,
                "uncertainty_m_s": self._config.max_wind_m_s,
            },
            "temperature_deg_c": None,
            "relative_humidity_pct": None,
            "precipitation_mm_h": None,
            "visibility_m": None,
            "pressure_pa": None,
            "risk_factors": {
                "wind": 0.0,
                "precipitation": 0.0,
                "visibility": 0.0,
                "icing": 0.0,
                "convective": 0.0,
                "uncertainty": 1.0,
            },
            "provenance": [{"source_id": "weather-unavailable", "weight": 0.0}],
        }

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise WeatherNotInitializedError("weather estimator is not initialized")
        return self._context
