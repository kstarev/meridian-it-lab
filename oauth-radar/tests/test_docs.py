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
    pages = [PROJECT / "BRIEF.md", PROJECT / "MILESTONES.md", *PROJECT.glob("docs/**/*.md")]
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


# The only statistics the brief may carry, in the wording checked against each primary source
# on 2026-11-19, each with its link. A new number or source needs the owner's check first.
CHECKED_FINDINGS = (
    (
        "Push Security's telemetry for an average week in April 2026: the average organisation "
        "had 17 AI OAuth integrations connected to Google Workspace and Microsoft 365, most of "
        "them unapproved.",
        "https://pushsecurity.com/blog/what-push-data-reveals-about-the-state-of-shadow-ai",
    ),
    (
        "Material Security analysed 22,332 OAuth-connected apps in 21 enterprise Google "
        "Workspace environments (June 2026): 47.2% had no active use in the past 90 days, with "
        "their authorisations still in place.",
        "https://material.security/resources/the-access-you-forgot-you-granted-materials-oauth-risk-report",
    ),
    (
        "UpGuard's State of Shadow AI report (November 2025): 45% of workers find workarounds "
        "to reach blocked applications.",
        "https://www.upguard.com/resources/the-state-of-shadow-ai",
    ),
)


def test_why_its_real_carries_only_the_checked_findings() -> None:
    brief = (PROJECT / "BRIEF.md").read_text(encoding="utf-8")
    section = brief.split("## Why it's real\n", 1)[1].split("\n## ", 1)[0]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(bullets) == len(CHECKED_FINDINGS)
    for line, (text, url) in zip(bullets, CHECKED_FINDINGS, strict=True):
        assert line.startswith(f"- {text} (["), line
        assert line.endswith(f"]({url}))"), line
    # No other number anywhere in the section, outside the links.
    for _, url in CHECKED_FINDINGS:
        section = section.replace(url, "")
    allowed = set(re.findall(r"\d[\d,.]*\d|\d", " ".join(text for text, _ in CHECKED_FINDINGS)))
    assert set(re.findall(r"\d[\d,.]*\d|\d", section)) <= allowed
