import asyncio
from pathlib import Path

import pytest

from low_altitude_ai.app.phase4_demo import run_phase4_demo


@pytest.mark.integration
def test_phase4_dynamic_crossing_produces_explainable_collision_free_detour(
    scenario_path: Path,
    scenario_schema: Path,
    schema_dir: Path,
) -> None:
    first = asyncio.run(run_phase4_demo(scenario_path, scenario_schema, schema_dir))
    second = asyncio.run(run_phase4_demo(scenario_path, scenario_schema, schema_dir))

    assert first == second
    assert first["risk_level"] in {"HIGH", "CRITICAL"}
    assert "CLOSING_TRACK" in first["explanation_codes"]
    assert first["path_status"] == "CANDIDATE"
    assert first["path_waypoints"] == 4
    assert first["collision_free"]
    assert first["minimum_clearance_m"] >= 10.0
