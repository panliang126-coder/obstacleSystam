"""Sensor driver port shared by real and simulated devices."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from low_altitude_ai.domain.envelope import Envelope


@dataclass(frozen=True, slots=True)
class SensorDescriptor:
    sensor_id: str
    sensor_type: str
    frame_id: str
    nominal_rate_hz: float


class SensorDriver(Protocol):
    @property
    def descriptor(self) -> SensorDescriptor: ...

    async def connect(self) -> None: ...

    def samples(self) -> AsyncIterator[Envelope]: ...

    async def health(self) -> Envelope: ...

    async def close(self) -> None: ...
