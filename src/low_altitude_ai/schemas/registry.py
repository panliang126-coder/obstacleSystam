"""JSON Schema 2020-12 registry for versioned event contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


class SchemaValidationError(ValueError):
    """A message did not satisfy its declared contract."""


class SchemaRegistry:
    """Load all ``*.schema.json`` documents in one version directory."""

    def __init__(self, schema_dir: Path) -> None:
        self.schema_dir = schema_dir.resolve()
        documents = [
            self._read_json(path)
            for path in sorted(self.schema_dir.glob("*.schema.json"))
        ]
        if not documents:
            raise FileNotFoundError(f"no schemas found in {self.schema_dir}")

        resources: list[tuple[str, Resource[Any]]] = []
        self._schemas_by_wire_name: dict[str, Mapping[str, Any]] = {}
        for document in documents:
            schema_id = document.get("$id")
            if not isinstance(schema_id, str):
                raise ValueError("every schema document must contain a string $id")
            resources.append((schema_id, Resource.from_contents(document)))

            wire_name = document.get("x-wire-schema")
            if isinstance(wire_name, str):
                if wire_name in self._schemas_by_wire_name:
                    raise ValueError(f"duplicate wire schema {wire_name}")
                self._schemas_by_wire_name[wire_name] = document

        self._registry = Registry().with_resources(resources)
        for document in documents:
            Draft202012Validator.check_schema(document)

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return value

    @property
    def wire_schemas(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas_by_wire_name))

    def validate(self, instance: Mapping[str, Any]) -> None:
        wire_name = instance.get("schema")
        if not isinstance(wire_name, str):
            raise SchemaValidationError("message must declare a string schema")
        try:
            schema = self._schemas_by_wire_name[wire_name]
        except KeyError as error:
            raise SchemaValidationError(f"unknown wire schema {wire_name!r}") from error

        validator = Draft202012Validator(
            schema,
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "<root>"
            raise SchemaValidationError(f"{wire_name} at {location}: {first.message}") from first

    def is_valid(self, instance: Mapping[str, Any]) -> bool:
        try:
            self.validate(instance)
        except SchemaValidationError:
            return False
        return True
