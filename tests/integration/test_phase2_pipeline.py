import asyncio
from pathlib import Path

import pytest

from low_altitude_ai.app.phase2_demo import run_phase2_demo


@pytest.mark.integration
def test_sensor_to_twin_pipeline_is_reproducible(
    scenario_path: Path,
    scenario_schema: Path,
    schema_dir: Path,
) -> None:
    first = asyncio.run(run_phase2_demo(scenario_path, scenario_schema, schema_dir))
    second = asyncio.run(run_phase2_demo(scenario_path, scenario_schema, schema_dir))

    assert first == second
    assert first["sensor_events"] == 10
    assert first["twin_events"] == 10
    assert first["twin_revision"] == 10
    assert str(first["sensor_event_hash"]).startswith("sha256:")
    assert str(first["twin_snapshot_hash"]).startswith("sha256:")
