from datetime import datetime

import pytest

from low_altitude_ai.compat import UTC
from low_altitude_ai.domain.enums import RuntimeMode
from low_altitude_ai.domain.envelope import Envelope, Quality, Source, new_envelope
from low_altitude_ai.domain.identifiers import uuid7


def test_new_envelope_uses_uuid7_and_serializes_utc() -> None:
    observed_at = datetime(2026, 7, 27, 3, 20, 15, 123000, tzinfo=UTC)

    envelope = new_envelope(
        schema="sensor/1.0",
        source=Source(
            service="ingest-service",
            instance_id="edge-01",
            plugin="radar-sim",
            plugin_version="1.0.0",
        ),
        observed_at=observed_at,
        received_at=observed_at,
        run_id=uuid7(),
        mode=RuntimeMode.SIM,
        sequence=1,
        quality=Quality(valid=True, confidence=0.9, clock_uncertainty_ms=1.2),
        payload={"sensor_id": "radar-front-01"},
        vehicle_id="uav-001",
    )

    result = envelope.to_mapping()

    assert envelope.event_id.version == 7
    assert envelope.trace_id == envelope.event_id
    assert result["observed_at"] == "2026-07-27T03:20:15.123Z"
    assert result["mode"] == "SIM"
    assert result["quality"]["confidence"] == 0.9


def test_envelope_copies_mutable_payload() -> None:
    payload = {"value": 1}
    event_id = uuid7()

    envelope = Envelope(
        schema="sensor/1.0",
        event_id=event_id,
        trace_id=event_id,
        source=Source(service="test", instance_id="test-01"),
        observed_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        run_id=uuid7(),
        mode=RuntimeMode.SIM,
        sequence=0,
        quality=Quality(valid=True, confidence=1.0),
        payload=payload,
    )
    payload["value"] = 2

    assert envelope.payload["value"] == 1


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_quality_rejects_out_of_range_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        Quality(valid=False, confidence=confidence)


def test_quality_rejects_negative_clock_uncertainty() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        Quality(valid=False, confidence=0.0, clock_uncertainty_ms=-1)


def test_quality_rejects_duplicate_flags() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        Quality(valid=False, confidence=0.0, flags=("late", "late"))


def test_source_requires_plugin_version_pair() -> None:
    with pytest.raises(ValueError, match="both be set"):
        Source(service="test", instance_id="test-01", plugin="detector")


def test_envelope_rejects_naive_datetime() -> None:
    event_id = uuid7()
    with pytest.raises(ValueError, match="timezone-aware"):
        Envelope(
            schema="sensor/1.0",
            event_id=event_id,
            trace_id=event_id,
            source=Source(service="test", instance_id="test-01"),
            observed_at=datetime(2026, 7, 27),
            received_at=datetime.now(UTC),
            run_id=uuid7(),
            mode=RuntimeMode.SIM,
            sequence=0,
            quality=Quality(valid=True, confidence=1.0),
            payload={},
        )
