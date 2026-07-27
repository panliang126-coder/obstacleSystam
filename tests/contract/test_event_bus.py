import asyncio

import pytest

from low_altitude_ai.adapters.event_bus import (
    EventBusClosedError,
    EventBusFullError,
    HandlerFailedError,
    InMemoryEventBus,
)
from low_altitude_ai.domain import Envelope


@pytest.mark.contract
def test_publish_delivers_matching_topic_and_drain_is_a_barrier(sensor_event: Envelope) -> None:
    async def exercise() -> list[int]:
        received: list[int] = []
        bus = InMemoryEventBus(queue_size=2)

        async def handler(event: Envelope) -> None:
            received.append(event.sequence)

        subscription = bus.subscribe("sensor.normalized.*", handler, group="twin")
        ack = await bus.publish("sensor.normalized.radar", sensor_event)
        await bus.drain()
        await subscription.close()
        await bus.close()
        assert ack.message_id == str(sensor_event.event_id)
        return received

    assert asyncio.run(exercise()) == [sensor_event.sequence]


@pytest.mark.contract
def test_bounded_queue_fails_publish_at_deadline(sensor_event: Envelope) -> None:
    async def exercise() -> None:
        gate = asyncio.Event()
        started = asyncio.Event()
        bus = InMemoryEventBus(queue_size=1, publish_timeout_s=0.01)

        async def blocked_handler(event: Envelope) -> None:
            del event
            started.set()
            await gate.wait()

        bus.subscribe("sensor.normalized.radar", blocked_handler, group="slow")
        await bus.publish("sensor.normalized.radar", sensor_event)
        await started.wait()
        await bus.publish("sensor.normalized.radar", sensor_event)
        with pytest.raises(EventBusFullError, match="queue is full"):
            await bus.publish("sensor.normalized.radar", sensor_event)
        gate.set()
        await bus.drain()
        await bus.close()

    asyncio.run(exercise())


@pytest.mark.contract
def test_handler_failure_is_reported_and_truth_access_is_denied(sensor_event: Envelope) -> None:
    async def exercise() -> None:
        bus = InMemoryEventBus()

        async def failing_handler(event: Envelope) -> None:
            del event
            raise RuntimeError("consumer failed")

        with pytest.raises(PermissionError, match="simulation truth"):
            bus.subscribe("simulation.truth.radar", failing_handler, group="perception")
        bus.subscribe("sensor.normalized.radar", failing_handler, group="failing")
        await bus.publish("sensor.normalized.radar", sensor_event)
        with pytest.raises(HandlerFailedError):
            await bus.drain()
        with pytest.raises(HandlerFailedError):
            await bus.close()

    asyncio.run(exercise())


@pytest.mark.contract
def test_bus_rejects_operations_after_shutdown(sensor_event: Envelope) -> None:
    async def exercise() -> None:
        bus = InMemoryEventBus()
        await bus.close()
        with pytest.raises(EventBusClosedError):
            await bus.publish("sensor.normalized.radar", sensor_event)

    asyncio.run(exercise())
