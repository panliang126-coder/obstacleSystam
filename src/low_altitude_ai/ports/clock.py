"""Clock port used by production, simulation, replay and tests."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ClockPort(Protocol):
    """Business code must use this port instead of reading wall time directly."""

    def now(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...

    async def sleep(self, seconds: float) -> None: ...
