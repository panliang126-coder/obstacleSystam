from datetime import datetime, timedelta
from pathlib import Path

import pytest

from low_altitude_ai.domain import Envelope, Quality, RuntimeMode, Source
from low_altitude_ai.domain.identifiers import uuid7


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def schema_dir(project_root: Path) -> Path:
    return project_root / "schemas" / "v1"


@pytest.fixture(scope="session")
def examples_dir(project_root: Path) -> Path:
    return project_root / "schemas" / "examples"


@pytest.fixture(scope="session")
def scenario_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "dynamic-crossing-v1.json"


@pytest.fixture(scope="session")
def scenario_schema(project_root: Path) -> Path:
    return project_root / "configs" / "scenario.schema.json"


@pytest.fixture
def sensor_event() -> Envelope:
    observed_at = datetime.fromisoformat("2026-07-27T03:20:15.000+00:00")
    event_id = uuid7()
    return Envelope(
        schema="sensor/1.0",
        event_id=event_id,
        trace_id=event_id,
        source=Source(
            service="simulator-service",
            instance_id="test",
            plugin="radar-sim",
            plugin_version="1.0.0",
        ),
        observed_at=observed_at,
        received_at=observed_at,
        monotonic_ns=0,
        run_id=uuid7(),
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=0,
        quality=Quality(valid=True, confidence=1.0, clock_uncertainty_ms=0.0),
        payload={
            "sensor_id": "radar-front-01",
            "sensor_type": "RADAR",
            "source_session_id": str(uuid7()),
            "frame_id": "site-alpha-enu-v1",
            "calibration_id": "radar-front-cal-v1",
            "sample_format": "radar_detections_v1",
            "sample": {"scan_id": 0, "detections": []},
            "raw_ref": None,
            "diagnostics": {},
        },
    )


@pytest.fixture
def risk_event(sensor_event: Envelope) -> Envelope:
    return Envelope(
        schema="risk/1.0",
        event_id=uuid7(),
        trace_id=sensor_event.trace_id,
        causation_id=sensor_event.event_id,
        source=Source(
            service="risk-service",
            instance_id="test",
            plugin="explainable_rule_risk",
            plugin_version="1.0.0",
        ),
        observed_at=sensor_event.observed_at,
        received_at=sensor_event.received_at,
        monotonic_ns=0,
        run_id=sensor_event.run_id,
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=0,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "risk_id": str(uuid7()),
            "subject": {"type": "VEHICLE", "id": "uav-001"},
            "twin_revision": 1,
            "horizon_s": 15.0,
            "score": 80.0,
            "level": "CRITICAL",
            "dimensions": {
                "weather": 0.0,
                "collision": 80.0,
                "energy": 0.0,
                "communication": 0.0,
                "system": 0.0,
            },
            "explanations": [],
            "valid_until": (
                sensor_event.received_at + timedelta(seconds=10)
            ).isoformat().replace("+00:00", "Z"),
            "recommended_constraints": [
                {
                    "type": "EXCLUSION_CYLINDER",
                    "center_enu_m": [50.0, 0.0, 20.0],
                    "radius_m": 20.0,
                    "valid_for_s": 5.0,
                }
            ],
        },
    )


@pytest.fixture
def path_event(risk_event: Envelope) -> Envelope:
    return Envelope(
        schema="path/1.0",
        event_id=uuid7(),
        trace_id=risk_event.trace_id,
        causation_id=risk_event.event_id,
        source=Source(
            service="planning-service",
            instance_id="test",
            plugin="validated_detour_planner",
            plugin_version="1.0.0",
        ),
        observed_at=risk_event.observed_at,
        received_at=risk_event.received_at,
        monotonic_ns=0,
        run_id=risk_event.run_id,
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=0,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "path_id": str(uuid7()),
            "mission_id": "test-mission",
            "planner": {
                "name": "validated_detour_planner",
                "version": "1.0.0",
                "algorithm": "DETERMINISTIC_DETOUR",
            },
            "twin_revision": risk_event.payload["twin_revision"],
            "risk_id": risk_event.payload["risk_id"],
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
                    "enu_m": [100.0, 30.0, 20.0],
                    "target_speed_m_s": 6.0,
                    "eta_s": 17.4,
                },
            ],
            "costs": {
                "distance": 104.4,
                "time": 17.4,
                "energy": 0.104,
                "risk": 80.0,
                "total": 3.0,
            },
            "constraints_applied": ["risk:test", "twin-revision:1"],
            "validation": {
                "collision_free": True,
                "dynamics_feasible": True,
                "geofence_valid": True,
                "minimum_clearance_m": 10.0,
            },
            "valid_until": (
                risk_event.received_at + timedelta(seconds=10)
            ).isoformat().replace("+00:00", "Z"),
            "status": "CANDIDATE",
        },
    )


@pytest.fixture
def vehicle_state_event(risk_event: Envelope) -> Envelope:
    return Envelope(
        schema="vehicle.state/1.0",
        event_id=uuid7(),
        trace_id=risk_event.trace_id,
        causation_id=risk_event.event_id,
        source=Source(service="vehicle-state-service", instance_id="test"),
        observed_at=risk_event.observed_at,
        received_at=risk_event.received_at,
        monotonic_ns=0,
        run_id=risk_event.run_id,
        mode=RuntimeMode.SIM,
        vehicle_id="uav-001",
        sequence=0,
        quality=Quality(valid=True, confidence=1.0),
        payload={
            "state_id": str(uuid7()),
            "twin_revision": risk_event.payload["twin_revision"],
            "frame_id": "site-alpha-enu-v1",
            "position_enu_m": [0.0, 0.0, 20.0],
            "velocity_enu_m_s": [6.0, 0.0, 0.0],
            "battery_pct": 80.0,
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
            "updated_at": risk_event.received_at.isoformat().replace("+00:00", "Z"),
        },
    )
