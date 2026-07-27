from dataclasses import replace
from datetime import timedelta

import pytest

from low_altitude_ai.digital_twin import (
    TwinCapacityError,
    TwinStateStore,
    UnsupportedTwinEventError,
)
from low_altitude_ai.domain import Envelope
from low_altitude_ai.domain.identifiers import uuid7

CONFIG_HASH = "sha256:" + "a" * 64


def make_store(*, max_entities: int = 10) -> TwinStateStore:
    return TwinStateStore(
        twin_id="site-alpha/uav-001",
        frame_id="site-alpha-enu-v1",
        map_version="site-alpha-map@1.0.0",
        config_hash=CONFIG_HASH,
        max_entities=max_entities,
    )


def test_twin_revisions_are_monotonic_and_duplicates_are_idempotent(
    sensor_event: Envelope,
) -> None:
    store = make_store()

    first = store.apply(sensor_event)
    duplicate = store.apply(sensor_event)
    second_event = replace(
        sensor_event,
        event_id=uuid7(),
        sequence=1,
        observed_at=sensor_event.observed_at + timedelta(milliseconds=100),
        received_at=sensor_event.received_at + timedelta(milliseconds=100),
    )
    second = store.apply(second_event)

    assert first.applied and first.snapshot.revision == 1
    assert not duplicate.applied and duplicate.reason == "duplicate"
    assert second.applied and second.snapshot.revision == 2
    assert store.get("sensor/radar-front-01").revision == 2  # type: ignore[union-attr]


def test_late_event_does_not_roll_back_twin_revision(sensor_event: Envelope) -> None:
    store = make_store()
    store.apply(sensor_event)
    late = replace(
        sensor_event,
        event_id=uuid7(),
        observed_at=sensor_event.observed_at - timedelta(seconds=1),
        received_at=sensor_event.received_at + timedelta(seconds=1),
    )

    outcome = store.apply(late)

    assert not outcome.applied
    assert outcome.reason == "late"
    assert store.revision == 1


def test_twin_capacity_is_bounded(sensor_event: Envelope) -> None:
    store = make_store(max_entities=1)
    store.apply(sensor_event)
    other_payload = dict(sensor_event.payload)
    other_payload["sensor_id"] = "radar-rear-01"
    other = replace(sensor_event, event_id=uuid7(), payload=other_payload)

    with pytest.raises(TwinCapacityError, match="capacity"):
        store.apply(other)


def test_unsupported_event_fails_explicitly(sensor_event: Envelope) -> None:
    store = make_store()
    unsupported = replace(sensor_event, schema="risk/1.0")

    with pytest.raises(UnsupportedTwinEventError, match="does not consume"):
        store.apply(unsupported)
