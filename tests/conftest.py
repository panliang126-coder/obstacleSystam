from datetime import datetime
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
