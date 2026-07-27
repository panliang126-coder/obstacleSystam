"""Command line utilities for contract validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from low_altitude_ai.schemas.registry import SchemaRegistry, SchemaValidationError


def _read_instance(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        instance = json.load(stream)
    if not isinstance(instance, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return instance


def validate_examples(schema_dir: Path, examples_dir: Path) -> int:
    registry = SchemaRegistry(schema_dir)
    valid_paths = sorted((examples_dir / "valid").glob("*.json"))
    invalid_paths = sorted((examples_dir / "invalid").glob("*.json"))
    if not valid_paths or not invalid_paths:
        raise FileNotFoundError(
            "both valid and invalid example directories must contain JSON files"
        )

    failures: list[str] = []
    for path in valid_paths:
        try:
            registry.validate(_read_instance(path))
        except (SchemaValidationError, ValueError) as error:
            failures.append(f"expected valid: {path}: {error}")

    for path in invalid_paths:
        try:
            registry.validate(_read_instance(path))
        except SchemaValidationError:
            continue
        except ValueError as error:
            failures.append(f"invalid fixture is unreadable: {path}: {error}")
        else:
            failures.append(f"expected invalid: {path}")

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print(
        f"validated {len(valid_paths)} valid and {len(invalid_paths)} invalid examples "
        f"against {len(registry.wire_schemas)} wire schemas"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obstacle-schema")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-examples")
    validate.add_argument("--schema-dir", type=Path, default=Path("schemas/v1"))
    validate.add_argument("--examples-dir", type=Path, default=Path("schemas/examples"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-examples":
        return validate_examples(args.schema_dir, args.examples_dir)
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
