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


@pytest.fixture(scope="session")
def keyed_world_dir(world: AccessWorld, tmp_path_factory: pytest.TempPathFactory):
    """The tuning world written to a directory, answer key included (scoring tests only)."""
    from meridian_dataset.access import write_access

    out = tmp_path_factory.mktemp("tuning-world")
    write_access(world, out)
    return out


@pytest.fixture(scope="session")
def world_dir(keyed_world_dir, tmp_path_factory: pytest.TempPathFactory):
    """The same world without answer_key.json: collectors and matchers must work on this."""
    import shutil

    out = tmp_path_factory.mktemp("tuning-world-nokey") / "world"
    shutil.copytree(keyed_world_dir, out, ignore=shutil.ignore_patterns("answer_key.json"))
    assert not (out / "answer_key.json").exists()
    return out


@pytest.fixture(autouse=True)
def _no_real_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test can make a real Claude call: no key in the environment, and the SDK's client
    class raises when built. Tests that need a client pass a fake one."""
    import anthropic

    def _refuse(*args, **kwargs):
        raise AssertionError("a test tried to build a real anthropic.Anthropic client")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", _refuse)
