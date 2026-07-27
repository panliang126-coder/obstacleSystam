"""Flight-controller port shared by isolated HIL and future LIVE adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from low_altitude_ai.domain import Envelope

Vector3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class FlightControllerDescriptor:
    endpoint_id: str
    vendor: str
    transport: str
    native_frame: str


@dataclass(frozen=True, slots=True)
class ControlSetpoint:
    position_enu_m: Vector3
    target_speed_m_s: float

    def __post_init__(self) -> None:
        if self.target_speed_m_s < 0:
            raise ValueError("target speed cannot be negative")


class FlightControllerPort(Protocol):
    @property
    def descriptor(self) -> FlightControllerDescriptor: ...

    async def connect(self) -> None: ...

    async def execute(
        self,
        command: Envelope,
        setpoints: tuple[ControlSetpoint, ...],
    ) -> Envelope: ...

    async def close(self) -> None: ...
