"""UI-side command guard; server-side RBAC remains authoritative."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from low_altitude_ai.management import (
    ManagementCommand,
    ManagementService,
    OperationResult,
    Role,
)
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.ui.state import ConnectionState, UiStateStore


class UiCommandBlocked(RuntimeError):
    """The UI refused to send a command in an unsafe connection or replay state."""


class UiCommandClient:
    def __init__(
        self,
        *,
        store: UiStateStore,
        service: ManagementService,
        clock: ClockPort,
        actor: str,
        role: Role,
    ) -> None:
        self._store = store
        self._service = service
        self._clock = clock
        self._actor = actor
        self._role = role
        self._sent_count = 0

    @property
    def sent_count(self) -> int:
        return self._sent_count

    def send(
        self,
        *,
        action: str,
        resource: str,
        idempotency_key: str,
        expected_revision: int,
        reason: str,
        payload: Mapping[str, Any],
    ) -> OperationResult:
        if self._store.connection != ConnectionState.LIVE:
            raise UiCommandBlocked("commands require a synchronized live connection")
        if not self._store.commands_enabled:
            raise UiCommandBlocked("commands are disabled in replay")
        command = ManagementCommand(
            action=action,
            resource=resource,
            idempotency_key=idempotency_key,
            actor=self._actor,
            role=self._role,
            client_time=self._clock.now(),
            expected_revision=expected_revision,
            reason=reason,
            payload=payload,
        )
        self._sent_count += 1
        return self._service.execute(command)
