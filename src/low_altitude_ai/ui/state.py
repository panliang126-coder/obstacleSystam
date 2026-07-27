"""Bounded immutable UI state projection with freshness and gap tracking."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from low_altitude_ai.domain import Envelope, RuntimeMode


class ConnectionState(str, Enum):  # noqa: UP042 - Python 3.10 host compatibility
    CONNECTING = "CONNECTING"
    SYNCING = "SYNCING"
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True, slots=True)
class EntityView:
    key: str
    schema: str
    status: str
    updated_at: datetime
    age_ms: float
    payload: Mapping[str, Any]
    critical: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class UiSnapshot:
    connection: ConnectionState
    mode: RuntimeMode
    complete: bool
    generated_at: datetime
    entities: tuple[EntityView, ...]
    highest_risk: str
    commands_enabled: bool
    cursor: int
    invalid_messages: int
    archived_critical: int


@dataclass(frozen=True, slots=True)
class _StoredEntity:
    key: str
    schema: str
    status: str
    updated_at: datetime
    payload: Mapping[str, Any]
    critical: bool


_CRITICAL_SCHEMAS = {
    "control.ack/1.0",
    "decision/1.0",
    "risk/1.0",
}
_RISK_RANK = {
    "UNKNOWN": 5,
    "CRITICAL": 4,
    "HIGH": 3,
    "MODERATE": 2,
    "LOW": 1,
    "NONE": 0,
}


class UiStateStore:
    def __init__(
        self,
        *,
        mode: RuntimeMode,
        max_entities: int = 5_000,
        max_critical_entities: int = 1_000,
    ) -> None:
        if min(max_entities, max_critical_entities) <= 0:
            raise ValueError("UI entity capacities must be positive")
        self._mode = mode
        self._max_entities = max_entities
        self._max_critical_entities = max_critical_entities
        self._connection = ConnectionState.DISCONNECTED
        self._complete = False
        self._cursor = 0
        self._invalid_messages = 0
        self._archived_critical = 0
        self._entities: OrderedDict[tuple[str, str], _StoredEntity] = OrderedDict()
        self._sequences: dict[tuple[str, str, str, str | None], int] = {}

    @property
    def mode(self) -> RuntimeMode:
        return self._mode

    @property
    def connection(self) -> ConnectionState:
        return self._connection

    @property
    def commands_enabled(self) -> bool:
        return self._connection == ConnectionState.LIVE and self._mode != RuntimeMode.REPLAY

    def begin_connect(self) -> None:
        self._connection = ConnectionState.CONNECTING
        self._complete = False

    def begin_sync(self) -> None:
        self._connection = ConnectionState.SYNCING
        self._complete = False

    def complete_sync(self, cursor: int) -> None:
        if cursor < 0:
            raise ValueError("cursor cannot be negative")
        self._cursor = max(self._cursor, cursor)
        self._complete = True
        self._connection = (
            ConnectionState.REPLAY
            if self._mode == RuntimeMode.REPLAY
            else ConnectionState.LIVE
        )

    def degrade(self) -> None:
        self._connection = ConnectionState.DEGRADED
        self._complete = False

    def disconnect(self) -> None:
        self._connection = ConnectionState.DISCONNECTED
        self._complete = False

    def reset_for_resync(self) -> None:
        self._entities.clear()
        self._sequences.clear()
        self._cursor = 0
        self.begin_sync()

    def apply(self, event: Envelope) -> bool:
        if event.mode != self._mode:
            self._invalid_messages += 1
            return False
        sequence_key = (
            event.source.service,
            event.source.instance_id,
            event.schema,
            event.vehicle_id,
        )
        previous_sequence = self._sequences.get(sequence_key)
        if previous_sequence is not None and event.sequence <= previous_sequence:
            return False
        if previous_sequence is not None and event.sequence > previous_sequence + 1:
            self._complete = False
            self._connection = ConnectionState.SYNCING
        self._sequences[sequence_key] = event.sequence
        try:
            projections = self._project(event)
        except (KeyError, TypeError, ValueError):
            self._invalid_messages += 1
            return False
        for projection in projections:
            key = (projection.schema, projection.key)
            self._entities[key] = projection
            self._entities.move_to_end(key)
        self._cursor += 1
        self._trim_noncritical()
        self._trim_critical_history()
        return True

    def apply_snapshot(self, events: tuple[Envelope, ...], cursor: int) -> None:
        self.reset_for_resync()
        for event in events:
            self.apply(event)
        self.complete_sync(cursor)

    def snapshot(self, now: datetime) -> UiSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("snapshot time must be timezone-aware")
        entities = tuple(
            EntityView(
                key=value.key,
                schema=value.schema,
                status=value.status,
                updated_at=value.updated_at,
                age_ms=round(
                    max(0.0, (now - value.updated_at).total_seconds() * 1_000),
                    3,
                ),
                payload=value.payload,
                critical=value.critical,
            )
            for value in self._entities.values()
        )
        risk_levels = [
            str(value.payload.get("level", "UNKNOWN"))
            for value in entities
            if value.schema == "risk/1.0"
        ]
        highest_risk = max(
            risk_levels or ["NONE"],
            key=lambda level: _RISK_RANK.get(level, _RISK_RANK["UNKNOWN"]),
        )
        return UiSnapshot(
            connection=self._connection,
            mode=self._mode,
            complete=self._complete,
            generated_at=now,
            entities=entities,
            highest_risk=highest_risk,
            commands_enabled=self.commands_enabled,
            cursor=self._cursor,
            invalid_messages=self._invalid_messages,
            archived_critical=self._archived_critical,
        )

    def _project(self, event: Envelope) -> list[_StoredEntity]:
        if event.schema == "target/1.0":
            targets = event.payload["targets"]
            if not isinstance(targets, list):
                raise TypeError("target payload targets must be an array")
            return [
                self._stored(
                    event,
                    str(target["track_id"]),
                    str(target.get("track_state", "UNKNOWN")),
                    target,
                )
                for target in targets
                if isinstance(target, dict)
            ]
        key_field = {
            "control.ack/1.0": "ack_id",
            "decision/1.0": "decision_id",
            "environment/1.0": "field_id",
            "health/1.0": "component_id",
            "path/1.0": "path_id",
            "risk/1.0": "risk_id",
            "twin.snapshot/1.0": "twin_id",
            "vehicle.state/1.0": "state_id",
        }.get(event.schema)
        key = str(event.payload[key_field]) if key_field else str(event.event_id)
        status = self._status(event)
        return [self._stored(event, key, status, event.payload)]

    @staticmethod
    def _stored(
        event: Envelope,
        key: str,
        status: str,
        payload: Mapping[str, Any],
    ) -> _StoredEntity:
        return _StoredEntity(
            key=key,
            schema=event.schema,
            status=status,
            updated_at=event.received_at,
            payload=dict(payload),
            critical=event.schema in _CRITICAL_SCHEMAS,
        )

    @staticmethod
    def _status(event: Envelope) -> str:
        payload = event.payload
        if event.schema == "decision/1.0":
            authorization = payload.get("authorization")
            if isinstance(authorization, dict):
                return str(authorization.get("state", "PENDING"))
        for field in ("status", "level", "state"):
            if field in payload:
                return str(payload[field])
        return "VALID" if event.quality.valid else "INVALID"

    def _trim_noncritical(self) -> None:
        regular_count = sum(not value.critical for value in self._entities.values())
        while regular_count > self._max_entities:
            removable = next(
                (
                    key
                    for key, value in self._entities.items()
                    if not value.critical
                ),
                None,
            )
            if removable is None:
                break
            del self._entities[removable]
            regular_count -= 1

    def _trim_critical_history(self) -> None:
        critical_count = sum(value.critical for value in self._entities.values())
        while critical_count > self._max_critical_entities:
            removable = next(
                (
                    key
                    for key, value in self._entities.items()
                    if value.critical
                ),
                None,
            )
            if removable is None:
                break
            del self._entities[removable]
            critical_count -= 1
            self._archived_critical += 1
