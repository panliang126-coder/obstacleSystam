import asyncio
from pathlib import Path

import pytest

from low_altitude_ai.app.phase3_demo import run_phase3_demo


@pytest.mark.integration
def test_phase3_pipeline_is_reproducible_and_meets_baseline_accuracy(
    scenario_path: Path,
    scenario_schema: Path,
    schema_dir: Path,
) -> None:
    first = asyncio.run(run_phase3_demo(scenario_path, scenario_schema, schema_dir))
    second = asyncio.run(run_phase3_demo(scenario_path, scenario_schema, schema_dir))

    assert first == second
    assert first["track_events"] == 10
    assert first["confirmed_track_events"] >= 8
    assert first["environment_events"] == 1
    assert first["twin_revision"] == 11
    assert first["position_rmse_m"] <= 2.0
    assert first["wind_error_m_s"] <= 2.0
