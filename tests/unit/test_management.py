from dataclasses import replace

from low_altitude_ai.domain import Envelope
from low_altitude_ai.management import (
    HealthAggregator,
    ManagementCommand,
    ManagementService,
    Role,
)
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


def _command(
    service: ManagementService,
    clock: SimClock,
    *,
    action: str,
    resource: str,
    key: str,
    actor: str,
    role: Role,
    payload: dict[str, object] | None = None,
) -> ManagementCommand:
    return ManagementCommand(
        action=action,
        resource=resource,
        idempotency_key=key,
        actor=actor,
        role=role,
        client_time=clock.now(),
        expected_revision=service.revision,
        reason="phase6 management test",
        payload=payload or {},
    )


def _service(clock: SimClock) -> ManagementService:
    return ManagementService(
        clock=clock,
        operation_ids=DeterministicUuid7Factory(RandomStream(61, "management")),
    )


def test_safety_config_requires_rbac_revision_and_two_approvers(
    risk_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    service = _service(clock)
    viewer = _command(
        service,
        clock,
        action="config.draft",
        resource="safety.flight",
        key="viewer-draft-001",
        actor="viewer",
        role=Role.VIEWER,
        payload={"values": {"minimum_separation_m": 15.0}},
    )
    assert service.execute(viewer).reason_code == "FORBIDDEN"

    draft = _command(
        service,
        clock,
        action="config.draft",
        resource="safety.flight",
        key="safety-draft-001",
        actor="engineer",
        role=Role.ENGINEER,
        payload={"values": {"minimum_separation_m": 15.0}},
    )
    draft_result = service.execute(draft)
    assert draft_result.status == "SUCCEEDED"
    assert service.execute(draft) is draft_result

    validate = _command(
        service,
        clock,
        action="config.validate",
        resource="safety.flight",
        key="safety-validate-001",
        actor="engineer",
        role=Role.ENGINEER,
    )
    assert service.execute(validate).status == "SUCCEEDED"
    approve_first = _command(
        service,
        clock,
        action="config.approve",
        resource="safety.flight",
        key="safety-approve-001",
        actor="approver-a",
        role=Role.SAFETY_APPROVER,
    )
    assert service.execute(approve_first).status == "SUCCEEDED"
    too_early = _command(
        service,
        clock,
        action="config.activate",
        resource="safety.flight",
        key="safety-activate-early",
        actor="approver-a",
        role=Role.SAFETY_APPROVER,
    )
    assert service.execute(too_early).reason_code == "INSUFFICIENT_APPROVALS"
    approve_second = _command(
        service,
        clock,
        action="config.approve",
        resource="safety.flight",
        key="safety-approve-002",
        actor="approver-b",
        role=Role.SAFETY_APPROVER,
    )
    assert service.execute(approve_second).status == "SUCCEEDED"
    activate = _command(
        service,
        clock,
        action="config.activate",
        resource="safety.flight",
        key="safety-activate-001",
        actor="approver-b",
        role=Role.SAFETY_APPROVER,
    )
    assert service.execute(activate).status == "SUCCEEDED"
    assert service.config_snapshots("safety.flight")[-1].state == "ACTIVE"
    assert len(service.audit_records) == 7


def test_management_rejects_key_reuse_plaintext_secret_and_unavailable_writes(
    risk_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    service = _service(clock)
    command = _command(
        service,
        clock,
        action="config.draft",
        resource="site.alpha",
        key="config-key-001",
        actor="engineer",
        role=Role.ENGINEER,
        payload={"values": {"refresh_hz": 10}},
    )
    assert service.execute(command).status == "SUCCEEDED"
    conflict = replace(
        command,
        payload={"values": {"refresh_hz": 20}},
        expected_revision=service.revision,
    )
    assert service.execute(conflict).reason_code == "IDEMPOTENCY_KEY_REUSED"

    plaintext = _command(
        service,
        clock,
        action="config.draft",
        resource="site.secrets",
        key="plaintext-secret-001",
        actor="engineer",
        role=Role.ENGINEER,
        payload={"values": {"api_secret": "not-a-reference"}},
    )
    assert service.execute(plaintext).reason_code == "PLAINTEXT_SECRET_FORBIDDEN"

    service.set_available(False)
    unavailable = _command(
        service,
        clock,
        action="mission.pause",
        resource="mission-001",
        key="mission-pause-001",
        actor="operator",
        role=Role.OPERATOR,
    )
    assert service.execute(unavailable).reason_code == "MANAGEMENT_UNAVAILABLE"


def test_plugin_gate_alert_ack_and_health_unknown_propagation(
    risk_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    service = _service(clock)
    artifact_hash = "sha256:" + "1" * 64
    register = _command(
        service,
        clock,
        action="plugin.register",
        resource="risk/baseline",
        key="plugin-register-001",
        actor="engineer",
        role=Role.ENGINEER,
        payload={
            "name": "baseline-risk",
            "version": "1.0.0",
            "artifact_hash": artifact_hash,
            "signature_valid": True,
            "compatible": True,
            "input_schema": "twin.snapshot/1.0",
            "output_schema": "risk/1.0",
        },
    )
    assert service.execute(register).status == "SUCCEEDED"
    shadow = _command(
        service,
        clock,
        action="plugin.shadow",
        resource="risk/baseline",
        key="plugin-shadow-001",
        actor="engineer",
        role=Role.ENGINEER,
        payload={"name": "baseline-risk", "version": "1.0.0"},
    )
    assert service.execute(shadow).details["state"] == "SHADOW"
    activate = _command(
        service,
        clock,
        action="plugin.activate",
        resource="risk/baseline",
        key="plugin-activate-001",
        actor="approver",
        role=Role.SAFETY_APPROVER,
        payload={"name": "baseline-risk", "version": "1.0.0"},
    )
    assert service.execute(activate).details["state"] == "ACTIVE"

    first_alert = service.upsert_alert(
        dedup_key="risk-service-down",
        severity="CRITICAL",
        summary="Risk service heartbeat missing.",
    )
    second_alert = service.upsert_alert(
        dedup_key="risk-service-down",
        severity="CRITICAL",
        summary="Risk service heartbeat still missing.",
    )
    assert second_alert.count == first_alert.count + 1
    acknowledge = _command(
        service,
        clock,
        action="alert.ack",
        resource="risk-service-down",
        key="alert-ack-001",
        actor="operator",
        role=Role.OPERATOR,
    )
    assert service.execute(acknowledge).reason_code == "ALERT_ACKNOWLEDGED"
    assert service.alerts()[0].acknowledged_by == "operator"

    health = HealthAggregator()
    health_event = replace(
        risk_event,
        schema="health/1.0",
        payload={
            "component_id": "risk-service",
            "component_type": "SERVICE",
            "status": "HEALTHY",
            "checked_at": risk_event.received_at.isoformat().replace("+00:00", "Z"),
            "dependencies": [],
            "data_freshness_ms": 0.0,
            "faults": [],
        },
    )
    health.ingest(health_event)
    assert health.snapshot(clock.now()).status == "HEALTHY"
    clock.advance(2.1)
    stale = health.snapshot(clock.now())
    assert stale.status == "UNHEALTHY"
    assert stale.components[0].status == "UNKNOWN"
