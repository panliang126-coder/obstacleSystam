"""Plugin manifests and lifecycle port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from low_altitude_ai.domain.enums import RuntimeMode
from low_altitude_ai.domain.envelope import Envelope
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.ports.event_bus import EventBusPort


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str
    kind: str
    api_version: str
    input_schema: str
    output_schema: str
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginContext:
    run_id: str
    mode: RuntimeMode
    clock: ClockPort
    event_bus: EventBusPort
    config: dict[str, Any]


class Plugin(Protocol):
    @property
    def manifest(self) -> PluginManifest: ...

    async def initialize(self, context: PluginContext) -> None: ...

    async def health(self) -> Envelope: ...

    async def shutdown(self, deadline_s: float) -> None: ...
