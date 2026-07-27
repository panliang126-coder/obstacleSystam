import asyncio
from pathlib import Path

import pytest

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.domain import RuntimeMode
from low_altitude_ai.perception import (
    PerceptionNotInitializedError,
    RadarTrackerConfig,
    RadarTrackerPlugin,
)
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.schemas.registry import SchemaRegistry
from low_altitude_ai.simulator import (
    DeterministicUuid7Factory,
    RadarSimulatorDriver,
    RandomStream,
    SimClock,
    load_radar_scenario,
)
from low_altitude_ai.weather import (
    WeatherEstimatorConfig,
    WeatherEstimatorPlugin,
    WeatherNotInitializedError,
)


def make_context(
    *,
    run_id: str,
    clock: SimClock,
    bus: InMemoryEventBus,
) -> PluginContext:
    return PluginContext(
        run_id=run_id,
        mode=RuntimeMode.SIM,
        clock=clock,
        event_bus=bus,
        config={},
    )


@pytest.mark.contract
def test_radar_tracker_plugin_lifecycle_and_output_contract(
    scenario_path: Path,
    scenario_schema: Path,
    schema_dir: Path,
) -> None:
    async def exercise() -> tuple[dict[str, object], dict[str, object]]:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        clock = SimClock(scenario.start_at)
        bus = InMemoryEventBus()
        driver = RadarSimulatorDriver(scenario, clock)
        plugin = RadarTrackerPlugin(
            config=RadarTrackerConfig(),
            event_ids=DeterministicUuid7Factory(RandomStream(1, "events")),
            track_ids=DeterministicUuid7Factory(RandomStream(1, "tracks")),
        )
        await driver.connect()
        first = await anext(driver.samples())
        with pytest.raises(PerceptionNotInitializedError):
            await plugin.process(first)
        await plugin.initialize(make_context(run_id=str(driver.run_id), clock=clock, bus=bus))
        output = await plugin.process(first)
        health = await plugin.health()
        await plugin.shutdown(1.0)
        with pytest.raises(PerceptionNotInitializedError):
            await plugin.health()
        await driver.close()
        await bus.close()
        return output.to_mapping(), health.to_mapping()

    output, health = asyncio.run(exercise())
    registry = SchemaRegistry(schema_dir)
    registry.validate(output)
    registry.validate(health)
    assert output["payload"]["targets"][0]["source_refs"]  # type: ignore[index]
    assert output["payload"]["targets"][0]["position"]["covariance"]  # type: ignore[index]


@pytest.mark.contract
def test_weather_plugin_lifecycle_requires_initialization() -> None:
    plugin = WeatherEstimatorPlugin(
        config=WeatherEstimatorConfig(
            frame_id="site-alpha-enu-v1",
            coverage_min_enu_m=(-1.0, -1.0, 0.0),
            coverage_max_enu_m=(1.0, 1.0, 10.0),
        ),
        event_ids=DeterministicUuid7Factory(RandomStream(1, "weather")),
    )

    with pytest.raises(WeatherNotInitializedError):
        asyncio.run(plugin.estimate([]))
