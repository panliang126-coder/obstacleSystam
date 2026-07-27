"""Idempotent simulated control gateway for SIL-only command execution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory


class ControlDispatchRejected(RuntimeError):
    """An unauthorized or unsafe decision was presented to the gateway."""


def _parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class SimulatedControlGateway:
    """Execute authorized SIM decisions once and emit traceable command/Ack events."""

    def __init__(
        self,
        *,
        endpoint_id: str,
        clock: ClockPort,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        if not endpoint_id.strip():
            raise ValueError("endpoint_id must be non-empty")
        self._endpoint_id = endpoint_id
        self._clock = clock
        self._event_ids = event_ids
        self._sequence = 0
        self._cache: dict[str, tuple[UUID, Envelope, Envelope]] = {}
        self._side_effect_count = 0

    @property
    def side_effect_count(self) -> int:
        return self._side_effect_count

    def dispatch(self, decision: Envelope) -> tuple[Envelope, Envelope]:
        decision_id = str(decision.payload.get("decision_id", "missing"))
        now = self._clock.now()
        self._validate_authorization(decision)
        cached = self._cache.get(decision_id)
        if cached is not None:
            if cached[0] != decision.event_id:
                raise ControlDispatchRejected("decision id was reused by another event")
            return cached[1], cached[2]
        self._validate_expiry(decision, now)
        command_id = self._event_ids.new(now)
        command_event_id = self._event_ids.new(now)
        idempotency_key = f"decision:{decision_id}"
        authorization = decision.payload["authorization"]
        token_material = json.dumps(
            {
                "authorization": authorization,
                "decision_id": decision_id,
                "endpoint_id": self._endpoint_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        token_hash = "sha256:" + hashlib.sha256(token_material).hexdigest()
        command = Envelope(
            schema="control.command/1.0",
            event_id=command_event_id,
            trace_id=decision.trace_id,
            causation_id=decision.event_id,
            source=Source(service="safety-gate", instance_id=str(authorization["gate"])),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=decision.run_id,
            mode=decision.mode,
            vehicle_id=decision.vehicle_id,
            sequence=self._sequence,
            quality=Quality(valid=True, confidence=1.0),
            payload={
                "command_id": str(command_id),
                "decision_id": decision_id,
                "mission_id": decision.payload["mission_id"],
                "action": decision.payload["action"],
                "path_id": decision.payload["path_id"],
                "endpoint_id": self._endpoint_id,
                "endpoint_kind": "SIMULATED",
                "idempotency_key": idempotency_key,
                "deadline": decision.payload["expires_at"],
                "authorization_token_hash": token_hash,
                "issued_at": now.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
            },
        )
        self._side_effect_count += 1
        ack_id = self._event_ids.new(now)
        ack = Envelope(
            schema="control.ack/1.0",
            event_id=self._event_ids.new(now),
            trace_id=decision.trace_id,
            causation_id=command.event_id,
            source=Source(service="control-gateway", instance_id=self._endpoint_id),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=decision.run_id,
            mode=decision.mode,
            vehicle_id=decision.vehicle_id,
            sequence=self._sequence,
            quality=Quality(valid=True, confidence=1.0),
            payload={
                "ack_id": str(ack_id),
                "command_id": str(command_id),
                "decision_id": decision_id,
                "status": "COMPLETED",
                "endpoint_id": self._endpoint_id,
                "idempotency_key": idempotency_key,
                "acknowledged_at": now.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "side_effect_applied": True,
                "attempt": 1,
                "detail": "Simulated action completed.",
            },
        )
        self._sequence += 1
        result = (command, ack)
        self._cache[decision_id] = (decision.event_id, command, ack)
        return result

    @staticmethod
    def _validate_authorization(decision: Envelope) -> None:
        if decision.schema != "decision/1.0" or not decision.quality.valid:
            raise ControlDispatchRejected("decision is invalid")
        if decision.mode != RuntimeMode.SIM:
            raise ControlDispatchRejected("simulated gateway only accepts SIM decisions")
        authorization = decision.payload.get("authorization")
        if not isinstance(authorization, dict) or authorization.get("state") != "AUTHORIZED":
            raise ControlDispatchRejected("decision is not authorized")
        if not authorization.get("gate"):
            raise ControlDispatchRejected("authorization gate identity is missing")

    @staticmethod
    def _validate_expiry(decision: Envelope, now: datetime) -> None:
        if _parse_time(decision.payload.get("expires_at")) <= now:
            raise ControlDispatchRejected("decision has expired")
