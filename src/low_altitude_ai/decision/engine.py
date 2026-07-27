"""Explainable fail-closed decision policy and safety state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from low_altitude_ai.decision.model import (
    DecisionConfig,
    DecisionContext,
    DecisionState,
    DecisionTransition,
)
from low_altitude_ai.domain import Envelope, Quality, Source
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory


@dataclass(frozen=True, slots=True)
class _Outcome:
    action: str
    priority: int
    reasons: tuple[str, ...]
    explanation: str
    use_path: bool


_TARGET_STATE = {
    "CONTINUE": DecisionState.CONTINUING,
    "AVOID": DecisionState.AVOIDING,
    "HOLD": DecisionState.HOLDING,
    "RETURN": DecisionState.RETURNING,
    "LAND": DecisionState.LANDING,
    "ABORT": DecisionState.ABORTING,
}


def _parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class DecisionCenter:
    """Rule baseline that cannot be weakened by learned policy candidates."""

    def __init__(
        self,
        *,
        config: DecisionConfig,
        clock: ClockPort,
        event_ids: DeterministicUuid7Factory,
    ) -> None:
        self._config = config
        self._clock = clock
        self._event_ids = event_ids
        self._state = DecisionState.READY
        self._sequence = 0
        self._last_unsafe_at: datetime | None = None
        self._cache: dict[tuple[int, str, str | None, str], Envelope] = {}
        self._transitions: list[DecisionTransition] = []

    @property
    def state(self) -> DecisionState:
        return self._state

    @property
    def transitions(self) -> tuple[DecisionTransition, ...]:
        return tuple(self._transitions)

    def decide(self, context: DecisionContext) -> Envelope:
        now = self._clock.now()
        risk_id = str(context.risk.payload.get("risk_id", "missing"))
        path_id = (
            str(context.path.payload.get("path_id", "missing"))
            if context.path is not None
            else None
        )
        twin_revision = int(context.risk.payload.get("twin_revision", -1))
        cache_key = (
            twin_revision,
            risk_id,
            path_id,
            self._config.ruleset_hash,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        path_valid = self._path_valid(context, now)
        outcome = self._evaluate(context, now, path_valid)
        if outcome.action in {"AVOID", "HOLD", "RETURN", "LAND", "ABORT"}:
            self._last_unsafe_at = now
        outcome = self._apply_recovery_hysteresis(outcome, context, now, path_valid)
        outcome = self._enforce_capability(outcome, context)
        target_state = _TARGET_STATE[outcome.action]
        if target_state != self._state:
            self._transitions.append(
                DecisionTransition(
                    previous=self._state,
                    current=target_state,
                    at=now,
                    reason_codes=outcome.reasons,
                )
            )
            self._state = target_state

        decision_id = self._event_ids.new(now)
        event_id = self._event_ids.new(now)
        selected_path_id = path_id if outcome.use_path and path_valid else None
        validity = [now + timedelta(seconds=self._config.decision_valid_for_s)]
        if context.risk.schema == "risk/1.0" and "valid_until" in context.risk.payload:
            validity.append(_parse_time(context.risk.payload["valid_until"]))
        if selected_path_id is not None and context.path is not None:
            validity.append(_parse_time(context.path.payload["valid_until"]))
        expires_at = min(validity)
        preconditions = [
            f"vehicle_state_age_ms<={self._config.vehicle_stale_ms:g}",
            "risk_revision_matches=true",
            "control_link=healthy",
        ]
        if selected_path_id is not None:
            preconditions.append("path_valid=true")
        proposal = Envelope(
            schema="decision/1.0",
            event_id=event_id,
            trace_id=context.risk.trace_id,
            causation_id=(
                context.path.event_id
                if selected_path_id and context.path
                else context.risk.event_id
            ),
            source=Source(
                service="decision-service",
                instance_id="baseline-decision-center",
                plugin=self._config.policy_name,
                plugin_version=self._config.policy_version,
            ),
            observed_at=now,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=context.risk.run_id,
            mode=context.risk.mode,
            vehicle_id=context.vehicle_state.vehicle_id,
            sequence=self._sequence,
            quality=Quality(valid=True, confidence=1.0),
            payload={
                "decision_id": str(decision_id),
                "mission_id": context.mission_id,
                "action": outcome.action,
                "priority": outcome.priority,
                "path_id": selected_path_id,
                "risk_id": risk_id,
                "twin_revision": twin_revision,
                "reason_codes": list(outcome.reasons),
                "explanation": outcome.explanation,
                "preconditions": preconditions,
                "expires_at": expires_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "policy": {
                    "name": self._config.policy_name,
                    "version": self._config.policy_version,
                    "ruleset_hash": self._config.ruleset_hash,
                },
                "authorization": {
                    "state": "PENDING",
                    "gate": None,
                    "checked_at": None,
                    "failures": [],
                },
            },
        )
        self._sequence += 1
        self._cache[cache_key] = proposal
        return proposal

    def _evaluate(
        self,
        context: DecisionContext,
        now: datetime,
        path_valid: bool,
    ) -> _Outcome:
        vehicle = context.vehicle_state
        vehicle_payload = vehicle.payload
        if vehicle.schema != "vehicle.state/1.0" or not vehicle.quality.valid:
            return self._abort("VEHICLE_STATE_INVALID", "车辆状态无效,触发飞控安全停机。")
        if bool(vehicle_payload.get("failsafe", False)):
            return self._abort("VEHICLE_FAILSAFE_ACTIVE", "飞控 failsafe 已激活,停止新任务控制。")
        if self._vehicle_age_ms(vehicle, now) > self._config.vehicle_stale_ms:
            return self._minimum_risk_stop(context, "VEHICLE_STATE_STALE")
        risk = context.risk
        if risk.schema != "risk/1.0" or not risk.quality.valid:
            return self._minimum_risk_stop(context, "RISK_UNKNOWN")
        if _parse_time(risk.payload.get("valid_until")) <= now:
            return self._minimum_risk_stop(context, "RISK_EXPIRED")
        if int(vehicle_payload.get("twin_revision", -1)) != int(
            risk.payload.get("twin_revision", -2)
        ):
            return self._minimum_risk_stop(context, "TWIN_REVISION_MISMATCH")

        battery_pct = float(vehicle_payload.get("battery_pct", 0.0))
        if battery_pct < context.mission_required_battery_pct:
            if bool(vehicle_payload.get("return_feasible")) and (
                battery_pct >= context.return_required_battery_pct
            ):
                return _Outcome(
                    "RETURN",
                    90,
                    ("MISSION_ENERGY_INSUFFICIENT", "RETURN_FEASIBLE"),
                    "任务剩余能源不足,但返航能源和路径可行,执行返航。",
                    path_valid,
                )
            if bool(vehicle_payload.get("landing_feasible")):
                return _Outcome(
                    "LAND",
                    95,
                    ("RETURN_ENERGY_INSUFFICIENT", "SAFE_LANDING_REACHABLE"),
                    "能源不足以返航,选择可达安全备降点。",
                    path_valid,
                )
            return self._abort("ENERGY_EXHAUSTION_IMMINENT", "无足够能源返航或安全备降。")

        link = vehicle_payload.get("link")
        link_healthy = isinstance(link, dict) and bool(link.get("healthy"))
        if not link_healthy:
            return self._link_loss(context, path_valid)

        risk_level = str(risk.payload.get("level", "UNKNOWN"))
        collision_score = float(
            self._dimensions(risk.payload).get(
                "collision",
                risk.payload.get("score", 100.0),
            )
        )
        if risk_level in {"HIGH", "CRITICAL"} or collision_score >= 55.0:
            if path_valid:
                return _Outcome(
                    "AVOID",
                    85 if risk_level == "HIGH" else 92,
                    ("COLLISION_RISK_HIGH", "ALTERNATE_PATH_VALID"),
                    "碰撞风险升高,执行已验证的局部避障路径。",
                    True,
                )
            return self._minimum_risk_stop(context, "NO_VALID_AVOIDANCE_PATH")
        if not path_valid:
            return self._minimum_risk_stop(context, "PATH_INVALID")
        return _Outcome(
            "CONTINUE",
            30,
            ("RISK_WITHIN_ENVELOPE", "MISSION_PATH_VALID"),
            "风险和系统状态在任务包线内,继续已验证路径。",
            True,
        )

    def _link_loss(self, context: DecisionContext, path_valid: bool) -> _Outcome:
        vehicle = context.vehicle_state.payload
        if context.link_loss_action == "RETURN" and bool(vehicle.get("return_feasible")):
            return _Outcome(
                "RETURN",
                90,
                ("CONTROL_LINK_LOST", "LINK_LOSS_POLICY_RETURN"),
                "控制链路失效,按任务预配置执行返航策略。",
                path_valid,
            )
        if context.link_loss_action == "LAND" and bool(vehicle.get("landing_feasible")):
            return _Outcome(
                "LAND",
                95,
                ("CONTROL_LINK_LOST", "LINK_LOSS_POLICY_LAND"),
                "控制链路失效,按任务预配置执行安全着陆。",
                path_valid,
            )
        return self._minimum_risk_stop(context, "CONTROL_LINK_LOST")

    def _minimum_risk_stop(self, context: DecisionContext, reason: str) -> _Outcome:
        vehicle = context.vehicle_state.payload
        if bool(vehicle.get("safe_to_hold")):
            return _Outcome(
                "HOLD",
                88,
                (reason, "SAFE_HOLD_AVAILABLE"),
                "关键决策输入不可用,停止扩大飞行包线并安全悬停。",
                False,
            )
        if bool(vehicle.get("landing_feasible")):
            return _Outcome(
                "LAND",
                95,
                (reason, "SAFE_LANDING_REACHABLE"),
                "无法安全悬停,转入可达安全备降点。",
                False,
            )
        return self._abort(reason, "无安全悬停或备降方案,触发飞控 failsafe。")

    @staticmethod
    def _abort(reason: str, explanation: str) -> _Outcome:
        return _Outcome(
            "ABORT",
            100,
            (reason, "UNRECOVERABLE_SAFETY_CONDITION"),
            explanation,
            False,
        )

    def _apply_recovery_hysteresis(
        self,
        outcome: _Outcome,
        context: DecisionContext,
        now: datetime,
        path_valid: bool,
    ) -> _Outcome:
        if (
            outcome.action == "CONTINUE"
            and self._state == DecisionState.AVOIDING
            and self._last_unsafe_at is not None
            and (now - self._last_unsafe_at).total_seconds() < self._config.recovery_hold_s
            and path_valid
        ):
            return _Outcome(
                "AVOID",
                80,
                ("RISK_RECOVERY_HYSTERESIS", "ALTERNATE_PATH_VALID"),
                "风险刚恢复,保持避障路径直至稳定窗口结束。",
                True,
            )
        return outcome

    def _enforce_capability(
        self,
        outcome: _Outcome,
        context: DecisionContext,
    ) -> _Outcome:
        capabilities = context.vehicle_state.payload.get("capabilities")
        if isinstance(capabilities, list) and outcome.action in capabilities:
            return outcome
        if isinstance(capabilities, list) and "HOLD" in capabilities and bool(
            context.vehicle_state.payload.get("safe_to_hold")
        ):
            return _Outcome(
                "HOLD",
                max(outcome.priority, 88),
                (*outcome.reasons, "ACTION_NOT_SUPPORTED"),
                "车辆不支持首选动作,保守降级为安全悬停。",
                False,
            )
        return self._abort("ACTION_NOT_SUPPORTED", "车辆不支持所需安全动作。")

    def _path_valid(self, context: DecisionContext, now: datetime) -> bool:
        path = context.path
        if path is None or path.schema != "path/1.0" or not path.quality.valid:
            return False
        payload = path.payload
        validation = payload.get("validation")
        if not isinstance(validation, dict):
            return False
        return (
            payload.get("status") in {"CANDIDATE", "SELECTED"}
            and bool(validation.get("collision_free"))
            and bool(validation.get("dynamics_feasible"))
            and bool(validation.get("geofence_valid"))
            and _parse_time(payload.get("valid_until")) > now
            and int(payload.get("twin_revision", -1))
            == int(context.risk.payload.get("twin_revision", -2))
            and str(payload.get("risk_id")) == str(context.risk.payload.get("risk_id"))
        )

    @staticmethod
    def _vehicle_age_ms(vehicle: Envelope, now: datetime) -> float:
        updated_at = _parse_time(vehicle.payload.get("updated_at", vehicle.observed_at))
        return max(0.0, (now - updated_at).total_seconds() * 1_000)

    @staticmethod
    def _dimensions(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        dimensions = payload.get("dimensions")
        return dimensions if isinstance(dimensions, dict) else {}
