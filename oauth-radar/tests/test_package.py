"""The package imports, and the root workspace installs it and runs these tests (OAR-001)."""

import tomllib
from pathlib import Path

import meridian_oauth_radar

ROOT = Path(__file__).resolve().parents[2]


def test_package_imports() -> None:
    assert meridian_oauth_radar.__doc__
    assert "OAR-004" in meridian_oauth_radar.__doc__


def test_the_root_workspace_includes_this_package() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "oauth-radar" in root["tool"]["uv"]["workspace"]["members"]
    assert "meridian-oauth-radar" in root["project"]["dependencies"]
    assert root["tool"]["uv"]["sources"]["meridian-oauth-radar"] == {"workspace": True}
    assert "oauth-radar/tests" in root["tool"]["pytest"]["ini_options"]["testpaths"]
    assert "oauth-radar/src" in root["tool"]["ruff"]["src"]
