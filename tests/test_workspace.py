"""The workspace installs both packages on the pinned Python version (ADR-012)."""

import sys


def test_runs_on_python_3_13():
    assert sys.version_info[:2] == (3, 13)


def test_both_packages_import():
    import meridian_dataset
    import tier0

    assert meridian_dataset.__doc__
    assert tier0.__doc__
