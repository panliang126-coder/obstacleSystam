"""Event-driven perception service composition."""

from __future__ import annotations

from low_altitude_ai.domain import Envelope
from low_altitude_ai.perception.radar_tracker import RadarTrackerPlugin
from low_altitude_ai.ports.event_bus import EventBusPort, Subscription


class PerceptionService:
    def __init__(self, *, event_bus: EventBusPort, tracker: RadarTrackerPlugin) -> None:
        self._event_bus = event_bus
        self._tracker = tracker
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = self._event_bus.subscribe(
            "sensor.normalized.radar",
            self._handle,
            group="perception-radar",
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        subscription = self._subscription
        self._subscription = None
        await subscription.close()

    async def _handle(self, event: Envelope) -> None:
        tracks = await self._tracker.process(event)
        await self._event_bus.publish("perception.tracks", tracks)
