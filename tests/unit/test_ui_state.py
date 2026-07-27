from dataclasses import replace
from datetime import timedelta

import pytest

from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.domain.identifiers import uuid7
from low_altitude_ai.management import ManagementService, Role
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock
from low_altitude_ai.ui import (
    ConnectionState,
    ReplayController,
    UiCommandBlocked,
    UiCommandClient,
    UiStateStore,
)


def _risk(
    event: Envelope,
    *,
    mode: RuntimeMode,
    sequence: int,
    received_offset_s: float = 0.0,
) -> Envelope:
    payload = dict(event.payload)
    payload["risk_id"] = str(uuid7())
    at = event.received_at + timedelta(seconds=received_offset_s)
    return replace(
        event,
        event_id=uuid7(),
        mode=mode,
        sequence=sequence,
        observed_at=at,
        received_at=at,
        payload=payload,
    )


def test_ui_store_tracks_freshness_connection_gaps_and_decision_status(
    risk_event: Envelope,
) -> None:
    store = UiStateStore(mode=RuntimeMode.SIM, max_entities=10)
    store.begin_connect()
    store.begin_sync()
    assert store.apply(_risk(risk_event, mode=RuntimeMode.SIM, sequence=0))
    store.complete_sync(1)
    live = store.snapshot(risk_event.received_at)
    assert live.connection == ConnectionState.LIVE
    assert live.highest_risk == "CRITICAL"
    assert live.commands_enabled

    gap = _risk(risk_event, mode=RuntimeMode.SIM, sequence=2)
    assert store.apply(gap)
    syncing = store.snapshot(risk_event.received_at)
    assert syncing.connection == ConnectionState.SYNCING
    assert not syncing.complete
    assert not store.apply(gap)

    decision_payload = {
        "decision_id": str(uuid7()),
        "authorization": {"state": "PENDING"},
    }
    decision = replace(
        risk_event,
        schema="decision/1.0",
        event_id=uuid7(),
        sequence=0,
        payload=decision_payload,
    )
    assert store.apply(decision)
    assert any(
        entity.status == "PENDING"
        for entity in store.snapshot(risk_event.received_at).entities
        if entity.schema == "decision/1.0"
    )
    authorized_payload = dict(decision_payload)
    authorized_payload["authorization"] = {"state": "AUTHORIZED"}
    authorized = replace(
        decision,
        event_id=uuid7(),
        source=replace(decision.source, service="safety-gate"),
        payload=authorized_payload,
    )
    assert store.apply(authorized)
    assert any(
        entity.status == "AUTHORIZED"
        for entity in store.snapshot(risk_event.received_at).entities
        if entity.schema == "decision/1.0"
    )

    wrong_mode = _risk(risk_event, mode=RuntimeMode.REPLAY, sequence=3)
    assert not store.apply(wrong_mode)
    store.disconnect()
    disconnected = store.snapshot(risk_event.received_at + timedelta(seconds=1))
    assert disconnected.connection == ConnectionState.DISCONNECTED
    assert not disconnected.commands_enabled
    assert max(entity.age_ms for entity in disconnected.entities) >= 1_000
    assert disconnected.invalid_messages == 1


def test_replay_seek_is_deterministic_and_cannot_send_live_commands(
    risk_event: Envelope,
) -> None:
    events = tuple(
        _risk(
            risk_event,
            mode=RuntimeMode.REPLAY,
            sequence=index,
            received_offset_s=float(index),
        )
        for index in range(3)
    )
    replay = ReplayController(events)
    seek_time = events[1].received_at
    assert replay.seek(seek_time) == 2
    first_hash = replay.state_hash(seek_time)
    assert replay.seek(seek_time) == 2
    assert replay.state_hash(seek_time) == first_hash
    assert replay.step() == events[2]
    assert replay.step() is None

    clock = SimClock(seek_time)
    service = ManagementService(
        clock=clock,
        operation_ids=DeterministicUuid7Factory(RandomStream(62, "ui-command")),
    )
    client = UiCommandClient(
        store=replay.store,
        service=service,
        clock=clock,
        actor="operator",
        role=Role.OPERATOR,
    )
    with pytest.raises(UiCommandBlocked, match="live connection"):
        client.send(
            action="mission.pause",
            resource="mission-001",
            idempotency_key="replay-command-001",
            expected_revision=0,
            reason="must not leave replay",
            payload={},
        )
    assert client.sent_count == 0
    assert not service.audit_records
