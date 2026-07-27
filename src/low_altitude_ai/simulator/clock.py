"""Controllable clock used by deterministic simulations and replay."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from low_altitude_ai.compat import UTC


class SimClock:
    """A single-writer clock advanced explicitly or by injected business sleeps."""

    def __init__(self, start_at: datetime) -> None:
        if start_at.tzinfo is None or start_at.utcoffset() is None:
            raise ValueError("start_at must be timezone-aware")
        self._now = start_at.astimezone(UTC)
        self._monotonic_ns = 0

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return self._monotonic_ns

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("simulation time cannot move backwards")
        delta_ns = round(seconds * 1_000_000_000)
        self._monotonic_ns += delta_ns
        self._now += timedelta(microseconds=delta_ns / 1_000)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)
