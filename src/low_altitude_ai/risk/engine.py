"""Explainable rule-based Phase 4 risk engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from low_altitude_ai.digital_twin import TwinSnapshot
from low_altitude_ai.domain import Envelope, Quality, Source
from low_altitude_ai.ports.plugins import PluginContext, PluginManifest
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory

Vector3 = tuple[float, float, float]


class RiskNotInitializedError(RuntimeError):
    """The risk engine was used outside its initialized lifecycle."""


@dataclass(frozen=True, slots=True)
class VehicleKinematics:
    position_enu_m: Vector3
    velocity_enu_m_s: Vector3
    battery_pct: float
    control_link_healthy: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.battery_pct <= 100:
            raise ValueError("battery_pct must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class RiskEngineConfig:
    horizon_s: float = 15.0
    minimum_separation_m: float = 15.0
    reaction_time_s: float = 1.0
    stale_twin_ms: float = 500.0
    energy_reserve_pct: float = 20.0
    valid_for_s: float = 0.5
    moderate_threshold: float = 25.0
    high_threshold: float = 50.0
    critical_threshold: float = 75.0

    def __post_init__(self) -> None:
        positive = (
            self.horizon_s,
            self.minimum_separation_m,
            self.reaction_time_s,
            self.stale_twin_ms,
            self.energy_reserve_pct,
            self.valid_for_s,
        )
        if min(positive) <= 0:
            raise ValueError("risk thresholds must be positive")
        if not (
            0
            < self.moderate_threshold
            < self.high_threshold
            < self.critical_threshold
            <= 100
        ):
            raise ValueError("risk level thresholds are invalid")


def _vector_length(value: Vector3) -> float:
    return math.sqrt(sum(component**2 for component in value))


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return tuple(first[index] - second[index] for index in range(3))  # type: ignore[return-value]


def _add_scaled(position: Vector3, velocity: Vector3, seconds: float) -> Vector3:
    return tuple(
        round(position[index] + velocity[index] * seconds, 6) for index in range(3)
    )  # type: ignore[return-value]


def _parse_wire_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a date-time string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


class RiskEnginePlugin:
    def __init__(
        self,
        *,
        config: RiskEngineConfig,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        self._config = config
        self._event_ids = event_ids
        self._context: PluginContext | None = None
        self._sequence = 0

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="explainable_rule_risk",
            version="1.0.0",
            kind="RISK",
            api_version="1.0",
            input_schema="twin.snapshot/1.0,target/1.0,environment/1.0",
            output_schema="risk/1.0",
            capabilities=("CPA_TCPA", "WEATHER", "ENERGY", "LINK", "HARD_RULES"),
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
                service="risk-service",
                instance_id="rule-risk",
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

    async def assess(
        self,
        *,
        snapshot: TwinSnapshot,
        tracks: Envelope,
        environment: Envelope,
        vehicle: VehicleKinematics,
    ) -> Envelope:
        context = self._require_context()
        if tracks.schema != "target/1.0" or environment.schema != "environment/1.0":
            raise ValueError("risk inputs must be target/1.0 and environment/1.0")
        now = context.clock.now()
        explanations: list[dict[str, Any]] = []
        constraints: list[dict[str, Any]] = []
        collision_score = self._collision_risk(
            tracks,
            vehicle,
            explanations,
            constraints,
        )
        weather_score = self._weather_risk(environment, now, explanations)
        energy_score = self._energy_risk(vehicle, explanations)
        communication_score = 0.0 if vehicle.control_link_healthy else 80.0
        if not vehicle.control_link_healthy:
            explanations.append(
                {
                    "code": "CONTROL_LINK_STALE",
                    "severity": "CRITICAL",
                    "summary": "控制链路不可用, 禁止按标称状态继续。",
                    "evidence": {"control_link_healthy": False},
                }
            )
        twin_age_ms = max(0.0, (now - snapshot.watermark).total_seconds() * 1_000)
        system_score = 0.0
        if twin_age_ms > self._config.stale_twin_ms or not snapshot.quality.valid:
            system_score = 55.0
            explanations.append(
                {
                    "code": "TWIN_STALE_OR_INVALID",
                    "severity": "HIGH",
                    "summary": "数字孪生状态过期或无效, 风险按保守下限处理。",
                    "evidence": {
                        "twin_age_ms": round(twin_age_ms, 3),
                        "threshold_ms": self._config.stale_twin_ms,
                        "valid": snapshot.quality.valid,
                    },
                }
            )
        dimensions = {
            "weather": round(weather_score, 3),
            "collision": round(collision_score, 3),
            "energy": round(energy_score, 3),
            "communication": round(communication_score, 3),
            "system": round(system_score, 3),
        }
        score = max(dimensions.values())
        level = self._level(score)
        risk_id = self._event_ids.new(now)
        event_id = self._event_ids.new(now)
        output = Envelope(
            schema="risk/1.0",
            event_id=event_id,
            trace_id=tracks.trace_id,
            causation_id=tracks.event_id,
            source=Source(
                service="risk-service",
                instance_id="rule-risk",
                plugin=self.manifest.name,
                plugin_version=self.manifest.version,
            ),
            observed_at=max(tracks.observed_at, environment.observed_at),
            received_at=now,
            monotonic_ns=context.clock.monotonic_ns(),
            run_id=tracks.run_id,
            mode=tracks.mode,
            vehicle_id=tracks.vehicle_id,
            sequence=self._sequence,
            quality=Quality(
                valid=tracks.quality.valid and environment.quality.valid and snapshot.quality.valid,
                confidence=min(
                    tracks.quality.confidence,
                    environment.quality.confidence,
                    snapshot.quality.confidence,
                ),
                flags=tuple(
                    sorted(
                        {
                            *tracks.quality.flags,
                            *environment.quality.flags,
                            *snapshot.quality.flags,
                        }
                    )
                ),
            ),
            payload={
                "risk_id": str(risk_id),
                "subject": {"type": "VEHICLE", "id": tracks.vehicle_id or "unknown"},
                "twin_revision": snapshot.revision,
                "horizon_s": self._config.horizon_s,
                "score": score,
                "level": level,
                "dimensions": dimensions,
                "explanations": explanations,
                "valid_until": (now + timedelta(seconds=self._config.valid_for_s))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "recommended_constraints": constraints,
            },
        )
        self._sequence += 1
        return output

    def _collision_risk(
        self,
        tracks: Envelope,
        vehicle: VehicleKinematics,
        explanations: list[dict[str, Any]],
        constraints: list[dict[str, Any]],
    ) -> float:
        targets = tracks.payload.get("targets")
        if not isinstance(targets, list):
            raise ValueError("target payload must contain targets")
        worst_score = 0.0
        worst: dict[str, Any] | None = None
        for target in targets:
            if not isinstance(target, dict) or target.get("state") == "LOST":
                continue
            position_value = target.get("position")
            velocity_value = target.get("velocity")
            if not isinstance(position_value, dict) or not isinstance(velocity_value, dict):
                continue
            enu = position_value.get("enu")
            if not isinstance(enu, dict):
                continue
            target_position = (
                float(enu["east_m"]),
                float(enu["north_m"]),
                float(enu["up_m"]),
            )
            target_velocity = (
                float(velocity_value["east_m_s"]),
                float(velocity_value["north_m_s"]),
                float(velocity_value["up_m_s"]),
            )
            relative_position = _subtract(target_position, vehicle.position_enu_m)
            relative_velocity = _subtract(target_velocity, vehicle.velocity_enu_m_s)
            speed_squared = sum(value**2 for value in relative_velocity)
            tcpa_s = (
                max(
                    0.0,
                    min(
                        self._config.horizon_s,
                        -sum(
                            relative_position[index] * relative_velocity[index]
                            for index in range(3)
                        )
                        / speed_squared,
                    ),
                )
                if speed_squared > 1e-9
                else 0.0
            )
            closest = _add_scaled(relative_position, relative_velocity, tcpa_s)
            dcpa_m = _vector_length(closest)
            covariance = position_value.get("covariance")
            covariance_margin = 0.0
            if isinstance(covariance, list) and len(covariance) == 9:
                covariance_margin = math.sqrt(
                    max(float(covariance[0]), float(covariance[4]), float(covariance[8]))
                )
            relative_speed = math.sqrt(speed_squared)
            protection_m = (
                self._config.minimum_separation_m
                + relative_speed * self._config.reaction_time_s
                + covariance_margin
            )
            clearance_factor = max(0.0, 1.0 - dcpa_m / protection_m)
            time_factor = max(0.0, 1.0 - tcpa_s / self._config.horizon_s)
            score = 100.0 * (0.7 * clearance_factor + 0.3 * time_factor)
            if dcpa_m < protection_m:
                score = max(score, self._config.critical_threshold)
            if score > worst_score:
                track_id = target.get("track_id")
                predicted_target = _add_scaled(target_position, target_velocity, tcpa_s)
                worst_score = min(100.0, score)
                worst = {
                    "track_id": track_id,
                    "tcpa_s": round(tcpa_s, 3),
                    "dcpa_m": round(dcpa_m, 3),
                    "protection_m": round(protection_m, 3),
                    "center_enu_m": list(predicted_target),
                }
        if worst is not None and worst_score >= self._config.moderate_threshold:
            severity = self._level(worst_score)
            explanations.append(
                {
                    "code": "CLOSING_TRACK",
                    "severity": severity,
                    "summary": "动态目标在预测窗口内接近保护区。",
                    "evidence": worst,
                }
            )
            constraints.append(
                {
                    "type": "EXCLUSION_CYLINDER",
                    "center_enu_m": worst["center_enu_m"],
                    "radius_m": worst["protection_m"],
                    "valid_for_s": self._config.valid_for_s,
                }
            )
        return worst_score

    def _weather_risk(
        self,
        environment: Envelope,
        now: datetime,
        explanations: list[dict[str, Any]],
    ) -> float:
        factors = environment.payload.get("risk_factors")
        if not isinstance(factors, dict):
            raise ValueError("environment risk_factors must be an object")
        valid_to = _parse_wire_datetime(environment.payload.get("valid_to"), "valid_to")
        unknown = not environment.quality.valid or valid_to <= now
        score = max(float(value) for value in factors.values()) * 100.0
        if unknown:
            score = max(score, self._config.high_threshold + 5.0)
            explanations.append(
                {
                    "code": "ENVIRONMENT_UNKNOWN",
                    "severity": "HIGH",
                    "summary": "天气数据无效或过期, 按保守风险下限处理。",
                    "evidence": {
                        "valid": environment.quality.valid,
                        "valid_to": environment.payload.get("valid_to"),
                    },
                }
            )
        elif score >= self._config.moderate_threshold:
            explanations.append(
                {
                    "code": "WEATHER_FACTOR_ELEVATED",
                    "severity": self._level(score),
                    "summary": "环境风险因子达到需要关注的水平。",
                    "evidence": {"risk_factors": factors},
                }
            )
        return min(100.0, score)

    def _energy_risk(
        self,
        vehicle: VehicleKinematics,
        explanations: list[dict[str, Any]],
    ) -> float:
        if vehicle.battery_pct < self._config.energy_reserve_pct:
            explanations.append(
                {
                    "code": "ENERGY_RESERVE_LOW",
                    "severity": "CRITICAL",
                    "summary": "剩余能源低于配置的安全储备。",
                    "evidence": {
                        "battery_pct": vehicle.battery_pct,
                        "reserve_pct": self._config.energy_reserve_pct,
                    },
                }
            )
            return 80.0
        return max(
            0.0,
            (self._config.energy_reserve_pct + 20.0 - vehicle.battery_pct) * 2.0,
        )

    def _level(self, score: float) -> str:
        if score >= self._config.critical_threshold:
            return "CRITICAL"
        if score >= self._config.high_threshold:
            return "HIGH"
        if score >= self._config.moderate_threshold:
            return "MODERATE"
        return "LOW"

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise RiskNotInitializedError("risk engine is not initialized")
        return self._context
