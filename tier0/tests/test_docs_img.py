"""The README's animated flow pictures: present, valid, small, accessible and reproducible."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tier0 import docs_img

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "docs/img"
NAMES = ("happy-path.svg", "happy-path-dark.svg", "attack-path.svg", "attack-path-dark.svg")
LIMIT = 30 * 1024
SVG = "{http://www.w3.org/2000/svg}"


@pytest.mark.parametrize("name", NAMES)
def test_each_svg_exists_parses_and_stays_small(name: str) -> None:
    path = IMG / name
    assert path.is_file(), name
    assert path.stat().st_size < LIMIT, (name, path.stat().st_size)
    root = ET.parse(path).getroot()
    assert root.tag == f"{SVG}svg"
    assert root.findtext(f"{SVG}title") and root.findtext(f"{SVG}desc")
    style = root.findtext(f"{SVG}style") or ""
    assert "@keyframes" in style  # CSS animation inside the SVG
    assert "@media (prefers-reduced-motion: reduce)" in style  # shows the final frame instead


def test_the_svgs_are_what_the_generator_makes() -> None:
    made = docs_img.files()
    assert sorted(made) == sorted(NAMES)
    for name, text in made.items():
        assert (IMG / name).read_text() == text, (
            f"docs/img/{name} drifted from its generator: run python -m tier0.docs_img"
        )


def test_the_readme_shows_both_with_a_dark_version_and_alt_text() -> None:
    readme = (ROOT / "README.md").read_text()
    for flow in ("happy-path", "attack-path"):
        assert f'srcset="docs/img/{flow}-dark.svg"' in readme
        assert f'<img src="docs/img/{flow}.svg" alt="Animation of ' in readme
    assert readme.index("happy-path.svg") < readme.index("## Why you can trust it")
    assert readme.index("## Why you can trust it") < readme.index("attack-path.svg")
    assert "```mermaid" not in readme  # the README's flow is the animated picture now
