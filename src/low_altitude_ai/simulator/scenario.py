"""Validated Phase 2 radar scenario configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from low_altitude_ai.compat import UTC

Vector3 = tuple[float, float, float]


class ScenarioValidationError(ValueError):
    """A scenario cannot be loaded safely."""


def _vector3(value: object, field_name: str) -> Vector3:
    if not isinstance(value, list) or len(value) != 3:
        raise ScenarioValidationError(f"{field_name} must contain three numbers")
    if any(not isinstance(component, (int, float)) for component in value):
        raise ScenarioValidationError(f"{field_name} must contain only numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


@dataclass(frozen=True, slots=True)
class RadarTargetScenario:
    target_id: str
    classification: str
    initial_enu_m: Vector3
    velocity_enu_m_s: Vector3


@dataclass(frozen=True, slots=True)
class RadarScenario:
    scenario_id: str
    duration_s: float
    seed: int
    start_at: datetime
    frame_id: str
    vehicle_id: str
    sensor_id: str
    calibration_id: str
    rate_hz: float
    range_m: float
    azimuth_fov_deg: float
    elevation_fov_deg: float
    detection_probability: float
    range_noise_std_m: float
    angle_noise_std_deg: float
    velocity_noise_std_m_s: float
    targets: tuple[RadarTargetScenario, ...]

    @property
    def sample_count(self) -> int:
        return max(1, round(self.duration_s * self.rate_hz))

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RadarScenario:
        scenario = value["scenario"]
        if not isinstance(scenario, dict):
            raise ScenarioValidationError("scenario must be an object")
        frame = scenario["frame"]
        radar = scenario["radar"]
        target_values = scenario["targets"]
        if not isinstance(frame, dict) or not isinstance(radar, dict):
            raise ScenarioValidationError("frame and radar must be objects")
        if not isinstance(target_values, list):
            raise ScenarioValidationError("targets must be an array")
        start_at = datetime.fromisoformat(str(scenario["start_at"]).replace("Z", "+00:00"))
        if start_at.tzinfo is None:
            raise ScenarioValidationError("start_at must include a timezone")
        targets = tuple(
            RadarTargetScenario(
                target_id=str(target["id"]),
                classification=str(target["classification"]),
                initial_enu_m=_vector3(target["initial_enu_m"], "initial_enu_m"),
                velocity_enu_m_s=_vector3(
                    target["velocity_enu_m_s"],
                    "velocity_enu_m_s",
                ),
            )
            for target in target_values
            if isinstance(target, dict)
        )
        if len(targets) != len(target_values):
            raise ScenarioValidationError("every target must be an object")
        target_ids = [target.target_id for target in targets]
        if len(set(target_ids)) != len(target_ids):
            raise ScenarioValidationError("target IDs must be unique")
        return cls(
            scenario_id=str(scenario["id"]),
            duration_s=float(scenario["duration_s"]),
            seed=int(scenario["seed"]),
            start_at=start_at.astimezone(UTC),
            frame_id=str(frame["id"]),
            vehicle_id=str(scenario["vehicle_id"]),
            sensor_id=str(radar["id"]),
            calibration_id=str(radar["calibration_id"]),
            rate_hz=float(radar["rate_hz"]),
            range_m=float(radar["range_m"]),
            azimuth_fov_deg=float(radar["azimuth_fov_deg"]),
            elevation_fov_deg=float(radar["elevation_fov_deg"]),
            detection_probability=float(radar["detection_probability"]),
            range_noise_std_m=float(radar["range_noise_std_m"]),
            angle_noise_std_deg=float(radar["angle_noise_std_deg"]),
            velocity_noise_std_m_s=float(radar["velocity_noise_std_m_s"]),
            targets=targets,
        )


def load_radar_scenario(path: Path, schema_path: Path) -> RadarScenario:
    """Validate and load a JSON scenario without applying unsafe defaults."""

    with schema_path.open("r", encoding="utf-8") as stream:
        schema = json.load(stream)
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(schema, dict) or not isinstance(value, dict):
        raise ScenarioValidationError("scenario and schema roots must be objects")
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ScenarioValidationError(f"scenario at {location}: {first.message}") from first
    return RadarScenario.from_mapping(value)
