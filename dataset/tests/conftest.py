"""Shared fixtures: the Access-as-Code tuning world is built once per test session."""

from pathlib import Path

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
