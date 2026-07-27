import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.digital_twin import TwinSnapshot
from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.risk import RiskEngineConfig, RiskEnginePlugin, VehicleKinematics
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


def load_envelope(path: Path) -> Envelope:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    source = value["source"]
    quality = value["quality"]
    return Envelope(
        schema=value["schema"],
        event_id=UUID(value["event_id"]),
        trace_id=UUID(value["trace_id"]),
        causation_id=UUID(value["causation_id"]) if value["causation_id"] else None,
        source=Source(
            service=source["service"],
            instance_id=source["instance_id"],
            plugin=source["plugin"],
            plugin_version=source["plugin_version"],
        ),
        observed_at=datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00")),
        received_at=datetime.fromisoformat(value["received_at"].replace("Z", "+00:00")),
        monotonic_ns=value["monotonic_ns"],
        run_id=UUID(value["run_id"]),
        mode=RuntimeMode(value["mode"]),
        vehicle_id=value["vehicle_id"],
        sequence=value["sequence"],
        quality=Quality(
            valid=quality["valid"],
            confidence=quality["confidence"],
            flags=tuple(quality["flags"]),
            clock_uncertainty_ms=quality["clock_uncertainty_ms"],
        ),
        payload=value["payload"],
    )


def test_missing_weather_never_reduces_risk_to_low(project_root: Path) -> None:
    async def exercise() -> tuple[Envelope, Envelope]:
        tracks = load_envelope(project_root / "schemas/examples/valid/target.json")
        environment = load_envelope(project_root / "schemas/examples/valid/environment.json")
        no_threat_payload = deepcopy(tracks.payload)
        target = no_threat_payload["targets"][0]
        target["position"]["enu"] = {"east_m": 100.0, "north_m": 200.0, "up_m": 20.0}
        target["velocity"] = {
            "frame_id": "site-alpha-enu-v1",
            "east_m_s": 0.0,
            "north_m_s": 0.0,
            "up_m_s": 0.0,
            "covariance": [0.4, 0, 0, 0, 0.4, 0, 0, 0, 0.3],
        }
        tracks = replace(tracks, payload=no_threat_payload)
        benign_payload = deepcopy(environment.payload)
        benign_payload["risk_factors"] = {
            "wind": 0.0,
            "precipitation": 0.0,
            "visibility": 0.0,
            "icing": 0.0,
            "convective": 0.0,
            "uncertainty": 0.0,
        }
        environment = replace(environment, payload=benign_payload)
        clock = SimClock(environment.received_at)
        bus = InMemoryEventBus()
        snapshot = TwinSnapshot(
            twin_id="test/uav-001",
            revision=1,
            as_of=clock.now(),
            watermark=clock.now(),
            frame_id="site-alpha-enu-v1",
            map_version="test-map@1.0.0",
            config_hash="sha256:" + "a" * 64,
            vehicle_ref=None,
            sensor_refs=(),
            track_refs=("state://track/test/1",),
            environment_ref="state://environment/test/1",
            health_refs=(),
            input_refs=(tracks.event_id, environment.event_id),
            quality=Quality(valid=True, confidence=1.0),
        )
        plugin = RiskEnginePlugin(
            config=RiskEngineConfig(),
            event_ids=DeterministicUuid7Factory(RandomStream(9, "risk")),
        )
        await plugin.initialize(
            PluginContext(
                run_id=str(tracks.run_id),
                mode=RuntimeMode.SIM,
                clock=clock,
                event_bus=bus,
                config={},
            )
        )
        nominal = await plugin.assess(
            snapshot=snapshot,
            tracks=tracks,
            environment=environment,
            vehicle=VehicleKinematics(
                position_enu_m=(0.0, 0.0, 20.0),
                velocity_enu_m_s=(6.0, 0.0, 0.0),
                battery_pct=80.0,
            ),
        )
        unavailable = await plugin.assess(
            snapshot=snapshot,
            tracks=tracks,
            environment=replace(
                environment,
                quality=Quality(
                    valid=False,
                    confidence=0.0,
                    flags=("WEATHER_UNAVAILABLE",),
                ),
            ),
            vehicle=VehicleKinematics(
                position_enu_m=(0.0, 0.0, 20.0),
                velocity_enu_m_s=(6.0, 0.0, 0.0),
                battery_pct=80.0,
            ),
        )
        await plugin.shutdown(1.0)
        await bus.close()
        return nominal, unavailable

    nominal, unavailable = asyncio.run(exercise())

    assert nominal.payload["level"] == "LOW"
    assert unavailable.payload["level"] in {"HIGH", "CRITICAL"}
    assert unavailable.payload["dimensions"]["weather"] >= 55
    assert "ENVIRONMENT_UNKNOWN" in [
        value["code"] for value in unavailable.payload["explanations"]
    ]
