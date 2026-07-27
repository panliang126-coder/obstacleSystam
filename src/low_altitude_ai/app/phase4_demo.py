"""Run the deterministic Phase 4 risk and path-planning demonstration."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.digital_twin import TwinStateStore
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.perception import RadarTrackerConfig, RadarTrackerPlugin
from low_altitude_ai.planning import PlannerConfig, PlanRequest, RuleBasedPlannerPlugin
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.risk import RiskEngineConfig, RiskEnginePlugin, VehicleKinematics
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


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


async def run_phase4_demo(
    scenario_path: Path,
    scenario_schema_path: Path,
    wire_schema_dir: Path,
) -> dict[str, Any]:
    scenario = load_radar_scenario(scenario_path, scenario_schema_path)
    clock = SimClock(scenario.start_at)
    bus = InMemoryEventBus()
    radar = RadarSimulatorDriver(scenario, clock)
    await radar.connect()
    context = PluginContext(
        run_id=str(radar.run_id),
        mode=RuntimeMode.SIM,
        clock=clock,
        event_bus=bus,
        config={},
    )
    tracker = RadarTrackerPlugin(
        config=RadarTrackerConfig(),
        event_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase4/perception/event-id")
        ),
        track_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase4/perception/track-id")
        ),
    )
    weather = WeatherEstimatorPlugin(
        config=WeatherEstimatorConfig(
            frame_id=scenario.frame_id,
            coverage_min_enu_m=(-500.0, -500.0, 0.0),
            coverage_max_enu_m=(500.0, 500.0, 300.0),
        ),
        event_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase4/weather/event-id")
        ),
    )
    await tracker.initialize(context)
    await weather.initialize(context)
    weather_truth = WeatherTruth(
        wind_enu_m_s=(5.0, -0.8, 0.2),
        gust_m_s=8.0,
        temperature_deg_c=31.0,
        relative_humidity_pct=78.0,
        precipitation_mm_h=2.0,
        visibility_m=4_500.0,
        pressure_pa=100_420.0,
    )
    weather_event = WeatherSensorModel(
        seed=scenario.seed,
        sensor_id="weather-station-01",
        frame_id=scenario.frame_id,
        vehicle_id=scenario.vehicle_id,
        run_id=radar.run_id,
        clock=clock,
    ).sample(weather_truth, sequence=0)
    environment = await weather.estimate([weather_event])
    track_events: list[Envelope] = []
    async for sensor_event in radar.samples():
        track_events.append(await tracker.process(sensor_event))
    tracks = track_events[-1]

    config_hash = "sha256:" + hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    store = TwinStateStore(
        twin_id=f"{scenario.scenario_id}/{scenario.vehicle_id}",
        frame_id=scenario.frame_id,
        map_version="phase4-map@1.0.0",
        config_hash=config_hash,
    )
    store.apply(environment)
    for event in track_events:
        store.apply(event)
    snapshot = store.snapshot(clock.now())

    risk_engine = RiskEnginePlugin(
        config=RiskEngineConfig(),
        event_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase4/risk/event-id")
        ),
    )
    planner = RuleBasedPlannerPlugin(
        config=PlannerConfig(),
        event_ids=DeterministicUuid7Factory(
            RandomStream(scenario.seed, "phase4/planning/event-id")
        ),
    )
    await risk_engine.initialize(context)
    await planner.initialize(context)
    elapsed_s = (clock.now() - scenario.start_at).total_seconds()
    vehicle = VehicleKinematics(
        position_enu_m=(6.0 * elapsed_s, 0.0, 20.0),
        velocity_enu_m_s=(6.0, 0.0, 0.0),
        battery_pct=80.0,
    )
    risk = await risk_engine.assess(
        snapshot=snapshot,
        tracks=tracks,
        environment=environment,
        vehicle=vehicle,
    )
    path = await planner.plan(
        PlanRequest(
            mission_id="phase4-dynamic-crossing",
            vehicle_id=scenario.vehicle_id,
            twin_revision=snapshot.revision,
            start_enu_m=vehicle.position_enu_m,
            goal_enu_m=(120.0, 0.0, 20.0),
            frame_id=scenario.frame_id,
            risk=risk,
            deadline=clock.now() + timedelta(seconds=1),
        )
    )
    registry = SchemaRegistry(wire_schema_dir)
    registry.validate(risk.to_mapping())
    registry.validate(path.to_mapping())

    await risk_engine.shutdown(1.0)
    await planner.shutdown(1.0)
    await tracker.shutdown(1.0)
    await weather.shutdown(1.0)
    await radar.close()
    await bus.close()

    validation = path.payload["validation"]
    return {
        "scenario_id": scenario.scenario_id,
        "seed": scenario.seed,
        "twin_revision": snapshot.revision,
        "risk_score": risk.payload["score"],
        "risk_level": risk.payload["level"],
        "explanation_codes": [
            value["code"] for value in risk.payload["explanations"]
        ],
        "path_status": path.payload["status"],
        "path_waypoints": len(path.payload["waypoints"]),
        "collision_free": validation["collision_free"],
        "minimum_clearance_m": validation["minimum_clearance_m"],
        "risk_hash": _canonical_hash(risk.to_mapping()),
        "path_hash": _canonical_hash(path.to_mapping()),
    }


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="obstacle-phase4-demo")
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
        run_phase4_demo(args.scenario, args.scenario_schema, args.wire_schemas)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
