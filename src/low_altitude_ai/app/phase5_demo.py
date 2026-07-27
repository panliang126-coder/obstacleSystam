"""Run deterministic Phase 5 decision, authorization, and simulated control scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from low_altitude_ai.compat import UTC
from low_altitude_ai.control import SimulatedControlGateway
from low_altitude_ai.decision import (
    DecisionCenter,
    DecisionConfig,
    DecisionContext,
    SafetyContext,
    SafetyGate,
)
from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.schemas.registry import SchemaRegistry
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock

_SCENARIOS = ("continue", "avoid", "return", "land", "hold")


def _wire_time(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _inputs(
    scenario: str,
    index: int,
    clock: SimClock,
    run_id: Any,
) -> DecisionContext:
    now = clock.now()
    ids = DeterministicUuid7Factory(RandomStream(52001 + index, f"phase5/{scenario}/input"))
    trace_id = ids.new(now)
    risk_id = ids.new(now)
    twin_revision = index + 100
    risk_level = "LOW"
    score = 10.0
    collision = 5.0
    energy = 5.0
    battery_pct = 80.0
    if scenario == "avoid":
        risk_level = "CRITICAL"
        score = 80.0
        collision = 80.0
    elif scenario == "return":
        risk_level = "HIGH"
        score = 65.0
        energy = 65.0
        battery_pct = 25.0
    elif scenario == "land":
        risk_level = "CRITICAL"
        score = 95.0
        energy = 95.0
        battery_pct = 10.0
    risk = Envelope(
        schema="risk/1.0",
        event_id=ids.new(now),
        trace_id=trace_id,
        source=Source(
            service="risk-service",
            instance_id="phase5-demo",
            plugin="explainable_rule_risk",
            plugin_version="1.0.0",
        ),
        observed_at=now,
        received_at=now,
        monotonic_ns=clock.monotonic_ns(),
        run_id=run_id,
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=index,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "risk_id": str(risk_id),
            "subject": {"type": "VEHICLE", "id": "uav-001"},
            "twin_revision": twin_revision,
            "horizon_s": 15.0,
            "score": score,
            "level": risk_level,
            "dimensions": {
                "weather": 5.0,
                "collision": collision,
                "energy": energy,
                "communication": 5.0,
                "system": 5.0,
            },
            "explanations": [
                {
                    "code": "PHASE5_SCENARIO",
                    "severity": risk_level,
                    "summary": f"Deterministic {scenario} decision scenario.",
                    "evidence": {"scenario": scenario},
                }
            ],
            "valid_until": _wire_time(now + timedelta(seconds=5)),
            "recommended_constraints": [],
        },
    )
    path_id = ids.new(now)
    path_valid_until = (
        now - timedelta(milliseconds=1)
        if scenario == "hold"
        else now + timedelta(seconds=5)
    )
    path = Envelope(
        schema="path/1.0",
        event_id=ids.new(now),
        trace_id=trace_id,
        causation_id=risk.event_id,
        source=Source(
            service="planning-service",
            instance_id="phase5-demo",
            plugin="validated_detour_planner",
            plugin_version="1.0.0",
        ),
        observed_at=now,
        received_at=now,
        monotonic_ns=clock.monotonic_ns(),
        run_id=run_id,
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=index,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "path_id": str(path_id),
            "mission_id": f"phase5-{scenario}",
            "planner": {
                "name": "validated_detour_planner",
                "version": "1.0.0",
                "algorithm": "DETERMINISTIC_DETOUR",
            },
            "twin_revision": twin_revision,
            "risk_id": str(risk_id),
            "frame_id": "site-alpha-enu-v1",
            "waypoints": [
                {
                    "seq": 0,
                    "enu_m": [0.0, 0.0, 20.0],
                    "target_speed_m_s": 6.0,
                    "eta_s": 0.0,
                },
                {
                    "seq": 1,
                    "enu_m": [100.0, 25.0, 20.0],
                    "target_speed_m_s": 6.0,
                    "eta_s": 17.2,
                },
            ],
            "costs": {
                "distance": 103.1,
                "time": 17.2,
                "energy": 0.1,
                "risk": score,
                "total": 3.0,
            },
            "constraints_applied": [f"risk:{risk_id}"],
            "validation": {
                "collision_free": True,
                "dynamics_feasible": True,
                "geofence_valid": True,
                "minimum_clearance_m": 15.0,
            },
            "valid_until": _wire_time(path_valid_until),
            "status": "CANDIDATE",
        },
    )
    vehicle = Envelope(
        schema="vehicle.state/1.0",
        event_id=ids.new(now),
        trace_id=trace_id,
        causation_id=risk.event_id,
        source=Source(service="vehicle-state-service", instance_id="phase5-demo"),
        observed_at=now,
        received_at=now,
        monotonic_ns=clock.monotonic_ns(),
        run_id=run_id,
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=index,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "state_id": str(ids.new(now)),
            "twin_revision": twin_revision,
            "frame_id": "site-alpha-enu-v1",
            "position_enu_m": [0.0, 0.0, 20.0],
            "velocity_enu_m_s": [6.0, 0.0, 0.0],
            "battery_pct": battery_pct,
            "flight_mode": "MISSION",
            "armed": True,
            "link": {"healthy": True, "age_ms": 0.0},
            "failsafe": False,
            "safe_to_hold": True,
            "return_feasible": True,
            "landing_feasible": True,
            "capabilities": [
                "CONTINUE",
                "AVOID",
                "HOLD",
                "RETURN",
                "LAND",
                "ABORT",
            ],
            "updated_at": _wire_time(now),
        },
    )
    return DecisionContext(
        mission_id=f"phase5-{scenario}",
        risk=risk,
        path=path,
        vehicle_state=vehicle,
    )


def run_phase5_demo(wire_schema_dir: Path) -> dict[str, Any]:
    start_at = datetime(2026, 7, 27, 3, 20, 15, 250_000, tzinfo=UTC)
    registry = SchemaRegistry(wire_schema_dir)
    scenarios: dict[str, Any] = {}
    total_side_effects = 0
    for index, scenario in enumerate(_SCENARIOS):
        clock = SimClock(start_at)
        run_ids = DeterministicUuid7Factory(
            RandomStream(52001 + index, f"phase5/{scenario}/run")
        )
        context = _inputs(scenario, index, clock, run_ids.new(start_at))
        center = DecisionCenter(
            config=DecisionConfig(),
            clock=clock,
            event_ids=DeterministicUuid7Factory(
                RandomStream(52001 + index, f"phase5/{scenario}/decision")
            ),
        )
        gate = SafetyGate(
            gate_id="phase5-safety-gate",
            config=DecisionConfig(),
            clock=clock,
            event_ids=DeterministicUuid7Factory(
                RandomStream(52001 + index, f"phase5/{scenario}/gate")
            ),
        )
        gateway = SimulatedControlGateway(
            endpoint_id="phase5-sim-flight-controller",
            clock=clock,
            event_ids=DeterministicUuid7Factory(
                RandomStream(52001 + index, f"phase5/{scenario}/control")
            ),
        )
        proposal = center.decide(context)
        authorized = gate.authorize(
            proposal,
            SafetyContext(
                risk=context.risk,
                path=context.path,
                vehicle_state=context.vehicle_state,
                endpoint_id="phase5-sim-flight-controller",
                endpoint_kind="SIMULATED",
            ),
        )
        command, ack = gateway.dispatch(authorized)
        assert gateway.dispatch(authorized) == (command, ack)
        assert context.path is not None
        for event in (
            context.risk,
            context.path,
            context.vehicle_state,
            proposal,
            authorized,
            command,
            ack,
        ):
            registry.validate(event.to_mapping())
        traceable = (
            proposal.payload["risk_id"] == context.risk.payload["risk_id"]
            and proposal.payload["twin_revision"]
            == context.vehicle_state.payload["twin_revision"]
            and command.payload["decision_id"] == proposal.payload["decision_id"]
            and ack.payload["command_id"] == command.payload["command_id"]
        )
        total_side_effects += gateway.side_effect_count
        scenarios[scenario] = {
            "action": proposal.payload["action"],
            "authorization": authorized.payload["authorization"]["state"],
            "ack": ack.payload["status"],
            "traceable": traceable,
            "decision_hash": _canonical_hash(authorized.to_mapping()),
            "state": center.state.value,
        }
    return {
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "side_effect_count": total_side_effects,
        "real_endpoint_commands": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="obstacle-phase5-demo")
    parser.add_argument(
        "--wire-schemas",
        type=Path,
        default=project_root / "schemas" / "v1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(
        json.dumps(
            run_phase5_demo(args.wire_schemas),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
