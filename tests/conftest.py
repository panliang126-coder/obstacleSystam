from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def schema_dir(project_root: Path) -> Path:
    return project_root / "schemas" / "v1"


@pytest.fixture(scope="session")
def examples_dir(project_root: Path) -> Path:
    return project_root / "schemas" / "examples"
