import json
from pathlib import Path
from typing import Any

import pytest

from low_altitude_ai.schemas.cli import main, validate_examples
from low_altitude_ai.schemas.registry import SchemaRegistry, SchemaValidationError

EXPECTED_SCHEMAS = {
    "control.ack/1.0",
    "control.command/1.0",
    "decision/1.0",
    "environment/1.0",
    "health/1.0",
    "mission.command/1.0",
    "path/1.0",
    "risk/1.0",
    "sensor/1.0",
    "target/1.0",
    "twin.snapshot/1.0",
    "vehicle.state/1.0",
}


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def registry(schema_dir: Path) -> SchemaRegistry:
    return SchemaRegistry(schema_dir)


@pytest.mark.contract
def test_registry_exposes_all_core_wire_schemas(registry: SchemaRegistry) -> None:
    assert set(registry.wire_schemas) == EXPECTED_SCHEMAS


@pytest.mark.contract
def test_all_valid_examples_satisfy_declared_schema(
    registry: SchemaRegistry,
    examples_dir: Path,
) -> None:
    examples = sorted((examples_dir / "valid").glob("*.json"))
    assert len(examples) == len(EXPECTED_SCHEMAS)

    for path in examples:
        registry.validate(read_json(path))


@pytest.mark.contract
def test_all_invalid_examples_are_rejected(
    registry: SchemaRegistry,
    examples_dir: Path,
) -> None:
    examples = sorted((examples_dir / "invalid").glob("*.json"))
    assert len(examples) == len(EXPECTED_SCHEMAS)

    for path in examples:
        with pytest.raises(SchemaValidationError):
            registry.validate(read_json(path))


@pytest.mark.contract
def test_unknown_schema_is_rejected(registry: SchemaRegistry) -> None:
    with pytest.raises(SchemaValidationError, match="unknown wire schema"):
        registry.validate({"schema": "unknown/1.0"})
    assert not registry.is_valid({"schema": "unknown/1.0"})


def test_cli_validates_fixture_directories(schema_dir: Path, examples_dir: Path) -> None:
    assert validate_examples(schema_dir, examples_dir) == 0
    assert (
        main(
            [
                "validate-examples",
                "--schema-dir",
                str(schema_dir),
                "--examples-dir",
                str(examples_dir),
            ]
        )
        == 0
    )
