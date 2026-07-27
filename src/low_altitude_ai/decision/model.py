"""Decision-center context, configuration, and state types."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from low_altitude_ai.domain import Envelope

RULESET_HASH = "sha256:" + hashlib.sha256(
    b"baseline-safety-policy/1.0.0:fail-closed:phase5"
).hexdigest()


class DecisionState(str, Enum):  # noqa: UP042 - Python 3.10 host compatibility
    IDLE = "IDLE"
    READY = "READY"
    CONTINUING = "CONTINUING"
    AVOIDING = "AVOIDING"
    HOLDING = "HOLDING"
    RETURNING = "RETURNING"
    LANDING = "LANDING"
    LANDED = "LANDED"
    ABORTING = "ABORTING"


@dataclass(frozen=True, slots=True)
class DecisionConfig:
    recovery_hold_s: float = 2.0
    decision_valid_for_s: float = 0.35
    vehicle_stale_ms: float = 100.0
    policy_name: str = "baseline_safety_policy"
    policy_version: str = "1.0.0"
    ruleset_hash: str = RULESET_HASH

    def __post_init__(self) -> None:
        if min(
            self.recovery_hold_s,
            self.decision_valid_for_s,
            self.vehicle_stale_ms,
        ) <= 0:
            raise ValueError("decision timing thresholds must be positive")
        if not self.ruleset_hash.startswith("sha256:") or len(self.ruleset_hash) != 71:
            raise ValueError("ruleset_hash must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class DecisionContext:
    mission_id: str
    risk: Envelope
    path: Envelope | None
    vehicle_state: Envelope
    mission_required_battery_pct: float = 30.0
    return_required_battery_pct: float = 15.0
    link_loss_action: str = "RETURN"

    def __post_init__(self) -> None:
        if not self.mission_id.strip():
            raise ValueError("mission_id must be non-empty")
        if not 0 <= self.return_required_battery_pct <= 100:
            raise ValueError("return battery requirement must be within 0..100")
        if not 0 <= self.mission_required_battery_pct <= 100:
            raise ValueError("mission battery requirement must be within 0..100")
        if self.return_required_battery_pct > self.mission_required_battery_pct:
            raise ValueError("return requirement cannot exceed mission requirement")
        if self.link_loss_action not in {"RETURN", "LAND", "HOLD"}:
            raise ValueError("link-loss action must be RETURN, LAND, or HOLD")


@dataclass(frozen=True, slots=True)
class DecisionTransition:
    previous: DecisionState
    current: DecisionState
    at: datetime
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SafetyContext:
    risk: Envelope
    path: Envelope | None
    vehicle_state: Envelope
    endpoint_id: str
    endpoint_kind: str

    def __post_init__(self) -> None:
        if not self.endpoint_id.strip():
            raise ValueError("endpoint_id must be non-empty")
        if self.endpoint_kind not in {"SIMULATED", "REAL"}:
            raise ValueError("endpoint_kind must be SIMULATED or REAL")
