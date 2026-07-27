"""Versioned event envelope shared by every module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from low_altitude_ai.compat import UTC
from low_altitude_ai.domain.enums import RuntimeMode
from low_altitude_ai.domain.identifiers import uuid7


def _ensure_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _wire_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Source:
    """Producer identity included in every event."""

    service: str
    instance_id: str
    plugin: str | None = None
    plugin_version: str | None = None

    def __post_init__(self) -> None:
        if not self.service.strip() or not self.instance_id.strip():
            raise ValueError("source service and instance_id must be non-empty")
        if (self.plugin is None) != (self.plugin_version is None):
            raise ValueError("plugin and plugin_version must either both be set or both be null")


@dataclass(frozen=True, slots=True)
class Quality:
    """Quality and clock uncertainty attached to an event."""

    valid: bool
    confidence: float
    flags: tuple[str, ...] = ()
    clock_uncertainty_ms: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("quality confidence must be between 0 and 1")
        if self.clock_uncertainty_ms is not None and self.clock_uncertainty_ms < 0:
            raise ValueError("clock uncertainty cannot be negative")
        if len(set(self.flags)) != len(self.flags):
            raise ValueError("quality flags must be unique")


@dataclass(frozen=True, slots=True)
class Envelope:
    """Immutable domain representation of the JSON event envelope."""

    schema: str
    event_id: UUID
    trace_id: UUID
    source: Source
    observed_at: datetime
    received_at: datetime
    run_id: UUID
    mode: RuntimeMode
    sequence: int
    quality: Quality
    payload: Mapping[str, Any]
    causation_id: UUID | None = None
    monotonic_ns: int | None = None
    vehicle_id: str | None = None

    def __post_init__(self) -> None:
        if "/" not in self.schema:
            raise ValueError("schema must use '<name>/<major>.<minor>'")
        if self.event_id.version != 7 or self.trace_id.version != 7:
            raise ValueError("event_id and trace_id must be UUIDv7")
        if self.run_id.version != 7:
            raise ValueError("run_id must be UUIDv7")
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")
        if self.monotonic_ns is not None and self.monotonic_ns < 0:
            raise ValueError("monotonic_ns cannot be negative")
        object.__setattr__(self, "observed_at", _ensure_aware(self.observed_at, "observed_at"))
        object.__setattr__(self, "received_at", _ensure_aware(self.received_at, "received_at"))
        object.__setattr__(self, "payload", dict(self.payload))

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""

        return {
            "schema": self.schema,
            "event_id": str(self.event_id),
            "trace_id": str(self.trace_id),
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "source": {
                "service": self.source.service,
                "instance_id": self.source.instance_id,
                "plugin": self.source.plugin,
                "plugin_version": self.source.plugin_version,
            },
            "observed_at": _wire_datetime(self.observed_at),
            "received_at": _wire_datetime(self.received_at),
            "monotonic_ns": self.monotonic_ns,
            "run_id": str(self.run_id),
            "mode": self.mode.value,
            "vehicle_id": self.vehicle_id,
            "sequence": self.sequence,
            "quality": {
                "valid": self.quality.valid,
                "confidence": self.quality.confidence,
                "clock_uncertainty_ms": self.quality.clock_uncertainty_ms,
                "flags": list(self.quality.flags),
            },
            "payload": dict(self.payload),
        }


def new_envelope(
    *,
    schema: str,
    source: Source,
    observed_at: datetime,
    received_at: datetime,
    run_id: UUID,
    mode: RuntimeMode,
    sequence: int,
    quality: Quality,
    payload: Mapping[str, Any],
    trace_id: UUID | None = None,
    causation_id: UUID | None = None,
    monotonic_ns: int | None = None,
    vehicle_id: str | None = None,
) -> Envelope:
    """Create an event with a new UUIDv7 while preserving an optional trace."""

    event_id = uuid7()
    return Envelope(
        schema=schema,
        event_id=event_id,
        trace_id=trace_id or event_id,
        causation_id=causation_id,
        source=source,
        observed_at=observed_at,
        received_at=received_at,
        monotonic_ns=monotonic_ns,
        run_id=run_id,
        mode=mode,
        vehicle_id=vehicle_id,
        sequence=sequence,
        quality=quality,
        payload=payload,
    )
