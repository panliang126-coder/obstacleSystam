"""Run the deterministic Phase 2 sensor-to-Twin demonstration."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.digital_twin import TwinIngestService, TwinStateStore
from low_altitude_ai.domain import Envelope
from low_altitude_ai.schemas.registry import SchemaRegistry
from low_altitude_ai.simulator import (
    DeterministicUuid7Factory,
    RadarSimulatorDriver,
    RandomStream,
    SimClock,
    load_radar_scenario,
)


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


async def run_phase2_demo(
    scenario_path: Path,
    scenario_schema_path: Path,
    wire_schema_dir: Path,
) -> dict[str, Any]:
    scenario = load_radar_scenario(scenario_path, scenario_schema_path)
    config_hash = "sha256:" + hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    clock = SimClock(scenario.start_at)
    bus = InMemoryEventBus(queue_size=32)
    driver = RadarSimulatorDriver(scenario, clock)
    store = TwinStateStore(
        twin_id=f"{scenario.scenario_id}/{scenario.vehicle_id}",
        frame_id=scenario.frame_id,
        map_version="phase2-map@1.0.0",
        config_hash=config_hash,
    )
    twin_ids = DeterministicUuid7Factory(
        RandomStream(scenario.seed, "digital-twin/event-id")
    )
    service = TwinIngestService(
        store=store,
        event_bus=bus,
        clock=clock,
        event_id_factory=twin_ids.new,
    )
    sensor_events: list[Envelope] = []
    snapshot_events: list[Envelope] = []

    async def collect_snapshot(event: Envelope) -> None:
        snapshot_events.append(event)

    await service.start()
    snapshot_subscription = bus.subscribe(
        "twin.snapshot",
        collect_snapshot,
        group="phase2-demo",
    )
    registry = SchemaRegistry(wire_schema_dir)
    await driver.connect()
    async for event in driver.samples():
        registry.validate(event.to_mapping())
        sensor_events.append(event)
        await bus.publish("sensor.normalized.radar", event)
    await bus.drain()
    for event in snapshot_events:
        registry.validate(event.to_mapping())
    final_snapshot = store.snapshot(clock.now())
    await driver.close()
    await service.stop()
    await snapshot_subscription.close()
    await bus.close()

    return {
        "scenario_id": scenario.scenario_id,
        "seed": scenario.seed,
        "sensor_events": len(sensor_events),
        "twin_events": len(snapshot_events),
        "twin_revision": final_snapshot.revision,
        "sensor_event_hash": _canonical_hash(
            [event.to_mapping() for event in sensor_events]
        ),
        "twin_snapshot_hash": final_snapshot.stable_hash(),
    }


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="obstacle-phase2-demo")
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
        run_phase2_demo(
            args.scenario,
            args.scenario_schema,
            args.wire_schemas,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
