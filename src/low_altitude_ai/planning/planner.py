"""Validated deterministic local planner for exclusion-cylinder constraints."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from low_altitude_ai.domain import Envelope, Quality, Source
from low_altitude_ai.ports.plugins import PluginContext, PluginManifest
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory

Vector3 = tuple[float, float, float]


class PlannerNotInitializedError(RuntimeError):
    """The planner was called before initialization or after shutdown."""


@dataclass(frozen=True, slots=True)
class PlanRequest:
    mission_id: str
    vehicle_id: str
    twin_revision: int
    start_enu_m: Vector3
    goal_enu_m: Vector3
    frame_id: str
    risk: Envelope
    deadline: datetime


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    cruise_speed_m_s: float = 6.0
    minimum_clearance_m: float = 10.0
    valid_for_s: float = 0.5
    geofence_min_enu_m: Vector3 = (-500.0, -500.0, 0.0)
    geofence_max_enu_m: Vector3 = (500.0, 500.0, 300.0)

    def __post_init__(self) -> None:
        if min(self.cruise_speed_m_s, self.minimum_clearance_m, self.valid_for_s) <= 0:
            raise ValueError("planner thresholds must be positive")


def _distance(first: Vector3, second: Vector3) -> float:
    return math.sqrt(sum((first[index] - second[index]) ** 2 for index in range(3)))


def _segment_distance(point: Vector3, start: Vector3, end: Vector3) -> float:
    segment = tuple(end[index] - start[index] for index in range(3))
    length_squared = sum(value**2 for value in segment)
    if length_squared == 0:
        return _distance(point, start)
    projection = sum(
        (point[index] - start[index]) * segment[index] for index in range(3)
    ) / length_squared
    projection = min(1.0, max(0.0, projection))
    closest = tuple(start[index] + projection * segment[index] for index in range(3))
    return _distance(point, closest)  # type: ignore[arg-type]


class RuleBasedPlannerPlugin:
    def __init__(
        self,
        *,
        config: PlannerConfig,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        self._config = config
        self._event_ids = event_ids
        self._context: PluginContext | None = None
        self._sequence = 0

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="validated_detour_planner",
            version="1.0.0",
            kind="PLANNING",
            api_version="1.0",
            input_schema="risk/1.0",
            output_schema="path/1.0",
            capabilities=("GLOBAL", "LOCAL", "3D", "DETERMINISTIC", "VALIDATOR"),
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
                service="planning-service",
                instance_id="detour-planner",
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

    async def plan(self, request: PlanRequest) -> Envelope:
        context = self._require_context()
        now = context.clock.now()
        if request.risk.schema != "risk/1.0":
            raise ValueError("planner requires risk/1.0")
        if request.deadline <= now:
            raise ValueError("planning request deadline has expired")
        risk_valid_until = datetime.fromisoformat(
            str(request.risk.payload["valid_until"]).replace("Z", "+00:00")
        )
        if risk_valid_until <= now:
            raise ValueError("risk input has expired")
        if int(request.risk.payload["twin_revision"]) != request.twin_revision:
            raise ValueError("risk and Twin revisions do not match")
        constraints = self._constraints(request.risk)
        candidates = [
            [request.start_enu_m, request.goal_enu_m],
            *self._detours(request.start_enu_m, request.goal_enu_m, constraints),
        ]
        feasible = [
            candidate
            for candidate in candidates
            if self._in_geofence(candidate) and self._collision_free(candidate, constraints)
        ]
        if feasible:
            points = min(feasible, key=self._path_length)
            status = "CANDIDATE"
            collision_free = True
            geofence_valid = True
            min_clearance = self._minimum_clearance(points, constraints)
        else:
            points = [request.start_enu_m, request.goal_enu_m]
            status = "REJECTED"
            collision_free = False
            geofence_valid = self._in_geofence(points)
            min_clearance = self._minimum_clearance(points, constraints)
        path_id = self._event_ids.new(now)
        event_id = self._event_ids.new(now)
        waypoints: list[dict[str, Any]] = []
        elapsed_s = 0.0
        for index, point in enumerate(points):
            if index:
                elapsed_s += _distance(points[index - 1], point) / self._config.cruise_speed_m_s
            waypoints.append(
                {
                    "seq": index,
                    "enu_m": list(point),
                    "target_speed_m_s": self._config.cruise_speed_m_s,
                    "eta_s": round(elapsed_s, 3),
                }
            )
        distance_m = self._path_length(points)
        risk_id = str(request.risk.payload["risk_id"])
        output = Envelope(
            schema="path/1.0",
            event_id=event_id,
            trace_id=request.risk.trace_id,
            causation_id=request.risk.event_id,
            source=Source(
                service="planning-service",
                instance_id="detour-planner",
                plugin=self.manifest.name,
                plugin_version=self.manifest.version,
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=context.clock.monotonic_ns(),
            run_id=request.risk.run_id,
            mode=request.risk.mode,
            vehicle_id=request.vehicle_id,
            sequence=self._sequence,
            quality=Quality(
                valid=bool(feasible),
                confidence=request.risk.quality.confidence if feasible else 0.0,
                flags=() if feasible else ("NO_FEASIBLE_PATH",),
            ),
            payload={
                "path_id": str(path_id),
                "mission_id": request.mission_id,
                "planner": {
                    "name": self.manifest.name,
                    "version": self.manifest.version,
                    "algorithm": "DETERMINISTIC_DETOUR",
                },
                "twin_revision": request.twin_revision,
                "risk_id": risk_id,
                "frame_id": request.frame_id,
                "waypoints": waypoints,
                "costs": {
                    "distance": round(distance_m, 3),
                    "time": round(elapsed_s, 3),
                    "energy": round(distance_m / 1_000, 3),
                    "risk": float(request.risk.payload["score"]),
                    "total": round(
                        distance_m / 100
                        + elapsed_s / 10
                        + float(request.risk.payload["score"]) / 25,
                        3,
                    ),
                },
                "constraints_applied": [
                    f"risk:{risk_id}",
                    f"twin-revision:{request.twin_revision}",
                    "geofence:phase4-baseline",
                ],
                "validation": {
                    "collision_free": collision_free,
                    "dynamics_feasible": bool(feasible),
                    "geofence_valid": geofence_valid,
                    "minimum_clearance_m": round(min_clearance, 3),
                },
                "valid_until": min(
                    risk_valid_until,
                    now + timedelta(seconds=self._config.valid_for_s),
                )
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "status": status,
            },
        )
        self._sequence += 1
        return output

    @staticmethod
    def _constraints(risk: Envelope) -> tuple[tuple[Vector3, float], ...]:
        values = risk.payload.get("recommended_constraints")
        if not isinstance(values, list):
            raise ValueError("risk constraints must be an array")
        result: list[tuple[Vector3, float]] = []
        for value in values:
            if not isinstance(value, dict) or value.get("type") != "EXCLUSION_CYLINDER":
                continue
            center_value = value.get("center_enu_m")
            if not isinstance(center_value, list) or len(center_value) != 3:
                raise ValueError("exclusion center must be a vector3")
            center = tuple(float(component) for component in center_value)
            result.append((center, float(value["radius_m"])))  # type: ignore[arg-type]
        return tuple(result)

    def _detours(
        self,
        start: Vector3,
        goal: Vector3,
        constraints: tuple[tuple[Vector3, float], ...],
    ) -> list[list[Vector3]]:
        results: list[list[Vector3]] = []
        for center, radius in constraints:
            offset = radius + self._config.minimum_clearance_m
            for sign in (-1.0, 1.0):
                y = center[1] + sign * offset
                results.append(
                    [
                        start,
                        (start[0], y, start[2]),
                        (goal[0], y, goal[2]),
                        goal,
                    ]
                )
        return results

    def _collision_free(
        self,
        points: list[Vector3],
        constraints: tuple[tuple[Vector3, float], ...],
    ) -> bool:
        return all(
            _segment_distance(center, points[index - 1], points[index])
            >= radius + self._config.minimum_clearance_m
            for index in range(1, len(points))
            for center, radius in constraints
        )

    def _minimum_clearance(
        self,
        points: list[Vector3],
        constraints: tuple[tuple[Vector3, float], ...],
    ) -> float:
        if not constraints:
            return 1_000_000.0
        return min(
            _segment_distance(center, points[index - 1], points[index]) - radius
            for index in range(1, len(points))
            for center, radius in constraints
        )

    def _in_geofence(self, points: list[Vector3]) -> bool:
        return all(
            all(
                self._config.geofence_min_enu_m[index]
                <= point[index]
                <= self._config.geofence_max_enu_m[index]
                for index in range(3)
            )
            for point in points
        )

    @staticmethod
    def _path_length(points: list[Vector3]) -> float:
        return sum(_distance(points[index - 1], points[index]) for index in range(1, len(points)))

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise PlannerNotInitializedError("planner is not initialized")
        return self._context
