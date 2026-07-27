"""Deterministic pause/seek/step replay controller."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.ui.state import UiSnapshot, UiStateStore


class ReplayController:
    def __init__(
        self,
        events: tuple[Envelope, ...],
        *,
        max_entities: int = 5_000,
    ) -> None:
        if any(event.mode != RuntimeMode.REPLAY for event in events):
            raise ValueError("replay controller only accepts REPLAY events")
        self._events = tuple(
            sorted(events, key=lambda event: (event.received_at, str(event.event_id)))
        )
        self._max_entities = max_entities
        self._store = UiStateStore(
            mode=RuntimeMode.REPLAY,
            max_entities=max_entities,
        )
        self._position = 0
        self._paused = True
        self._store.complete_sync(0)

    @property
    def store(self) -> UiStateStore:
        return self._store

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def play(self) -> None:
        self._paused = False

    def seek(self, at: datetime) -> int:
        self._store = UiStateStore(
            mode=RuntimeMode.REPLAY,
            max_entities=self._max_entities,
        )
        self._store.begin_sync()
        self._position = 0
        while (
            self._position < len(self._events)
            and self._events[self._position].received_at <= at
        ):
            self._store.apply(self._events[self._position])
            self._position += 1
        self._store.complete_sync(self._position)
        return self._position

    def step(self) -> Envelope | None:
        if self._position >= len(self._events):
            return None
        event = self._events[self._position]
        self._store.apply(event)
        self._position += 1
        self._store.complete_sync(self._position)
        return event

    def snapshot(self, now: datetime) -> UiSnapshot:
        return self._store.snapshot(now)

    def state_hash(self, now: datetime) -> str:
        snapshot = self.snapshot(now)
        value = {
            "complete": snapshot.complete,
            "cursor": snapshot.cursor,
            "entities": [
                {
                    "key": entity.key,
                    "schema": entity.schema,
                    "status": entity.status,
                    "payload": entity.payload,
                }
                for entity in snapshot.entities
            ],
        }
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()
