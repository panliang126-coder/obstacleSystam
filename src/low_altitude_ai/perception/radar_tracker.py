"""Deterministic nearest-neighbor radar tracker baseline."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from low_altitude_ai.domain import Envelope, Quality, Source
from low_altitude_ai.ports.plugins import PluginContext, PluginManifest
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory

Vector3 = tuple[float, float, float]


class PerceptionNotInitializedError(RuntimeError):
    """The plugin was used outside its initialized lifecycle."""


@dataclass(frozen=True, slots=True)
class RadarTrackerConfig:
    association_gate_m: float = 15.0
    confirm_hits: int = 3
    coast_timeout_ms: int = 250
    lost_timeout_ms: int = 600
    max_tracks: int = 1_000
    dedup_capacity: int = 10_000

    def __post_init__(self) -> None:
        if self.association_gate_m <= 0:
            raise ValueError("association_gate_m must be positive")
        if self.confirm_hits < 2:
            raise ValueError("confirm_hits must be at least 2")
        if not 0 < self.coast_timeout_ms < self.lost_timeout_ms:
            raise ValueError("track timeout ordering is invalid")
        if self.max_tracks < 1 or self.dedup_capacity < 1:
            raise ValueError("tracker capacities must be positive")


@dataclass(slots=True)
class _Track:
    track_id: UUID
    position: Vector3
    velocity: Vector3
    covariance: tuple[float, ...]
    hits: int
    last_observed_at: datetime
    source_ref: UUID
    state: str = "TENTATIVE"


def _spherical_to_enu(detection: Mapping[str, Any]) -> tuple[Vector3, Vector3]:
    range_m = float(detection["range_m"])
    azimuth_rad = math.radians(float(detection["azimuth_deg"]))
    elevation_rad = math.radians(float(detection["elevation_deg"]))
    radial_velocity = float(detection["radial_velocity_m_s"])
    horizontal = range_m * math.cos(elevation_rad)
    direction = (
        math.cos(elevation_rad) * math.cos(azimuth_rad),
        math.cos(elevation_rad) * math.sin(azimuth_rad),
        math.sin(elevation_rad),
    )
    position = (
        round(horizontal * math.cos(azimuth_rad), 6),
        round(horizontal * math.sin(azimuth_rad), 6),
        round(range_m * math.sin(elevation_rad), 6),
    )
    velocity = tuple(round(component * radial_velocity, 6) for component in direction)
    return position, velocity  # type: ignore[return-value]


def _distance(first: Vector3, second: Vector3) -> float:
    return math.sqrt(sum((first[index] - second[index]) ** 2 for index in range(3)))


class RadarTrackerPlugin:
    """A minimal radar tracker with explicit state, evidence and degradation."""

    def __init__(
        self,
        *,
        config: RadarTrackerConfig,
        event_ids: DeterministicUuid7Factory,
        track_ids: DeterministicUuid7Factory,
    ) -> None:
        self._config = config
        self._event_ids = event_ids
        self._track_ids = track_ids
        self._context: PluginContext | None = None
        self._tracks: dict[UUID, _Track] = {}
        self._last_sequence: dict[str, int] = {}
        self._seen_order: deque[UUID] = deque()
        self._seen: set[UUID] = set()

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="radar_nearest_neighbor",
            version="1.0.0",
            kind="PERCEPTION",
            api_version="1.0",
            input_schema="sensor/1.0",
            output_schema="target/1.0",
            capabilities=("RADAR", "ENU", "TRACK_LIFECYCLE", "DETERMINISTIC"),
        )

    async def initialize(self, context: PluginContext) -> None:
        if context.mode.value not in {"SIM", "REPLAY", "HIL", "LIVE"}:
            raise ValueError("unsupported runtime mode")
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
                service="perception-service",
                instance_id="radar-tracker",
                plugin=self.manifest.name,
                plugin_version=self.manifest.version,
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=context.clock.monotonic_ns(),
            run_id=UUID(context.run_id),
            mode=context.mode,
            sequence=0,
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

    async def process(self, event: Envelope) -> Envelope:
        self._require_context()
        if event.schema != "sensor/1.0":
            raise ValueError("radar tracker requires sensor/1.0")
        if event.payload.get("sensor_type") != "RADAR":
            raise ValueError("radar tracker only accepts RADAR observations")
        frame_id = self._required_string(event.payload, "frame_id")
        session_id = self._required_string(event.payload, "source_session_id")
        flags: set[str] = set(event.quality.flags)

        if event.event_id in self._seen:
            flags.add("DUPLICATE_INPUT")
            return self._build_output(event, frame_id, flags, valid=False)
        self._remember(event.event_id)

        previous_sequence = self._last_sequence.get(session_id)
        if previous_sequence is not None and event.sequence <= previous_sequence:
            flags.add("OUT_OF_ORDER_INPUT")
            return self._build_output(event, frame_id, flags, valid=False)
        self._last_sequence[session_id] = event.sequence

        sample = event.payload.get("sample")
        if not isinstance(sample, Mapping):
            raise ValueError("radar sample must be an object")
        detections_value = sample.get("detections")
        if not isinstance(detections_value, list):
            raise ValueError("radar detections must be an array")
        detections: list[tuple[Vector3, Vector3]] = []
        for value in detections_value:
            if not isinstance(value, Mapping):
                flags.add("INVALID_DETECTION")
                continue
            try:
                detections.append(_spherical_to_enu(value))
            except (KeyError, TypeError, ValueError):
                flags.add("INVALID_DETECTION")

        unmatched = set(self._tracks)
        for position, radial_velocity in detections:
            match = min(
                unmatched,
                key=lambda track_id: _distance(
                    self._predict(self._tracks[track_id], event.observed_at),
                    position,
                ),
                default=None,
            )
            if (
                match is not None
                and _distance(self._predict(self._tracks[match], event.observed_at), position)
                <= self._config.association_gate_m
            ):
                self._update_track(
                    self._tracks[match],
                    position,
                    radial_velocity,
                    event,
                )
                unmatched.remove(match)
            else:
                if len(self._tracks) >= self._config.max_tracks:
                    flags.add("TRACK_CAPACITY_REACHED")
                    continue
                track_id = self._track_ids.new(event.observed_at)
                self._tracks[track_id] = _Track(
                    track_id=track_id,
                    position=position,
                    velocity=radial_velocity,
                    covariance=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.5),
                    hits=1,
                    last_observed_at=event.observed_at,
                    source_ref=event.event_id,
                )

        lost_after_output: list[UUID] = []
        for track_id in unmatched:
            track = self._tracks[track_id]
            age_ms = (event.observed_at - track.last_observed_at).total_seconds() * 1_000
            if age_ms >= self._config.lost_timeout_ms:
                track.state = "LOST"
                lost_after_output.append(track_id)
            elif age_ms >= self._config.coast_timeout_ms:
                track.state = "COASTING"
                track.covariance = tuple(value * 1.5 for value in track.covariance)

        output = self._build_output(
            event,
            frame_id,
            flags,
            valid=event.quality.valid,
        )
        for track_id in lost_after_output:
            del self._tracks[track_id]
        return output

    def _update_track(
        self,
        track: _Track,
        position: Vector3,
        radial_velocity: Vector3,
        event: Envelope,
    ) -> None:
        elapsed_s = (event.observed_at - track.last_observed_at).total_seconds()
        if elapsed_s > 0:
            measured_velocity = tuple(
                (position[index] - track.position[index]) / elapsed_s for index in range(3)
            )
            track.velocity = tuple(
                round(
                    0.8 * measured_velocity[index] + 0.2 * radial_velocity[index],
                    6,
                )
                for index in range(3)
            )  # type: ignore[assignment]
        track.position = position
        track.hits += 1
        track.last_observed_at = event.observed_at
        track.source_ref = event.event_id
        track.covariance = (0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.8)
        track.state = "CONFIRMED" if track.hits >= self._config.confirm_hits else "TENTATIVE"

    @staticmethod
    def _predict(track: _Track, at: datetime) -> Vector3:
        elapsed_s = max(0.0, (at - track.last_observed_at).total_seconds())
        return tuple(
            round(track.position[index] + track.velocity[index] * elapsed_s, 6)
            for index in range(3)
        )  # type: ignore[return-value]

    def _build_output(
        self,
        event: Envelope,
        frame_id: str,
        flags: set[str],
        *,
        valid: bool,
    ) -> Envelope:
        context = self._require_context()
        now = context.clock.now()
        event_id = self._event_ids.new(now)
        batch_id = self._event_ids.new(now)
        targets = [
            {
                "track_id": str(track.track_id),
                "state": track.state,
                "classification": {
                    "top_label": "UNKNOWN",
                    "probabilities": {"UNKNOWN": 1.0},
                },
                "position": {
                    "enu": {
                        "east_m": track.position[0],
                        "north_m": track.position[1],
                        "up_m": track.position[2],
                    },
                    "frame_id": frame_id,
                    "covariance": list(track.covariance),
                },
                "velocity": {
                    "frame_id": frame_id,
                    "east_m_s": track.velocity[0],
                    "north_m_s": track.velocity[1],
                    "up_m_s": track.velocity[2],
                    "covariance": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                },
                "dimensions_m": None,
                "embedding_ref": None,
                "source_refs": [str(track.source_ref)],
                "age_ms": max(
                    0,
                    round((event.observed_at - track.last_observed_at).total_seconds() * 1_000),
                ),
            }
            for track in sorted(self._tracks.values(), key=lambda item: str(item.track_id))
        ]
        return Envelope(
            schema="target/1.0",
            event_id=event_id,
            trace_id=event.trace_id,
            causation_id=event.event_id,
            source=Source(
                service="perception-service",
                instance_id="radar-tracker",
                plugin=self.manifest.name,
                plugin_version=self.manifest.version,
            ),
            observed_at=event.observed_at,
            received_at=now,
            monotonic_ns=context.clock.monotonic_ns(),
            run_id=event.run_id,
            mode=event.mode,
            vehicle_id=event.vehicle_id,
            sequence=event.sequence,
            quality=Quality(
                valid=valid and "INVALID_DETECTION" not in flags,
                confidence=event.quality.confidence if valid else 0.0,
                flags=tuple(sorted(flags)),
                clock_uncertainty_ms=event.quality.clock_uncertainty_ms,
            ),
            payload={"batch_id": str(batch_id), "frame_id": frame_id, "targets": targets},
        )

    @staticmethod
    def _required_string(payload: Mapping[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value

    def _remember(self, event_id: UUID) -> None:
        if len(self._seen_order) == self._config.dedup_capacity:
            expired = self._seen_order.popleft()
            self._seen.remove(expired)
        self._seen_order.append(event_id)
        self._seen.add(event_id)

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise PerceptionNotInitializedError("radar tracker is not initialized")
        return self._context
