"""The package imports and the access world it will read generates."""

import meridian_access
from meridian_dataset.access import generate_access


def test_package_imports() -> None:
    assert meridian_access.__doc__


def test_the_access_world_generates() -> None:
    world = generate_access()
    assert len(world.exports) == 95
    assert len(world.answer_key.true_roles) == 230
