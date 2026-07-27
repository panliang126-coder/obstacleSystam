from pathlib import Path

import pytest

from low_altitude_ai.app.phase5_demo import run_phase5_demo


@pytest.mark.integration
def test_phase5_exit_scenarios_are_authorized_traceable_and_deterministic(
    schema_dir: Path,
) -> None:
    first = run_phase5_demo(schema_dir)
    second = run_phase5_demo(schema_dir)

    assert first == second
    assert first["scenario_count"] == 5
    assert first["side_effect_count"] == 5
    assert first["real_endpoint_commands"] == 0
    expected = {
        "continue": "CONTINUE",
        "avoid": "AVOID",
        "return": "RETURN",
        "land": "LAND",
        "hold": "HOLD",
    }
    for scenario, action in expected.items():
        result = first["scenarios"][scenario]
        assert result["action"] == action
        assert result["authorization"] == "AUTHORIZED"
        assert result["ack"] == "COMPLETED"
        assert result["traceable"]
