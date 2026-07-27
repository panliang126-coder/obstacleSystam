"""Immutable management command and query models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class Role(str, Enum):  # noqa: UP042 - Python 3.10 host compatibility
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ENGINEER = "ENGINEER"
    SAFETY_APPROVER = "SAFETY_APPROVER"
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class ManagementCommand:
    action: str
    resource: str
    idempotency_key: str
    actor: str
    role: Role
    client_time: datetime
    expected_revision: int
    reason: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.action.strip() or not self.resource.strip():
            raise ValueError("command action and resource must be non-empty")
        if len(self.idempotency_key) < 8:
            raise ValueError("idempotency_key must contain at least 8 characters")
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError("actor and audit reason must be non-empty")
        if self.client_time.tzinfo is None or self.client_time.utcoffset() is None:
            raise ValueError("client_time must be timezone-aware")
        if self.expected_revision < 0:
            raise ValueError("expected_revision cannot be negative")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation_id: str
    status: str
    action: str
    resource: str
    revision: int
    reason_code: str
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.status not in {"SUCCEEDED", "REJECTED"}:
            raise ValueError("operation status must be SUCCEEDED or REJECTED")
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True, slots=True)
class AuditRecord:
    operation_id: str
    at: datetime
    actor: str
    role: Role
    action: str
    resource: str
    reason: str
    before_hash: str | None
    after_hash: str | None
    result: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    namespace: str
    revision: int
    state: str
    values: Mapping[str, Any]
    digest: str
    approvals: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", dict(self.values))


@dataclass(frozen=True, slots=True)
class PluginRecord:
    name: str
    version: str
    artifact_hash: str
    state: str
    input_schema: str
    output_schema: str


@dataclass(frozen=True, slots=True)
class AlertRecord:
    dedup_key: str
    severity: str
    summary: str
    first_seen: datetime
    last_seen: datetime
    count: int
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
