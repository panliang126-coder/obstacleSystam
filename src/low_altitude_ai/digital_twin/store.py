"""Bounded, revisioned in-memory digital twin state store."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from low_altitude_ai.compat import UTC
from low_altitude_ai.domain.envelope import Envelope, Quality


class UnsupportedTwinEventError(ValueError):
    """An event schema has no registered Twin reducer."""


class TwinCapacityError(RuntimeError):
    """Applying an event would exceed the configured bounded state."""


def _wire_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class TwinEntity:
    entity_id: str
    entity_type: str
    revision: int
    observed_at: datetime
    state: Mapping[str, Any]
    quality: Quality
    source_refs: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class TwinSnapshot:
    twin_id: str
    revision: int
    as_of: datetime
    watermark: datetime
    frame_id: str
    map_version: str
    config_hash: str
    vehicle_ref: str | None
    sensor_refs: tuple[str, ...]
    track_refs: tuple[str, ...]
    environment_ref: str | None
    health_refs: tuple[str, ...]
    input_refs: tuple[UUID, ...]
    quality: Quality

    def to_payload(self) -> dict[str, Any]:
        staleness_ms = max(0.0, (self.as_of - self.watermark).total_seconds() * 1_000)
        return {
            "twin_id": self.twin_id,
            "revision": self.revision,
            "as_of": _wire_datetime(self.as_of),
            "watermark": _wire_datetime(self.watermark),
            "frame_id": self.frame_id,
            "map_version": self.map_version,
            "config_hash": self.config_hash,
            "vehicle_ref": self.vehicle_ref,
            "sensor_refs": list(self.sensor_refs),
            "track_refs": list(self.track_refs),
            "environment_ref": self.environment_ref,
            "health_refs": list(self.health_refs),
            "input_refs": [str(value) for value in self.input_refs],
            "staleness": {
                "vehicle_ms": staleness_ms if self.vehicle_ref else None,
                "sensors_max_ms": staleness_ms if self.sensor_refs else None,
                "tracks_max_ms": staleness_ms if self.track_refs else None,
                "environment_ms": staleness_ms if self.environment_ref else None,
            },
            "quality": {
                "valid": self.quality.valid,
                "confidence": self.quality.confidence,
                "clock_uncertainty_ms": self.quality.clock_uncertainty_ms,
                "flags": list(self.quality.flags),
            },
        }

    def stable_hash(self) -> str:
        canonical = json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    applied: bool
    reason: str
    snapshot: TwinSnapshot


class TwinStateStore:
    """Single-writer in-memory Twin with deduplication and monotonic revisions."""

    def __init__(
        self,
        *,
        twin_id: str,
        frame_id: str,
        map_version: str,
        config_hash: str,
        max_entities: int = 10_000,
        dedup_capacity: int = 50_000,
    ) -> None:
        if max_entities < 1 or dedup_capacity < 1:
            raise ValueError("Twin capacities must be positive")
        if not twin_id.strip() or not frame_id.strip() or not map_version.strip():
            raise ValueError("Twin identity, frame and map version must be non-empty")
        if not config_hash.startswith("sha256:") or len(config_hash) != 71:
            raise ValueError("config_hash must be a sha256 digest")
        self._twin_id = twin_id
        self._frame_id = frame_id
        self._map_version = map_version
        self._config_hash = config_hash
        self._max_entities = max_entities
        self._dedup_capacity = dedup_capacity
        self._entities: dict[str, TwinEntity] = {}
        self._seen_order: deque[UUID] = deque()
        self._seen: set[UUID] = set()
        self._revision = 0
        self._watermark: datetime | None = None
        self._last_input_refs: tuple[UUID, ...] = ()
        self._last_as_of: datetime | None = None

    @property
    def revision(self) -> int:
        return self._revision

    def get(self, entity_id: str) -> TwinEntity | None:
        return self._entities.get(entity_id)

    def apply(self, event: Envelope) -> ApplyOutcome:
        if event.event_id in self._seen:
            return ApplyOutcome(False, "duplicate", self.snapshot(event.received_at))
        candidates = self._reduce(event)
        if any(
            previous is not None and event.observed_at < previous.observed_at
            for key, _, _ in candidates
            if (previous := self._entities.get(key)) is not None
        ):
            self._remember(event.event_id)
            return ApplyOutcome(False, "late", self.snapshot(event.received_at))
        new_keys = {key for key, _, _ in candidates if key not in self._entities}
        if len(self._entities) + len(new_keys) > self._max_entities:
            raise TwinCapacityError("Twin entity capacity would be exceeded")

        next_revision = self._revision + 1
        for key, entity_type, state in candidates:
            self._entities[key] = TwinEntity(
                entity_id=key,
                entity_type=entity_type,
                revision=next_revision,
                observed_at=event.observed_at,
                state=deepcopy(state),
                quality=event.quality,
                source_refs=(event.event_id,),
            )
        self._revision = next_revision
        self._watermark = max(
            filter(None, (self._watermark, event.observed_at)),
        )
        self._last_as_of = event.received_at
        self._last_input_refs = (event.event_id,)
        self._remember(event.event_id)
        return ApplyOutcome(True, "applied", self.snapshot(event.received_at))

    def snapshot(self, as_of: datetime | None = None) -> TwinSnapshot:
        if self._revision == 0 or self._watermark is None:
            raise RuntimeError("cannot snapshot an empty Twin")
        effective_as_of = as_of or self._last_as_of
        if effective_as_of is None:
            raise RuntimeError("Twin has no as_of timestamp")
        by_type: dict[str, list[TwinEntity]] = {}
        for entity in self._entities.values():
            by_type.setdefault(entity.entity_type, []).append(entity)

        def refs(entity_type: str) -> tuple[str, ...]:
            return tuple(
                f"state://{item.entity_id}/{item.revision}"
                for item in sorted(
                    by_type.get(entity_type, []),
                    key=lambda entity: entity.entity_id,
                )
            )

        confidences = [entity.quality.confidence for entity in self._entities.values()]
        flags = tuple(
            sorted(
                {
                    flag
                    for entity in self._entities.values()
                    for flag in entity.quality.flags
                }
            )
        )
        vehicle_refs = refs("vehicle")
        environment_refs = refs("environment")
        return TwinSnapshot(
            twin_id=self._twin_id,
            revision=self._revision,
            as_of=effective_as_of,
            watermark=self._watermark,
            frame_id=self._frame_id,
            map_version=self._map_version,
            config_hash=self._config_hash,
            vehicle_ref=vehicle_refs[0] if vehicle_refs else None,
            sensor_refs=refs("sensor"),
            track_refs=refs("track"),
            environment_ref=environment_refs[0] if environment_refs else None,
            health_refs=refs("health"),
            input_refs=self._last_input_refs,
            quality=Quality(
                valid=all(entity.quality.valid for entity in self._entities.values()),
                confidence=min(confidences),
                flags=flags,
                clock_uncertainty_ms=max(
                    (
                        entity.quality.clock_uncertainty_ms or 0.0
                        for entity in self._entities.values()
                    ),
                    default=0.0,
                ),
            ),
        )

    def _reduce(self, event: Envelope) -> list[tuple[str, str, Mapping[str, Any]]]:
        payload = event.payload
        if event.schema == "sensor/1.0":
            sensor_id = self._required_string(payload, "sensor_id")
            return [(f"sensor/{sensor_id}", "sensor", payload)]
        if event.schema == "target/1.0":
            targets = payload.get("targets")
            if not isinstance(targets, list):
                raise ValueError("target payload must contain an array of targets")
            reduced: list[tuple[str, str, Mapping[str, Any]]] = []
            for target in targets:
                if not isinstance(target, dict):
                    raise ValueError("each target must be an object")
                track_id = self._required_string(target, "track_id")
                reduced.append((f"track/{track_id}", "track", target))
            return reduced
        if event.schema == "environment/1.0":
            environment_id = self._required_string(payload, "environment_id")
            return [(f"environment/{environment_id}", "environment", payload)]
        if event.schema == "health/1.0":
            component_id = self._required_string(payload, "component_id")
            return [(f"health/{component_id}", "health", payload)]
        raise UnsupportedTwinEventError(f"Twin does not consume {event.schema!r}")

    @staticmethod
    def _required_string(value: Mapping[str, Any], key: str) -> str:
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return item

    def _remember(self, event_id: UUID) -> None:
        if event_id in self._seen:
            return
        if len(self._seen_order) == self._dedup_capacity:
            expired = self._seen_order.popleft()
            self._seen.remove(expired)
        self._seen_order.append(event_id)
        self._seen.add(event_id)
