"""Shared fixtures: the Access-as-Code tuning world is built once per test session."""

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.access import ACCESS_TUNING_SEED, AccessWorld, generate_access, write_access


@pytest.fixture(scope="session")
def access_world() -> AccessWorld:
    return generate_access(ACCESS_TUNING_SEED)


@pytest.fixture(scope="session")
def access_files(
    access_world: AccessWorld, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Path]:
    return write_access(access_world, tmp_path_factory.mktemp("access"))


@pytest.fixture
def tuning_object() -> dict[str, Any]:
    """A fresh copy of the tuning recipe as a JSON object, for tests to change."""
    path = resources.files("meridian_dataset.access").joinpath("recipes", "tuning.json")
    return json.loads(path.read_text(encoding="utf-8"))
