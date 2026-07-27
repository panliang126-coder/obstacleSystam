"""Transport-injected MAVLink HIL mapping with no socket or serial ownership."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.hil import HilPermit
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.ports.flight_control import (
    ControlSetpoint,
    FlightControllerDescriptor,
)
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory

Vector3 = tuple[float, float, float]


class MavlinkTransport(Protocol):
    async def connect(self) -> None: ...

    async def send_action(
        self,
        *,
        action: str,
        setpoints_ned_m: tuple[Vector3, ...],
        deadline: datetime,
    ) -> str: ...

    async def close(self) -> None: ...


def enu_to_ned(value: Vector3) -> Vector3:
    return value[1], value[0], -value[2]


class MavlinkHilAdapter:
    """HIL-only adapter. A vendor transport must be injected after bench approval."""

    def __init__(
        self,
        *,
        descriptor: FlightControllerDescriptor,
        permit: HilPermit,
        transport: MavlinkTransport,
        clock: ClockPort,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        if descriptor.native_frame != "NED":
            raise ValueError("MAVLink adapter requires a NED-native descriptor")
        if descriptor.endpoint_id != permit.endpoint_id:
            raise ValueError("permit and descriptor endpoint mismatch")
        self._descriptor = descriptor
        self._permit = permit
        self._transport = transport
        self._clock = clock
        self._event_ids = event_ids
        self._connected = False
        self._sequence = 0
        self._acks: dict[str, Envelope] = {}

    @property
    def descriptor(self) -> FlightControllerDescriptor:
        return self._descriptor

    async def connect(self) -> None:
        if self._permit.expires_at <= self._clock.now():
            raise RuntimeError("HIL permit has expired")
        await self._transport.connect()
        self._connected = True

    async def execute(
        self,
        command: Envelope,
        setpoints: tuple[ControlSetpoint, ...],
    ) -> Envelope:
        if not self._connected:
            raise RuntimeError("flight-controller adapter is not connected")
        command_id = str(command.payload.get("command_id", "missing"))
        cached = self._acks.get(command_id)
        if cached is not None:
            return cached
        now = self._clock.now()
        if command.schema != "control.command/1.0" or not command.quality.valid:
            raise ValueError("invalid control command")
        if command.mode != RuntimeMode.HIL:
            raise ValueError("MAVLink HIL adapter only accepts HIL commands")
        if command.payload.get("endpoint_kind") != "REAL":
            raise ValueError("HIL command must bind a REAL bench endpoint")
        if command.payload.get("endpoint_id") != self._descriptor.endpoint_id:
            raise ValueError("control endpoint mismatch")
        if command.payload.get("authorization_token_hash") != self._permit.token_hash:
            raise ValueError("HIL permit token mismatch")
        deadline = datetime.fromisoformat(
            str(command.payload["deadline"]).replace("Z", "+00:00")
        )
        if deadline <= now or self._permit.expires_at <= now:
            raise ValueError("control command or HIL permit has expired")
        action = str(command.payload["action"])
        if action in {"CONTINUE", "AVOID", "RETURN", "LAND"} and not setpoints:
            raise ValueError("path action requires at least one setpoint")
        status = await self._transport.send_action(
            action=action,
            setpoints_ned_m=tuple(
                enu_to_ned(setpoint.position_enu_m) for setpoint in setpoints
            ),
            deadline=deadline,
        )
        if status not in {"ACCEPTED", "EXECUTING", "COMPLETED", "REJECTED", "TIMEOUT"}:
            status = "REJECTED"
        ack_id = self._event_ids.new(now)
        ack = Envelope(
            schema="control.ack/1.0",
            event_id=self._event_ids.new(now),
            trace_id=command.trace_id,
            causation_id=command.event_id,
            source=Source(
                service="control-gateway",
                instance_id=self._descriptor.endpoint_id,
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=command.run_id,
            mode=RuntimeMode.HIL,
            vehicle_id=command.vehicle_id,
            sequence=self._sequence,
            quality=Quality(valid=status != "REJECTED", confidence=1.0),
            payload={
                "ack_id": str(ack_id),
                "command_id": command_id,
                "decision_id": command.payload["decision_id"],
                "status": status,
                "endpoint_id": self._descriptor.endpoint_id,
                "idempotency_key": command.payload["idempotency_key"],
                "acknowledged_at": now.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "side_effect_applied": status in {"ACCEPTED", "EXECUTING", "COMPLETED"},
                "attempt": 1,
                "detail": f"MAVLink bench transport returned {status}.",
            },
        )
        self._sequence += 1
        self._acks[command_id] = ack
        return ack

    async def close(self) -> None:
        if self._connected:
            await self._transport.close()
        self._connected = False
