"""Run the deterministic Phase 3 perception/weather-to-Twin demonstration."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.digital_twin import TwinIngestService, TwinStateStore
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.perception import PerceptionService, RadarTrackerConfig, RadarTrackerPlugin
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
from low_altitude_ai.weather import WeatherEstimatorConfig, WeatherEstimatorPlugin, WeatherService


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _position_rmse(
    track_events: list[Envelope],
    *,
    initial: tuple[float, float, float],
    velocity: tuple[float, float, float],
    rate_hz: float,
) -> float:
    squared_errors: list[float] = []
    for event in track_events:
        targets = event.payload["targets"]
        if not isinstance(targets, list) or not targets:
            continue
        position = targets[0]["position"]["enu"]
        elapsed_s = event.sequence / rate_hz
        truth = tuple(initial[index] + velocity[index] * elapsed_s for index in range(3))
        squared_errors.append(
            sum((float(position[key]) - truth[index]) ** 2 for index, key in enumerate(
                ("east_m", "north_m", "up_m")
            ))
        )
    return math.sqrt(sum(squared_errors) / len(squared_errors))


async def run_phase3_demo(
    scenario_path: Path,
    scenario_schema_path: Path,
    wire_schema_dir: Path,
) -> dict[str, Any]:
    scenario = load_radar_scenario(scenario_path, scenario_schema_path)
    clock = SimClock(scenario.start_at)
    bus = InMemoryEventBus(queue_size=64)
    driver = RadarSimulatorDriver(scenario, clock)
    await driver.connect()
    config_hash = "sha256:" + hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    store = TwinStateStore(
        twin_id=f"{scenario.scenario_id}/{scenario.vehicle_id}",
        frame_id=scenario.frame_id,
        map_version="phase3-map@1.0.0",
        config_hash=config_hash,
    )
    twin_ids = DeterministicUuid7Factory(
        RandomStream(scenario.seed, "phase3/twin/event-id")
    )
    twin_service = TwinIngestService(
        store=store,
        event_bus=bus,
        clock=clock,
        event_id_factory=twin_ids.new,
        topics=("perception.tracks", "environment.update"),
    )
    tracker = RadarTrackerPlugin(
        config=RadarTrackerConfig(),
        event_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase3/perception/event-id")
        ),
        track_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase3/perception/track-id")
        ),
    )
    weather = WeatherEstimatorPlugin(
        config=WeatherEstimatorConfig(
            frame_id=scenario.frame_id,
            coverage_min_enu_m=(-500.0, -500.0, 0.0),
            coverage_max_enu_m=(500.0, 500.0, 300.0),
        ),
        event_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase3/weather/event-id")
        ),
    )
    context = PluginContext(
        run_id=str(driver.run_id),
        mode=RuntimeMode.SIM,
        clock=clock,
        event_bus=bus,
        config={},
    )
    await tracker.initialize(context)
    await weather.initialize(context)
    perception_service = PerceptionService(event_bus=bus, tracker=tracker)
    weather_service = WeatherService(event_bus=bus, estimator=weather)
    await twin_service.start()
    await perception_service.start()
    await weather_service.start()

    tracks: list[Envelope] = []
    environments: list[Envelope] = []
    snapshots: list[Envelope] = []

    async def collect_tracks(event: Envelope) -> None:
        tracks.append(event)

    async def collect_environment(event: Envelope) -> None:
        environments.append(event)

    async def collect_snapshot(event: Envelope) -> None:
        snapshots.append(event)

    track_subscription = bus.subscribe(
        "perception.tracks",
        collect_tracks,
        group="phase3-track-metrics",
    )
    environment_subscription = bus.subscribe(
        "environment.update",
        collect_environment,
        group="phase3-weather-metrics",
    )
    snapshot_subscription = bus.subscribe(
        "twin.snapshot",
        collect_snapshot,
        group="phase3-snapshot-metrics",
    )
    weather_truth = WeatherTruth(
        wind_enu_m_s=(5.0, -0.8, 0.2),
        gust_m_s=8.0,
        temperature_deg_c=31.0,
        relative_humidity_pct=78.0,
        precipitation_mm_h=2.0,
        visibility_m=4_500.0,
        pressure_pa=100_420.0,
    )
    weather_sensor = WeatherSensorModel(
        seed=scenario.seed,
        sensor_id="weather-station-01",
        frame_id=scenario.frame_id,
        vehicle_id=scenario.vehicle_id,
        run_id=driver.run_id,
        clock=clock,
    )
    weather_event = weather_sensor.sample(weather_truth, sequence=0)
    await bus.publish("sensor.normalized.weather", weather_event)
    await bus.drain()

    sensor_events: list[Envelope] = []
    async for event in driver.samples():
        sensor_events.append(event)
        await bus.publish("sensor.normalized.radar", event)
        await bus.drain()

    registry = SchemaRegistry(wire_schema_dir)
    for event in [*sensor_events, weather_event, *tracks, *environments, *snapshots]:
        registry.validate(event.to_mapping())
    final_snapshot = store.snapshot(clock.now())

    await perception_service.stop()
    await weather_service.stop()
    await twin_service.stop()
    await track_subscription.close()
    await environment_subscription.close()
    await snapshot_subscription.close()
    await tracker.shutdown(1.0)
    await weather.shutdown(1.0)
    await driver.close()
    await bus.close()

    truth_target = scenario.targets[0]
    confirmed = sum(
        1
        for event in tracks
        if event.payload["targets"]
        and event.payload["targets"][0]["state"] == "CONFIRMED"
    )
    environment_payload = environments[-1].payload
    wind = environment_payload["wind"]
    wind_error_m_s = math.sqrt(
        sum(
            (
                float(wind[key]) - weather_truth.wind_enu_m_s[index]
            )
            ** 2
            for index, key in enumerate(("east_m_s", "north_m_s", "up_m_s"))
        )
    )
    return {
        "scenario_id": scenario.scenario_id,
        "seed": scenario.seed,
        "sensor_events": len(sensor_events) + 1,
        "track_events": len(tracks),
        "confirmed_track_events": confirmed,
        "environment_events": len(environments),
        "twin_revision": final_snapshot.revision,
        "position_rmse_m": round(
            _position_rmse(
                tracks,
                initial=truth_target.initial_enu_m,
                velocity=truth_target.velocity_enu_m_s,
                rate_hz=scenario.rate_hz,
            ),
            6,
        ),
        "wind_error_m_s": round(wind_error_m_s, 6),
        "track_hash": _canonical_hash([event.to_mapping() for event in tracks]),
        "environment_hash": _canonical_hash(
            [event.to_mapping() for event in environments]
        ),
        "twin_snapshot_hash": final_snapshot.stable_hash(),
    }


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="obstacle-phase3-demo")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=project_root / "configs" / "scenarios" / "dynamic-crossing-v1.json",
    )
    parser.add_argument(
        "--scenario-schema",
        type=Path,
        default=project_root / "configs" / "scenario.schema.json",
    )
    parser.add_argument(
        "--wire-schemas",
        type=Path,
        default=project_root / "schemas" / "v1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(
        run_phase3_demo(args.scenario, args.scenario_schema, args.wire_schemas)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
