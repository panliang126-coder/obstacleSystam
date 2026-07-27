"""Health aggregation with explicit UNKNOWN semantics for missed heartbeats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from low_altitude_ai.domain import Envelope


def _parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class ComponentHealthView:
    component_id: str
    status: str
    age_ms: float
    dependencies: tuple[str, ...]
    faults: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: str
    generated_at: datetime
    components: tuple[ComponentHealthView, ...]


class HealthAggregator:
    def __init__(
        self,
        *,
        heartbeat_timeout_s: float = 2.0,
        critical_components: tuple[str, ...] = (
            "risk-service",
            "safety-gate",
            "control-gateway",
        ),
    ) -> None:
        if heartbeat_timeout_s <= 0:
            raise ValueError("heartbeat_timeout_s must be positive")
        self._timeout_s = heartbeat_timeout_s
        self._critical = frozenset(critical_components)
        self._events: dict[str, Envelope] = {}

    def ingest(self, event: Envelope) -> None:
        if event.schema != "health/1.0":
            raise ValueError("health aggregator requires health/1.0")
        component_id = str(event.payload["component_id"])
        previous = self._events.get(component_id)
        if previous is None or event.sequence >= previous.sequence:
            self._events[component_id] = event

    def snapshot(self, now: datetime) -> HealthSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("snapshot time must be timezone-aware")
        views: list[ComponentHealthView] = []
        for component_id, event in sorted(self._events.items()):
            checked_at = _parse_time(event.payload["checked_at"])
            age_ms = max(0.0, (now - checked_at).total_seconds() * 1_000)
            status = str(event.payload["status"])
            if age_ms > self._timeout_s * 1_000:
                status = "UNKNOWN"
            dependencies = tuple(
                str(value.get("component_id", "unknown"))
                for value in event.payload.get("dependencies", [])
                if isinstance(value, dict)
            )
            faults = tuple(
                str(value.get("code", "UNKNOWN_FAULT"))
                for value in event.payload.get("faults", [])
                if isinstance(value, dict)
            )
            views.append(
                ComponentHealthView(
                    component_id=component_id,
                    status=status,
                    age_ms=round(age_ms, 3),
                    dependencies=dependencies,
                    faults=faults,
                )
            )
        status = self._system_status(views)
        return HealthSnapshot(status=status, generated_at=now, components=tuple(views))

    def _system_status(self, views: list[ComponentHealthView]) -> str:
        if not views:
            return "UNKNOWN"
        by_id = {view.component_id: view for view in views}
        if any(
            by_id[component].status in {"UNKNOWN", "UNHEALTHY"}
            for component in self._critical
            if component in by_id
        ):
            return "UNHEALTHY"
        statuses = {view.status for view in views}
        if "UNHEALTHY" in statuses:
            return "UNHEALTHY"
        if "UNKNOWN" in statuses:
            return "UNKNOWN"
        if "DEGRADED" in statuses:
            return "DEGRADED"
        if statuses == {"HEALTHY"}:
            return "HEALTHY"
        return "UNKNOWN"
