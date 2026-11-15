"""The brief, the plan and the decisions stay in step with each other and with the code."""

import re
from pathlib import Path

from meridian_oauth_radar.rules import RULES

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "oauth-radar"
ADR_DIR = PROJECT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"
LINK = re.compile(r"\]\(([^)\s]+)\)")
DATE_AND_STATUS = re.compile(r"\*\*Date:\*\* \d{4}-\d{2}-\d{2} · \*\*Status:\*\* \S")


def adr_files() -> list[Path]:
    files = sorted(ADR_DIR.glob("[0-9][0-9][0-9]-*.md"))
    assert files
    return files


def test_every_adr_is_in_the_index() -> None:
    index = INDEX.read_text(encoding="utf-8")
    listed = re.findall(r"^\| \[OAR-(\d{3})\]\((\d{3}-[^)]+\.md)\)", index, re.MULTILINE)
    assert [file for _, file in listed] == [path.name for path in adr_files()]
    assert all(number == file[:3] for number, file in listed)


def test_every_adr_has_a_title_a_date_and_a_status() -> None:
    for path in adr_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith(f"# OAR-{path.name[:3]}: "), path.name
        assert DATE_AND_STATUS.match(lines[2]), path.name
        text = "\n".join(lines)
        for part in ("**Context:**", "**Decision:**", "**Consequences:**"):
            assert part in text, (path.name, part)


def test_every_rule_is_in_oar_002() -> None:
    risk_model = (ADR_DIR / "002-risk-model-rules-first.md").read_text(encoding="utf-8")
    for rule in RULES:
        assert f"| `{rule}` |" in risk_model, rule


def test_relative_links_resolve() -> None:
    pages = [PROJECT / "BRIEF.md", PROJECT / "MILESTONES.md", *ADR_DIR.glob("*.md")]
    broken = []
    for page in pages:
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            if not (page.parent / target.split("#")[0]).exists():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")
    assert not broken


def test_the_lab_brief_links_here() -> None:
    brief = (ROOT / "docs" / "brief.md").read_text(encoding="utf-8")
    assert "](../oauth-radar/BRIEF.md)" in brief
