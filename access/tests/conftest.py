"""Session-cached tuning world, policy and desired state (tuning only; never a sealed recipe)."""

import pytest

from meridian_access.desired import DesiredState, desired_state
from meridian_access.policy import Policy, load_policy
from meridian_dataset.access import AccessWorld, generate_access


@pytest.fixture(scope="session")
def world() -> AccessWorld:
    return generate_access()


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load_policy()


@pytest.fixture(scope="session")
def state(world: AccessWorld, policy: Policy) -> DesiredState:
    return desired_state(world.hris.records, world.hris.events, policy)
