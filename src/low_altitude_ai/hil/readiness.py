"""Evidence-based HIL permit gate; it never grants LIVE authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from low_altitude_ai.domain import RuntimeMode


@dataclass(frozen=True, slots=True)
class HilEvidence:
    mode: RuntimeMode
    endpoint_id: str
    endpoint_kind: str
    hardware_id: str
    firmware_hash: str
    calibration_hash: str
    emergency_stop_verified: bool
    propellers_removed: bool
    network_isolated: bool
    native_failsafe_verified: bool
    rollback_plan_verified: bool
    time_sync_offset_ms: float
    operator: str
    safety_approver: str
    approved_until: datetime


@dataclass(frozen=True, slots=True)
class HilReadinessReport:
    ready: bool
    checked_at: datetime
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HilPermit:
    endpoint_id: str
    hardware_id: str
    expires_at: datetime
    token_hash: str


class HilReadinessGate:
    def evaluate(self, evidence: HilEvidence, now: datetime) -> HilReadinessReport:
        blockers: list[str] = []
        if evidence.mode != RuntimeMode.HIL:
            blockers.append("MODE_NOT_HIL")
        if evidence.endpoint_kind != "REAL" or not evidence.endpoint_id.strip():
            blockers.append("ENDPOINT_BINDING_INVALID")
        if not evidence.hardware_id.strip():
            blockers.append("HARDWARE_ID_MISSING")
        if not self._digest_valid(evidence.firmware_hash):
            blockers.append("FIRMWARE_HASH_INVALID")
        if not self._digest_valid(evidence.calibration_hash):
            blockers.append("CALIBRATION_HASH_INVALID")
        checks = (
            ("EMERGENCY_STOP_NOT_VERIFIED", evidence.emergency_stop_verified),
            ("PROPELLERS_NOT_REMOVED", evidence.propellers_removed),
            ("NETWORK_NOT_ISOLATED", evidence.network_isolated),
            ("NATIVE_FAILSAFE_NOT_VERIFIED", evidence.native_failsafe_verified),
            ("ROLLBACK_PLAN_NOT_VERIFIED", evidence.rollback_plan_verified),
        )
        blockers.extend(code for code, passed in checks if not passed)
        if abs(evidence.time_sync_offset_ms) > 5.0:
            blockers.append("TIME_SYNC_OUT_OF_BOUNDS")
        if (
            not evidence.operator.strip()
            or not evidence.safety_approver.strip()
            or evidence.operator == evidence.safety_approver
        ):
            blockers.append("INDEPENDENT_APPROVAL_MISSING")
        if evidence.approved_until <= now:
            blockers.append("HIL_APPROVAL_EXPIRED")
        return HilReadinessReport(not blockers, now, tuple(blockers))

    def issue_permit(self, evidence: HilEvidence, now: datetime) -> HilPermit:
        report = self.evaluate(evidence, now)
        if not report.ready:
            raise ValueError("HIL readiness blockers: " + ",".join(report.blockers))
        material = json.dumps(
            {
                **asdict(evidence),
                "mode": evidence.mode.value,
                "approved_until": evidence.approved_until.isoformat(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode()
        return HilPermit(
            endpoint_id=evidence.endpoint_id,
            hardware_id=evidence.hardware_id,
            expires_at=evidence.approved_until,
            token_hash="sha256:" + hashlib.sha256(material).hexdigest(),
        )

    @staticmethod
    def _digest_valid(value: str) -> bool:
        if not value.startswith("sha256:") or len(value) != 71:
            return False
        return all(character in "0123456789abcdef" for character in value[7:])
