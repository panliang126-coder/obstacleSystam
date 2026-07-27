import asyncio
import statistics
import time
from pathlib import Path

import pytest

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.domain import RuntimeMode
from low_altitude_ai.perception import RadarTrackerConfig, RadarTrackerPlugin
from low_altitude_ai.ports.plugins import PluginContext
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


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[94]


@pytest.mark.performance
def test_phase3_local_plugin_latency_budgets(
    scenario_path: Path,
    scenario_schema: Path,
) -> None:
    async def exercise() -> tuple[list[float], list[float]]:
        scenario = load_radar_scenario(scenario_path, scenario_schema)
        clock = SimClock(scenario.start_at)
        bus = InMemoryEventBus()
        driver = RadarSimulatorDriver(scenario, clock)
        await driver.connect()
        context = PluginContext(
            run_id=str(driver.run_id),
            mode=RuntimeMode.SIM,
            clock=clock,
            event_bus=bus,
            config={},
        )
        tracker = RadarTrackerPlugin(
            config=RadarTrackerConfig(),
            event_ids=DeterministicUuid7Factory(RandomStream(5, "tracker-events")),
            track_ids=DeterministicUuid7Factory(RandomStream(5, "tracker-tracks")),
        )
        weather = WeatherEstimatorPlugin(
            config=WeatherEstimatorConfig(
                frame_id=scenario.frame_id,
                coverage_min_enu_m=(-100.0, -100.0, 0.0),
                coverage_max_enu_m=(100.0, 100.0, 100.0),
            ),
            event_ids=DeterministicUuid7Factory(RandomStream(5, "weather-events")),
        )
        await tracker.initialize(context)
        await weather.initialize(context)
        radar_events = [event async for event in driver.samples()]
        weather_event = WeatherSensorModel(
            seed=5,
            sensor_id="weather-01",
            frame_id=scenario.frame_id,
            vehicle_id=scenario.vehicle_id,
            run_id=driver.run_id,
            clock=clock,
        ).sample(
            WeatherTruth(
                wind_enu_m_s=(5.0, 0.0, 0.0),
                gust_m_s=8.0,
                temperature_deg_c=25.0,
                relative_humidity_pct=70.0,
                precipitation_mm_h=1.0,
                visibility_m=4_000.0,
                pressure_pa=100_000.0,
            ),
            sequence=0,
        )
        tracker_latencies: list[float] = []
        weather_latencies: list[float] = []
        for index in range(100):
            radar_event = radar_events[index % len(radar_events)]
            start = time.perf_counter_ns()
            await tracker.process(radar_event)
            tracker_latencies.append((time.perf_counter_ns() - start) / 1_000_000)
            start = time.perf_counter_ns()
            await weather.estimate([weather_event])
            weather_latencies.append((time.perf_counter_ns() - start) / 1_000_000)
        await tracker.shutdown(1.0)
        await weather.shutdown(1.0)
        await driver.close()
        await bus.close()
        return tracker_latencies, weather_latencies

    tracker_ms, weather_ms = asyncio.run(exercise())

    assert p95(tracker_ms) <= 80.0
    assert p95(weather_ms) <= 100.0
