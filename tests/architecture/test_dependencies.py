import ast
from pathlib import Path

import pytest

FORBIDDEN_DOMAIN_PREFIXES = (
    "low_altitude_ai.adapters",
    "low_altitude_ai.app",
    "low_altitude_ai.ports",
    "low_altitude_ai.schemas",
)
FORBIDDEN_PORT_PREFIXES = (
    "low_altitude_ai.adapters",
    "low_altitude_ai.app",
    "low_altitude_ai.schemas",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def violations(directory: Path, forbidden: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for path in directory.rglob("*.py"):
        for module in imported_modules(path):
            if module.startswith(forbidden):
                result.append(f"{path.name} imports {module}")
    return result


@pytest.mark.architecture
def test_domain_has_no_outward_project_dependencies(project_root: Path) -> None:
    domain = project_root / "src" / "low_altitude_ai" / "domain"

    assert not violations(domain, FORBIDDEN_DOMAIN_PREFIXES)


@pytest.mark.architecture
def test_ports_do_not_depend_on_adapters_or_composition(project_root: Path) -> None:
    ports = project_root / "src" / "low_altitude_ai" / "ports"

    assert not violations(ports, FORBIDDEN_PORT_PREFIXES)
