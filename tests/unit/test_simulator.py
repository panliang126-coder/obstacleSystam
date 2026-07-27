import asyncio
from pathlib import Path

import pytest

from low_altitude_ai.schemas.registry import SchemaRegistry
from low_altitude_ai.simulator import (
    RadarSimulatorDriver,
    RandomStream,
    ScenarioValidationError,
    SimClock,
    load_radar_scenario,
)


def test_sim_clock_advances_without_wall_time(scenario_path: Path, scenario_schema: Path) -> None:
    scenario = load_radar_scenario(scenario_path, scenario_schema)
    clock = SimClock(scenario.start_at)

    asyncio.run(clock.sleep(0.125))

    assert (clock.now() - scenario.start_at).total_seconds() == pytest.approx(0.125)
    assert clock.monotonic_ns() == 125_000_000
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(-0.001)


def test_component_random_stream_is_stable_and_isolated() -> None:
    first = RandomStream(42001, "sensor/radar/noise")
    second = RandomStream(42001, "sensor/radar/noise")
    unrelated = RandomStream(42001, "weather/gust")

    assert [first.random() for _ in range(5)] == [second.random() for _ in range(5)]
    assert unrelated.random() != RandomStream(42001, "sensor/radar/noise").random()


def test_invalid_scenario_is_rejected(
    tmp_path: Path,
    scenario_path: Path,
    scenario_schema: Path,
) -> None:
    invalid = scenario_path.read_text(encoding="utf-8").replace(
        '"rate_hz": 10.0',
        '"rate_hz": 0',
    )
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ScenarioValidationError, match="minimum"):
        load_radar_scenario(invalid_path, scenario_schema)


def test_radar_simulator_implements_driver_contract_and_schema(
    scenario_path: Path,
    scenario_schema: Path,
    schema_dir: Path,
) -> None:
    async def exercise() -> tuple[list[dict[str, object]], dict[str, object]]:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        driver = RadarSimulatorDriver(scenario, SimClock(scenario.start_at))
        await driver.connect()
        await driver.connect()
        events = [event.to_mapping() async for event in driver.samples()]
        health = (await driver.health()).to_mapping()
        await driver.close()
        return events, health

    events, health = asyncio.run(exercise())
    registry = SchemaRegistry(schema_dir)

    assert len(events) == 10
    assert [event["sequence"] for event in events] == list(range(10))
    assert len({event["payload"]["source_session_id"] for event in events}) == 1  # type: ignore[index]
    assert all(event["mode"] == "SIM" for event in events)
    for event in events:
        registry.validate(event)
    registry.validate(health)


def test_same_scenario_and_seed_produce_identical_wire_events(
    scenario_path: Path,
    scenario_schema: Path,
) -> None:
    async def run_once() -> list[dict[str, object]]:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        driver = RadarSimulatorDriver(scenario, SimClock(scenario.start_at))
        await driver.connect()
        return [event.to_mapping() async for event in driver.samples()]

    assert asyncio.run(run_once()) == asyncio.run(run_once())
