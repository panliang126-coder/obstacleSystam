import asyncio
from dataclasses import replace
from pathlib import Path

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.domain.identifiers import uuid7
from low_altitude_ai.perception import RadarTrackerConfig, RadarTrackerPlugin
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.simulator import (
    DeterministicUuid7Factory,
    RadarSimulatorDriver,
    RandomStream,
    SimClock,
    load_radar_scenario,
)


def test_tracker_degrades_for_duplicate_out_of_order_and_dropout(
    scenario_path: Path,
    scenario_schema: Path,
) -> None:
    async def exercise() -> tuple[Envelope, Envelope, Envelope, Envelope]:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        clock = SimClock(scenario.start_at)
        bus = InMemoryEventBus()
        driver = RadarSimulatorDriver(scenario, clock)
        tracker = RadarTrackerPlugin(
            config=RadarTrackerConfig(),
            event_ids=DeterministicUuid7Factory(RandomStream(3, "events")),
            track_ids=DeterministicUuid7Factory(RandomStream(3, "tracks")),
        )
        await driver.connect()
        await tracker.initialize(
            PluginContext(
                run_id=str(driver.run_id),
                mode=RuntimeMode.SIM,
                clock=clock,
                event_bus=bus,
                config={},
            )
        )
        samples = driver.samples()
        first = await anext(samples)
        await tracker.process(first)
        duplicate = await tracker.process(first)
        second = await anext(samples)
        await tracker.process(second)
        out_of_order = await tracker.process(replace(second, event_id=uuid7()))
        third = await anext(samples)
        confirmed = await tracker.process(third)

        empty_payload = dict(third.payload)
        sample = dict(empty_payload["sample"])
        sample["detections"] = []
        empty_payload["sample"] = sample
        clock.advance(0.3)
        coasting_event = replace(
            third,
            event_id=uuid7(),
            sequence=3,
            observed_at=clock.now(),
            received_at=clock.now(),
            payload=empty_payload,
        )
        coasting = await tracker.process(coasting_event)
        clock.advance(0.4)
        lost_event = replace(
            coasting_event,
            event_id=uuid7(),
            sequence=4,
            observed_at=clock.now(),
            received_at=clock.now(),
        )
        lost = await tracker.process(lost_event)
        await tracker.shutdown(1.0)
        await driver.close()
        await bus.close()
        return duplicate, out_of_order, confirmed, coasting, lost

    duplicate, out_of_order, confirmed, coasting, lost = asyncio.run(exercise())

    assert "DUPLICATE_INPUT" in duplicate.quality.flags
    assert not duplicate.quality.valid
    assert "OUT_OF_ORDER_INPUT" in out_of_order.quality.flags
    assert confirmed.payload["targets"][0]["state"] == "CONFIRMED"
    assert coasting.payload["targets"][0]["state"] == "COASTING"
    assert lost.payload["targets"][0]["state"] == "LOST"
