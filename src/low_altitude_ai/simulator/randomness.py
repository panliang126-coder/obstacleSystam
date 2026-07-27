"""Stable component-scoped random streams and deterministic UUIDv7 generation."""

from __future__ import annotations

import hashlib
import random
from datetime import datetime
from uuid import UUID


def _derived_seed(master_seed: int, component: str) -> int:
    if not component.strip():
        raise ValueError("component name must be non-empty")
    material = f"{master_seed}\0{component}".encode()
    return int.from_bytes(hashlib.sha256(material).digest(), byteorder="big")


class RandomStream:
    """A stable stream whose values do not depend on unrelated components."""

    def __init__(self, master_seed: int, component: str) -> None:
        self._random = random.Random(_derived_seed(master_seed, component))

    def random(self) -> float:
        return self._random.random()

    def uniform(self, lower: float, upper: float) -> float:
        return self._random.uniform(lower, upper)

    def gauss(self, mean: float, standard_deviation: float) -> float:
        return self._random.gauss(mean, standard_deviation)

    def getrandbits(self, bits: int) -> int:
        return self._random.getrandbits(bits)


class DeterministicUuid7Factory:
    """Create reproducible UUIDv7 values while retaining the event timestamp."""

    def __init__(self, stream: RandomStream) -> None:
        self._stream = stream

    def new(self, at: datetime) -> UUID:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("UUID timestamp must be timezone-aware")
        unix_ms = int(at.timestamp() * 1_000)
        if not 0 <= unix_ms <= (1 << 48) - 1:
            raise ValueError("UUID timestamp must fit in 48 bits")
        value = unix_ms << 80
        value |= 0x7 << 76
        value |= self._stream.getrandbits(12) << 64
        value |= 0b10 << 62
        value |= self._stream.getrandbits(62)
        return UUID(int=value)
