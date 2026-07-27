"""Identifier factories."""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_MAX_UNIX_MS = (1 << 48) - 1


def uuid7(*, unix_ms: int | None = None) -> UUID:
    """Create an RFC 9562 UUIDv7.

    ``unix_ms`` exists for deterministic tests. Production callers should let the function
    obtain the current Unix time.
    """

    timestamp_ms = time.time_ns() // 1_000_000 if unix_ms is None else unix_ms
    if not 0 <= timestamp_ms <= _MAX_UNIX_MS:
        raise ValueError("unix_ms must fit in the 48-bit UUIDv7 timestamp field")

    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return UUID(int=value)
