import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from low_altitude_ai.adapters.flight_control import MavlinkHilAdapter
from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.domain.identifiers import uuid7
from low_altitude_ai.hil import HilEvidence, HilReadinessGate
from low_altitude_ai.ports.flight_control import (
    ControlSetpoint,
    FlightControllerDescriptor,
)
from low_altitude_ai.schemas.registry import SchemaRegistry
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


class FakeMavlinkTransport:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.calls: list[tuple[str, tuple[tuple[float, float, float], ...]]] = []

    async def connect(self) -> None:
        self.connected = True

    async def send_action(
        self,
        *,
        action: str,
        setpoints_ned_m: tuple[tuple[float, float, float], ...],
        deadline: datetime,
    ) -> str:
        del deadline
        self.calls.append((action, setpoints_ned_m))
        return "ACCEPTED"

    async def close(self) -> None:
        self.closed = True


def _evidence(now: datetime) -> HilEvidence:
    return HilEvidence(
        mode=RuntimeMode.HIL,
        endpoint_id="hil-bench-px4-01",
        endpoint_kind="REAL",
        hardware_id="px4-fmu-test-01",
        firmware_hash="sha256:" + "1" * 64,
        calibration_hash="sha256:" + "2" * 64,
        emergency_stop_verified=True,
        propellers_removed=True,
        network_isolated=True,
        native_failsafe_verified=True,
        rollback_plan_verified=True,
        time_sync_offset_ms=1.0,
        operator="operator-a",
        safety_approver="approver-b",
        approved_until=now + timedelta(minutes=5),
    )


def _command(risk_event: Envelope, token_hash: str) -> Envelope:
    now = risk_event.received_at
    return Envelope(
        schema="control.command/1.0",
        event_id=uuid7(),
        trace_id=risk_event.trace_id,
        causation_id=risk_event.event_id,
        source=Source(service="safety-gate", instance_id="hil-gate"),
        observed_at=now,
        received_at=now,
        monotonic_ns=0,
        run_id=risk_event.run_id,
        mode=RuntimeMode.HIL,
        vehicle_id="uav-001",
        sequence=0,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "command_id": str(uuid7()),
            "decision_id": str(uuid7()),
            "mission_id": "hil-contract",
            "action": "AVOID",
            "path_id": str(uuid7()),
            "endpoint_id": "hil-bench-px4-01",
            "endpoint_kind": "REAL",
            "idempotency_key": "hil-command-001",
            "deadline": (now + timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
            "authorization_token_hash": token_hash,
            "issued_at": now.isoformat().replace("+00:00", "Z"),
        },
    )


@pytest.mark.contract
def test_hil_readiness_fails_closed_and_never_accepts_live(
    risk_event: Envelope,
) -> None:
    now = risk_event.received_at
    gate = HilReadinessGate()
    valid = _evidence(now)
    assert gate.evaluate(valid, now).ready
    permit = gate.issue_permit(valid, now)
    assert permit.endpoint_id == valid.endpoint_id

    unsafe = replace(
        valid,
        mode=RuntimeMode.LIVE,
        emergency_stop_verified=False,
        operator="same-person",
        safety_approver="same-person",
        approved_until=now - timedelta(seconds=1),
    )
    report = gate.evaluate(unsafe, now)
    assert not report.ready
    assert {
        "MODE_NOT_HIL",
        "EMERGENCY_STOP_NOT_VERIFIED",
        "INDEPENDENT_APPROVAL_MISSING",
        "HIL_APPROVAL_EXPIRED",
    }.issubset(report.blockers)
    with pytest.raises(ValueError, match="HIL readiness blockers"):
        gate.issue_permit(unsafe, now)


@pytest.mark.contract
def test_mavlink_hil_adapter_maps_enu_to_ned_and_is_idempotent(
    risk_event: Envelope,
    schema_dir: Path,
) -> None:
    async def exercise() -> tuple[Envelope, FakeMavlinkTransport]:
        clock = SimClock(risk_event.received_at)
        permit = HilReadinessGate().issue_permit(
            _evidence(clock.now()),
            clock.now(),
        )
        transport = FakeMavlinkTransport()
        adapter = MavlinkHilAdapter(
            descriptor=FlightControllerDescriptor(
                endpoint_id=permit.endpoint_id,
                vendor="PX4",
                transport="MAVLink",
                native_frame="NED",
            ),
            permit=permit,
            transport=transport,
            clock=clock,
            event_ids=DeterministicUuid7Factory(RandomStream(71, "mavlink-hil")),
        )
        await adapter.connect()
        command = _command(risk_event, permit.token_hash)
        setpoints = (
            ControlSetpoint((10.0, 20.0, 30.0), 5.0),
            ControlSetpoint((40.0, 50.0, 60.0), 6.0),
        )
        first = await adapter.execute(command, setpoints)
        assert await adapter.execute(command, setpoints) is first
        invalid_payload = dict(command.payload)
        invalid_payload["command_id"] = str(uuid7())
        invalid = replace(
            command,
            event_id=uuid7(),
            mode=RuntimeMode.SIM,
            payload=invalid_payload,
        )
        with pytest.raises(ValueError, match="only accepts HIL"):
            await adapter.execute(invalid, setpoints)
        await adapter.close()
        return first, transport

    ack, transport = asyncio.run(exercise())
    assert transport.connected and transport.closed
    assert transport.calls == [
        (
            "AVOID",
            ((20.0, 10.0, -30.0), (50.0, 40.0, -60.0)),
        )
    ]
    assert ack.payload["status"] == "ACCEPTED"
    assert ack.payload["side_effect_applied"]
    SchemaRegistry(schema_dir).validate(ack.to_mapping())
