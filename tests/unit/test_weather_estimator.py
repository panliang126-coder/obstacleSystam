import asyncio
from dataclasses import replace
from pathlib import Path

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.schemas.registry import SchemaRegistry
from low_altitude_ai.simulator import (
    DeterministicUuid7Factory,
    RadarSimulatorDriver,
    RandomStream,
    SimClock,
    WeatherSensorModel,
    WeatherTruth,
    load_radar_scenario,
)
from low_altitude_ai.weather import WeatherEstimatorConfig, WeatherEstimatorPlugin


def test_weather_estimate_is_complete_and_unknown_is_conservative(
    scenario_path: Path,
    scenario_schema: Path,
    schema_dir: Path,
) -> None:
    async def exercise() -> tuple[Envelope, Envelope, Envelope]:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        clock = SimClock(scenario.start_at)
        bus = InMemoryEventBus()
        radar = RadarSimulatorDriver(scenario, clock)
        model = WeatherSensorModel(
            seed=scenario.seed,
            sensor_id="weather-01",
            frame_id=scenario.frame_id,
            vehicle_id=scenario.vehicle_id,
            run_id=radar.run_id,
            clock=clock,
        )
        truth = WeatherTruth(
            wind_enu_m_s=(5.0, -1.0, 0.2),
            gust_m_s=8.0,
            temperature_deg_c=3.0,
            relative_humidity_pct=90.0,
            precipitation_mm_h=4.0,
            visibility_m=2_000.0,
            pressure_pa=100_000.0,
        )
        observation = model.sample(truth, sequence=0)
        plugin = WeatherEstimatorPlugin(
            config=WeatherEstimatorConfig(
                frame_id=scenario.frame_id,
                coverage_min_enu_m=(-100.0, -100.0, 0.0),
                coverage_max_enu_m=(100.0, 100.0, 100.0),
            ),
            event_ids=DeterministicUuid7Factory(RandomStream(2, "weather-events")),
        )
        await plugin.initialize(
            PluginContext(
                run_id=str(radar.run_id),
                mode=RuntimeMode.SIM,
                clock=clock,
                event_bus=bus,
                config={},
            )
        )
        estimate = await plugin.estimate([observation])
        clock.advance(6.0)
        stale = await plugin.estimate([observation])
        unavailable = await plugin.estimate([])
        await bus.close()
        return estimate, stale, unavailable

    estimate, stale, unavailable = asyncio.run(exercise())
    registry = SchemaRegistry(schema_dir)
    for event in (estimate, stale, unavailable):
        registry.validate(event.to_mapping())

    assert estimate.quality.valid
    assert estimate.payload["risk_factors"]["icing"] > 0
    assert not stale.quality.valid
    assert "STALE_OBSERVATION" in stale.quality.flags
    assert unavailable.payload["risk_factors"]["uncertainty"] == 1.0
    assert unavailable.quality.confidence == 0.0


def test_weather_physical_qc_rejects_invalid_humidity(
    scenario_path: Path,
    scenario_schema: Path,
) -> None:
    async def exercise() -> Envelope:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        clock = SimClock(scenario.start_at)
        bus = InMemoryEventBus()
        radar = RadarSimulatorDriver(scenario, clock)
        model = WeatherSensorModel(
            seed=1,
            sensor_id="weather-01",
            frame_id=scenario.frame_id,
            vehicle_id=scenario.vehicle_id,
            run_id=radar.run_id,
            clock=clock,
        )
        event = model.sample(
            WeatherTruth(
                wind_enu_m_s=(1.0, 0.0, 0.0),
                gust_m_s=2.0,
                temperature_deg_c=20.0,
                relative_humidity_pct=50.0,
                precipitation_mm_h=0.0,
                visibility_m=5_000.0,
                pressure_pa=100_000.0,
            ),
            sequence=0,
        )
        payload = dict(event.payload)
        sample = dict(payload["sample"])
        sample["relative_humidity_pct"] = 120.0
        payload["sample"] = sample
        invalid = replace(event, payload=payload)
        plugin = WeatherEstimatorPlugin(
            config=WeatherEstimatorConfig(
                frame_id=scenario.frame_id,
                coverage_min_enu_m=(-1.0, -1.0, 0.0),
                coverage_max_enu_m=(1.0, 1.0, 10.0),
            ),
            event_ids=DeterministicUuid7Factory(RandomStream(2, "weather-events")),
        )
        await plugin.initialize(
            PluginContext(
                run_id=str(radar.run_id),
                mode=RuntimeMode.SIM,
                clock=clock,
                event_bus=bus,
                config={},
            )
        )
        output = await plugin.estimate([invalid])
        await bus.close()
        return output

    output = asyncio.run(exercise())

    assert not output.quality.valid
    assert "INVALID_OBSERVATION" in output.quality.flags
    assert output.payload["risk_factors"]["uncertainty"] == 1.0
