from uuid import UUID

import pytest

from low_altitude_ai.domain.identifiers import uuid7


def test_uuid7_contains_requested_timestamp_and_variant() -> None:
    timestamp_ms = 1_722_050_415_123

    value = uuid7(unix_ms=timestamp_ms)

    assert value.version == 7
    assert value.variant == UUID("00000000-0000-7000-8000-000000000000").variant
    assert value.int >> 80 == timestamp_ms


@pytest.mark.parametrize("timestamp_ms", [-1, 1 << 48])
def test_uuid7_rejects_timestamp_outside_48_bits(timestamp_ms: int) -> None:
    with pytest.raises(ValueError, match="48-bit"):
        uuid7(unix_ms=timestamp_ms)
