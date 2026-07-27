from dataclasses import replace
from datetime import timedelta

import pytest

from low_altitude_ai.control import ControlDispatchRejected, SimulatedControlGateway
from low_altitude_ai.decision import (
    DecisionCenter,
    DecisionConfig,
    DecisionContext,
    DecisionState,
    SafetyContext,
    SafetyGate,
)
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.domain.identifiers import uuid7
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


def _payload(event: Envelope, **updates: object) -> Envelope:
    payload = dict(event.payload)
    payload.update(updates)
    return replace(event, payload=payload)


def _center(clock: SimClock, component: str = "decision") -> DecisionCenter:
    return DecisionCenter(
        config=DecisionConfig(),
        clock=clock,
        event_ids=DeterministicUuid7Factory(RandomStream(10, component)),
    )


def _context(
    risk: Envelope,
    path: Envelope,
    vehicle: Envelope,
) -> DecisionContext:
    return DecisionContext(
        mission_id="test-mission",
        risk=risk,
        path=path,
        vehicle_state=vehicle,
    )


@pytest.mark.parametrize(
    ("scenario", "expected_action", "expected_state"),
    [
        ("continue", "CONTINUE", DecisionState.CONTINUING),
        ("avoid", "AVOID", DecisionState.AVOIDING),
        ("return", "RETURN", DecisionState.RETURNING),
        ("land", "LAND", DecisionState.LANDING),
        ("hold", "HOLD", DecisionState.HOLDING),
    ],
)
def test_baseline_policy_covers_phase5_exit_actions(
    scenario: str,
    expected_action: str,
    expected_state: DecisionState,
    risk_event: Envelope,
    path_event: Envelope,
    vehicle_state_event: Envelope,
) -> None:
    risk = risk_event
    path = path_event
    vehicle = vehicle_state_event
    if scenario == "continue":
        dimensions = dict(risk.payload["dimensions"])
        dimensions["collision"] = 5.0
        risk = _payload(risk, score=10.0, level="LOW", dimensions=dimensions)
    elif scenario == "return":
        vehicle = _payload(vehicle, battery_pct=25.0)
    elif scenario == "land":
        vehicle = _payload(vehicle, battery_pct=10.0)
    elif scenario == "hold":
        path = _payload(
            path,
            valid_until=(risk.received_at - timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
        )

    clock = SimClock(risk.received_at)
    center = _center(clock, scenario)
    decision = center.decide(_context(risk, path, vehicle))

    assert decision.payload["action"] == expected_action
    assert center.state == expected_state
    assert decision.payload["reason_codes"]
    assert decision.payload["authorization"]["state"] == "PENDING"
    assert len(center.transitions) == 1


def test_recovery_hysteresis_and_context_deduplication(
    risk_event: Envelope,
    path_event: Envelope,
    vehicle_state_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    center = _center(clock)
    high_context = _context(risk_event, path_event, vehicle_state_event)

    first = center.decide(high_context)
    assert center.decide(high_context) is first
    assert first.payload["action"] == "AVOID"

    low_risk_id = str(uuid7())
    low_risk = _payload(
        risk_event,
        risk_id=low_risk_id,
        score=10.0,
        level="LOW",
        dimensions={**risk_event.payload["dimensions"], "collision": 5.0},
    )
    low_path = _payload(path_event, path_id=str(uuid7()), risk_id=low_risk_id)
    hysteresis = center.decide(_context(low_risk, low_path, vehicle_state_event))
    assert hysteresis.payload["action"] == "AVOID"
    assert "RISK_RECOVERY_HYSTERESIS" in hysteresis.payload["reason_codes"]

    clock.advance(2.1)
    recovered_risk_id = str(uuid7())
    recovered_risk = _payload(low_risk, risk_id=recovered_risk_id)
    recovered_path = _payload(
        low_path,
        path_id=str(uuid7()),
        risk_id=recovered_risk_id,
    )
    fresh_vehicle = _payload(
        vehicle_state_event,
        updated_at=clock.now().isoformat().replace("+00:00", "Z"),
    )
    recovered = center.decide(
        _context(recovered_risk, recovered_path, fresh_vehicle)
    )
    assert recovered.payload["action"] == "CONTINUE"
    assert center.state == DecisionState.CONTINUING


def test_safety_gate_rejects_stale_path_and_real_endpoint_in_sim(
    risk_event: Envelope,
    path_event: Envelope,
    vehicle_state_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    proposal = _center(clock).decide(
        _context(risk_event, path_event, vehicle_state_event)
    )
    expired_path = _payload(
        path_event,
        valid_until=(clock.now() - timedelta(seconds=1))
        .isoformat()
        .replace("+00:00", "Z"),
    )

    expired_gate = SafetyGate(
        gate_id="gate-expired",
        config=DecisionConfig(),
        clock=clock,
        event_ids=DeterministicUuid7Factory(RandomStream(11, "expired-gate")),
    )
    rejected = expired_gate.authorize(
        proposal,
        SafetyContext(
            risk=risk_event,
            path=expired_path,
            vehicle_state=vehicle_state_event,
            endpoint_id="sim-flight-controller",
            endpoint_kind="SIMULATED",
        ),
    )
    assert rejected.payload["authorization"]["state"] == "REJECTED"
    assert "PATH_EXPIRED" in rejected.payload["authorization"]["failures"]

    real_gate = SafetyGate(
        gate_id="gate-real",
        config=DecisionConfig(),
        clock=clock,
        event_ids=DeterministicUuid7Factory(RandomStream(11, "real-gate")),
    )
    real_rejected = real_gate.authorize(
        proposal,
        SafetyContext(
            risk=risk_event,
            path=path_event,
            vehicle_state=vehicle_state_event,
            endpoint_id="real-flight-controller",
            endpoint_kind="REAL",
        ),
    )
    assert "REAL_ENDPOINT_FORBIDDEN_IN_MODE" in real_rejected.payload[
        "authorization"
    ]["failures"]


def test_authorization_and_simulated_gateway_are_idempotent(
    risk_event: Envelope,
    path_event: Envelope,
    vehicle_state_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    proposal = _center(clock).decide(
        _context(risk_event, path_event, vehicle_state_event)
    )
    gate = SafetyGate(
        gate_id="gate-01",
        config=DecisionConfig(),
        clock=clock,
        event_ids=DeterministicUuid7Factory(RandomStream(12, "gate")),
    )
    safety_context = SafetyContext(
        risk=risk_event,
        path=path_event,
        vehicle_state=vehicle_state_event,
        endpoint_id="sim-flight-controller",
        endpoint_kind="SIMULATED",
    )
    authorized = gate.authorize(proposal, safety_context)
    assert gate.authorize(proposal, safety_context) is authorized
    assert authorized.payload["authorization"]["state"] == "AUTHORIZED"
    reused = gate.authorize(replace(proposal, event_id=uuid7()), safety_context)
    assert reused.payload["authorization"]["state"] == "REJECTED"
    assert "DECISION_ID_REUSED" in reused.payload["authorization"]["failures"]

    gateway = SimulatedControlGateway(
        endpoint_id="sim-flight-controller",
        clock=clock,
        event_ids=DeterministicUuid7Factory(RandomStream(13, "gateway")),
    )
    first = gateway.dispatch(authorized)
    second = gateway.dispatch(authorized)
    assert first == second
    assert gateway.side_effect_count == 1
    assert first[0].payload["decision_id"] == authorized.payload["decision_id"]
    assert first[1].payload["command_id"] == first[0].payload["command_id"]
    assert first[1].payload["status"] == "COMPLETED"

    with pytest.raises(ControlDispatchRejected, match="not authorized"):
        gateway.dispatch(proposal)
    replay = replace(authorized, mode=RuntimeMode.REPLAY)
    replay_payload = dict(replay.payload)
    replay_payload["decision_id"] = str(uuid7())
    with pytest.raises(ControlDispatchRejected, match="only accepts SIM"):
        gateway.dispatch(replace(replay, payload=replay_payload))
