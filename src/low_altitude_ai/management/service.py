"""In-memory audited management baseline with RBAC and optimistic revisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from low_altitude_ai.management.model import (
    AlertRecord,
    AuditRecord,
    ConfigSnapshot,
    ManagementCommand,
    OperationResult,
    PluginRecord,
    Role,
)
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.simulator.randomness import DeterministicUuid7Factory

_ALLOWED_ROLES = {
    "alert.ack": {Role.OPERATOR, Role.ENGINEER, Role.SAFETY_APPROVER, Role.ADMIN},
    "config.draft": {Role.ENGINEER, Role.ADMIN},
    "config.validate": {Role.ENGINEER, Role.ADMIN},
    "config.approve": {Role.SAFETY_APPROVER},
    "config.activate": {Role.SAFETY_APPROVER},
    "config.rollback": {Role.SAFETY_APPROVER},
    "plugin.register": {Role.ENGINEER, Role.ADMIN},
    "plugin.shadow": {Role.ENGINEER, Role.ADMIN},
    "plugin.activate": {Role.SAFETY_APPROVER},
    "plugin.rollback": {Role.SAFETY_APPROVER},
    "replay.start": {Role.ENGINEER, Role.SAFETY_APPROVER},
    "mission.create": {Role.OPERATOR, Role.SAFETY_APPROVER},
    "mission.pause": {Role.OPERATOR, Role.SAFETY_APPROVER},
    "mission.resume": {Role.OPERATOR, Role.SAFETY_APPROVER},
    "mission.cancel": {Role.OPERATOR, Role.SAFETY_APPROVER},
}


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class ManagementService:
    def __init__(
        self,
        *,
        clock: ClockPort,
        operation_ids: DeterministicUuid7Factory,
    ) -> None:
        self._clock = clock
        self._operation_ids = operation_ids
        self._available = True
        self._revision = 0
        self._operations: dict[str, tuple[str, OperationResult]] = {}
        self._audit: list[AuditRecord] = []
        self._configs: dict[str, list[ConfigSnapshot]] = {}
        self._plugins: dict[tuple[str, str], PluginRecord] = {}
        self._alerts: dict[str, AlertRecord] = {}

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def audit_records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._audit)

    def set_available(self, value: bool) -> None:
        self._available = value

    def execute(self, command: ManagementCommand) -> OperationResult:
        signature = _digest(
            {
                "action": command.action,
                "resource": command.resource,
                "actor": command.actor,
                "role": command.role.value,
                "reason": command.reason,
                "payload": command.payload,
            }
        )
        cached = self._operations.get(command.idempotency_key)
        if cached is not None:
            if cached[0] == signature:
                return cached[1]
            return self._reject(command, "IDEMPOTENCY_KEY_REUSED")
        if not self._available:
            return self._record(command, signature, False, "MANAGEMENT_UNAVAILABLE", {})
        allowed = _ALLOWED_ROLES.get(command.action)
        if allowed is None or command.role not in allowed:
            return self._record(command, signature, False, "FORBIDDEN", {})
        if command.expected_revision != self._revision:
            return self._record(
                command,
                signature,
                False,
                "REVISION_CONFLICT",
                {"current_revision": self._revision},
            )
        try:
            success, reason_code, details, before_hash, after_hash = self._dispatch(command)
        except (KeyError, TypeError, ValueError):
            success, reason_code, details, before_hash, after_hash = (
                False,
                "COMMAND_VALIDATION_FAILED",
                {},
                None,
                None,
            )
        return self._record(
            command,
            signature,
            success,
            reason_code,
            details,
            before_hash,
            after_hash,
        )

    def config_snapshots(self, namespace: str) -> tuple[ConfigSnapshot, ...]:
        return tuple(self._configs.get(namespace, ()))

    def plugins(self) -> tuple[PluginRecord, ...]:
        return tuple(sorted(self._plugins.values(), key=lambda item: (item.name, item.version)))

    def upsert_alert(
        self,
        *,
        dedup_key: str,
        severity: str,
        summary: str,
    ) -> AlertRecord:
        if severity not in {"INFO", "WARNING", "CRITICAL"}:
            raise ValueError("unsupported alert severity")
        now = self._clock.now()
        existing = self._alerts.get(dedup_key)
        if existing is None:
            alert = AlertRecord(dedup_key, severity, summary, now, now, 1)
        else:
            alert = replace(
                existing,
                severity=severity,
                summary=summary,
                last_seen=now,
                count=existing.count + 1,
            )
        self._alerts[dedup_key] = alert
        return alert

    def alerts(self) -> tuple[AlertRecord, ...]:
        return tuple(sorted(self._alerts.values(), key=lambda item: item.dedup_key))

    def _dispatch(
        self,
        command: ManagementCommand,
    ) -> tuple[bool, str, dict[str, Any], str | None, str | None]:
        if command.action.startswith("config."):
            return self._config_command(command)
        if command.action.startswith("plugin."):
            return self._plugin_command(command)
        if command.action == "alert.ack":
            return self._ack_alert(command)
        return True, "COMMAND_ACCEPTED", {}, None, None

    def _config_command(
        self,
        command: ManagementCommand,
    ) -> tuple[bool, str, dict[str, Any], str | None, str | None]:
        history = self._configs.setdefault(command.resource, [])
        current = history[-1] if history else None
        before_hash = current.digest if current else None
        if command.action == "config.draft":
            values = command.payload["values"]
            if not isinstance(values, dict) or not values:
                raise ValueError("config values must be a non-empty object")
            if any(
                "secret" in key.lower() and not str(value).startswith("ref:")
                for key, value in values.items()
            ):
                return False, "PLAINTEXT_SECRET_FORBIDDEN", {}, before_hash, None
            snapshot = ConfigSnapshot(
                namespace=command.resource,
                revision=self._revision + 1,
                state="DRAFT",
                values=values,
                digest=_digest(values),
                approvals=(),
            )
        else:
            if current is None:
                return False, "CONFIG_NOT_FOUND", {}, None, None
            if command.action == "config.validate":
                if current.state != "DRAFT":
                    return False, "CONFIG_STATE_INVALID", {}, before_hash, None
                snapshot = replace(current, state="SCHEMA_VALIDATED")
            elif command.action == "config.approve":
                if current.state not in {"SCHEMA_VALIDATED", "APPROVED"}:
                    return False, "CONFIG_STATE_INVALID", {}, before_hash, None
                snapshot = replace(
                    current,
                    state="APPROVED",
                    approvals=tuple(sorted({*current.approvals, command.actor})),
                )
            elif command.action == "config.activate":
                required = 2 if self._safety_critical(current) else 1
                if current.state != "APPROVED" or len(current.approvals) < required:
                    return False, "INSUFFICIENT_APPROVALS", {}, before_hash, None
                snapshot = replace(current, state="ACTIVE")
            elif command.action == "config.rollback":
                active = next(
                    (item for item in reversed(history[:-1]) if item.state == "ACTIVE"),
                    None,
                )
                if active is None:
                    return False, "ROLLBACK_TARGET_NOT_FOUND", {}, before_hash, None
                history[-1] = replace(current, state="ROLLED_BACK")
                snapshot = replace(active, revision=self._revision + 1, state="ACTIVE")
            else:
                return False, "COMMAND_UNKNOWN", {}, before_hash, None
        if command.action == "config.draft":
            history.append(snapshot)
        elif history and history[-1] is current:
            history[-1] = snapshot
        else:
            history.append(snapshot)
        return (
            True,
            "CONFIG_UPDATED",
            {
                "state": snapshot.state,
                "digest": snapshot.digest,
                "approvals": list(snapshot.approvals),
            },
            before_hash,
            snapshot.digest,
        )

    def _plugin_command(
        self,
        command: ManagementCommand,
    ) -> tuple[bool, str, dict[str, Any], str | None, str | None]:
        name = str(command.payload["name"])
        version = str(command.payload["version"])
        key = (name, version)
        current = self._plugins.get(key)
        before_hash = current.artifact_hash if current else None
        if command.action == "plugin.register":
            artifact_hash = str(command.payload["artifact_hash"])
            if not artifact_hash.startswith("sha256:") or len(artifact_hash) != 71:
                return False, "ARTIFACT_HASH_INVALID", {}, None, None
            if not bool(command.payload.get("signature_valid")) or not bool(
                command.payload.get("compatible")
            ):
                return False, "ARTIFACT_VALIDATION_FAILED", {}, None, None
            record = PluginRecord(
                name=name,
                version=version,
                artifact_hash=artifact_hash,
                state="VALIDATED",
                input_schema=str(command.payload["input_schema"]),
                output_schema=str(command.payload["output_schema"]),
            )
        elif current is None:
            return False, "PLUGIN_NOT_FOUND", {}, None, None
        elif command.action == "plugin.shadow" and current.state == "VALIDATED":
            record = replace(current, state="SHADOW")
        elif command.action == "plugin.activate" and current.state == "SHADOW":
            record = replace(current, state="ACTIVE")
        elif command.action == "plugin.rollback" and current.state == "ACTIVE":
            record = replace(current, state="SHADOW")
        else:
            return False, "PLUGIN_STATE_INVALID", {}, before_hash, None
        self._plugins[key] = record
        return (
            True,
            "PLUGIN_UPDATED",
            {"state": record.state},
            before_hash,
            record.artifact_hash,
        )

    def _ack_alert(
        self,
        command: ManagementCommand,
    ) -> tuple[bool, str, dict[str, Any], str | None, str | None]:
        alert = self._alerts.get(command.resource)
        if alert is None:
            return False, "ALERT_NOT_FOUND", {}, None, None
        self._alerts[command.resource] = replace(
            alert,
            acknowledged_by=command.actor,
            acknowledged_at=self._clock.now(),
        )
        return True, "ALERT_ACKNOWLEDGED", {"dedup_key": command.resource}, None, None

    def _record(
        self,
        command: ManagementCommand,
        signature: str,
        success: bool,
        reason_code: str,
        details: dict[str, Any],
        before_hash: str | None = None,
        after_hash: str | None = None,
    ) -> OperationResult:
        if success:
            self._revision += 1
        operation_id = str(self._operation_ids.new(self._clock.now()))
        result = OperationResult(
            operation_id=operation_id,
            status="SUCCEEDED" if success else "REJECTED",
            action=command.action,
            resource=command.resource,
            revision=self._revision,
            reason_code=reason_code,
            details=details,
        )
        self._operations[command.idempotency_key] = (signature, result)
        self._audit.append(
            AuditRecord(
                operation_id=operation_id,
                at=self._clock.now(),
                actor=command.actor,
                role=command.role,
                action=command.action,
                resource=command.resource,
                reason=command.reason,
                before_hash=before_hash,
                after_hash=after_hash,
                result=result.status,
                reason_code=reason_code,
            )
        )
        return result

    def _reject(self, command: ManagementCommand, reason_code: str) -> OperationResult:
        operation_id = str(self._operation_ids.new(self._clock.now()))
        result = OperationResult(
            operation_id=operation_id,
            status="REJECTED",
            action=command.action,
            resource=command.resource,
            revision=self._revision,
            reason_code=reason_code,
            details={},
        )
        self._audit.append(
            AuditRecord(
                operation_id=operation_id,
                at=self._clock.now(),
                actor=command.actor,
                role=command.role,
                action=command.action,
                resource=command.resource,
                reason=command.reason,
                before_hash=None,
                after_hash=None,
                result="REJECTED",
                reason_code=reason_code,
            )
        )
        return result

    @staticmethod
    def _safety_critical(snapshot: ConfigSnapshot) -> bool:
        return snapshot.namespace.startswith("safety") or any(
            key in {"live_endpoint", "minimum_separation_m", "control_policy"}
            for key in snapshot.values
        )
