"""Event-driven weather service composition."""

from __future__ import annotations

from low_altitude_ai.domain import Envelope
from low_altitude_ai.ports.event_bus import EventBusPort, Subscription
from low_altitude_ai.weather.estimator import WeatherEstimatorPlugin


class WeatherService:
    def __init__(self, *, event_bus: EventBusPort, estimator: WeatherEstimatorPlugin) -> None:
        self._event_bus = event_bus
        self._estimator = estimator
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = self._event_bus.subscribe(
            "sensor.normalized.weather",
            self._handle,
            group="weather-estimator",
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        subscription = self._subscription
        self._subscription = None
        await subscription.close()

    async def _handle(self, event: Envelope) -> None:
        environment = await self._estimator.estimate([event])
        await self._event_bus.publish("environment.update", environment)
