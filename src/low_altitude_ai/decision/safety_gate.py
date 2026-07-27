"""Independent fail-closed authorization gate for proposed decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from low_altitude_ai.decision.model import DecisionConfig, SafetyContext
from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory


def _parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class SafetyGate:
    """Minimal rule-only gate; every exception becomes a rejection."""

    def __init__(
        self,
        *,
        gate_id: str,
        config: DecisionConfig,
        clock: ClockPort,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        if not gate_id.strip():
            raise ValueError("gate_id must be non-empty")
        self._gate_id = gate_id
        self._config = config
        self._clock = clock
        self._event_ids = event_ids
        self._sequence = 0
        self._cache: dict[str, tuple[UUID, Envelope]] = {}

    def authorize(self, proposal: Envelope, context: SafetyContext) -> Envelope:
        decision_id = str(proposal.payload.get("decision_id", "missing"))
        cached = self._cache.get(decision_id)
        if cached is not None:
            if cached[0] == proposal.event_id:
                return cached[1]
            return self._output(proposal, ["DECISION_ID_REUSED"])
        now = self._clock.now()
        try:
            failures = self._failures(proposal, context, now)
        except (KeyError, TypeError, ValueError, OverflowError):
            failures = ["SAFETY_GATE_INTERNAL_VALIDATION_ERROR"]
        output = self._output(proposal, failures)
        self._cache[decision_id] = (proposal.event_id, output)
        return output

    def _output(self, proposal: Envelope, failures: list[str]) -> Envelope:
        now = self._clock.now()
        state = "REJECTED" if failures else "AUTHORIZED"
        payload = dict(proposal.payload)
        payload["authorization"] = {
            "state": state,
            "gate": self._gate_id,
            "checked_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "failures": failures,
        }
        output = Envelope(
            schema="decision/1.0",
            event_id=self._event_ids.new(now),
            trace_id=proposal.trace_id,
            causation_id=proposal.event_id,
            source=Source(service="safety-gate", instance_id=self._gate_id),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=proposal.run_id,
            mode=proposal.mode,
            vehicle_id=proposal.vehicle_id,
            sequence=self._sequence,
            quality=Quality(
                valid=not failures,
                confidence=1.0 if not failures else 0.0,
                flags=() if not failures else ("SAFETY_GATE_REJECTED",),
            ),
            payload=payload,
        )
        self._sequence += 1
        return output

    def _failures(
        self,
        proposal: Envelope,
        context: SafetyContext,
        now: datetime,
    ) -> list[str]:
        failures: list[str] = []
        if proposal.schema != "decision/1.0" or not proposal.quality.valid:
            failures.append("DECISION_INVALID")
        authorization = proposal.payload.get("authorization")
        if not isinstance(authorization, dict) or authorization.get("state") != "PENDING":
            failures.append("DECISION_NOT_PENDING")
        if _parse_time(proposal.payload["expires_at"]) <= now:
            failures.append("DECISION_EXPIRED")
        if proposal.vehicle_id != context.vehicle_state.vehicle_id:
            failures.append("VEHICLE_BINDING_MISMATCH")
        if context.endpoint_kind == "REAL" and proposal.mode in {
            RuntimeMode.SIM,
            RuntimeMode.REPLAY,
        }:
            failures.append("REAL_ENDPOINT_FORBIDDEN_IN_MODE")
        if context.vehicle_state.schema != "vehicle.state/1.0":
            failures.append("VEHICLE_STATE_INVALID")
        if not context.vehicle_state.quality.valid:
            failures.append("VEHICLE_STATE_QUALITY_INVALID")
        vehicle_age_ms = (
            now
            - _parse_time(
                context.vehicle_state.payload.get(
                    "updated_at",
                    context.vehicle_state.observed_at,
                )
            )
        ).total_seconds() * 1_000
        if vehicle_age_ms > self._config.vehicle_stale_ms:
            failures.append("VEHICLE_STATE_STALE")
        link = context.vehicle_state.payload.get("link")
        if not isinstance(link, dict) or not bool(link.get("healthy")):
            failures.append("CONTROL_LINK_UNHEALTHY")
        if bool(context.vehicle_state.payload.get("failsafe")):
            failures.append("VEHICLE_FAILSAFE_ACTIVE")

        risk = context.risk
        if risk.schema != "risk/1.0" or not risk.quality.valid:
            failures.append("RISK_INVALID")
        if _parse_time(risk.payload["valid_until"]) <= now:
            failures.append("RISK_EXPIRED")
        if str(proposal.payload.get("risk_id")) != str(risk.payload.get("risk_id")):
            failures.append("RISK_ID_MISMATCH")
        if int(proposal.payload.get("twin_revision", -1)) != int(
            risk.payload.get("twin_revision", -2)
        ):
            failures.append("RISK_REVISION_MISMATCH")
        if int(context.vehicle_state.payload.get("twin_revision", -1)) != int(
            proposal.payload.get("twin_revision", -2)
        ):
            failures.append("VEHICLE_REVISION_MISMATCH")

        action = str(proposal.payload.get("action"))
        capabilities = context.vehicle_state.payload.get("capabilities")
        if not isinstance(capabilities, list) or action not in capabilities:
            failures.append("ACTION_NOT_SUPPORTED")
        path_id = proposal.payload.get("path_id")
        if action in {"CONTINUE", "AVOID", "RETURN", "LAND"} and path_id is None:
            failures.append("ACTION_PATH_REQUIRED")
        if path_id is not None:
            failures.extend(self._path_failures(path_id, proposal, context, now))
        return list(dict.fromkeys(failures))

    @staticmethod
    def _path_failures(
        path_id: Any,
        proposal: Envelope,
        context: SafetyContext,
        now: datetime,
    ) -> list[str]:
        path = context.path
        if path is None or path.schema != "path/1.0" or not path.quality.valid:
            return ["PATH_INVALID"]
        failures: list[str] = []
        payload = path.payload
        if str(path_id) != str(payload.get("path_id")):
            failures.append("PATH_ID_MISMATCH")
        if _parse_time(payload["valid_until"]) <= now:
            failures.append("PATH_EXPIRED")
        if int(payload.get("twin_revision", -1)) != int(
            proposal.payload.get("twin_revision", -2)
        ):
            failures.append("PATH_REVISION_MISMATCH")
        if str(payload.get("risk_id")) != str(proposal.payload.get("risk_id")):
            failures.append("PATH_RISK_MISMATCH")
        validation = payload.get("validation")
        if not isinstance(validation, dict) or not all(
            bool(validation.get(key))
            for key in ("collision_free", "dynamics_feasible", "geofence_valid")
        ):
            failures.append("PATH_VALIDATION_FAILED")
        if payload.get("status") not in {"CANDIDATE", "SELECTED"}:
            failures.append("PATH_STATUS_INVALID")
        return failures
