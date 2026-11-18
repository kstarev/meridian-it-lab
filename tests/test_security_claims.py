"""The security baseline's claims (docs/brief.md, docs/security.md, ADR-011) match the repo.

The checks no workflow runs are listed as planned, and the ones in place are the ones the
workflows and .github/dependabot.yml actually have. If a workflow starts running CodeQL,
gitleaks, zizmor or an SBOM tool, or Dependabot's file goes, a test here fails until the pages
say so.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
PLANNED = {
    "CodeQL": r"codeql",
    "gitleaks": r"gitleaks",
    "zizmor": r"zizmor",
    "an SBOM": r"sbom|syft|cyclonedx|spdx",
}
PAGES = {
    "brief": ROOT / "docs/brief.md",
    "security": ROOT / "docs/security.md",
    "ADR-011": ROOT / "docs/decisions.md",
}


def _workflows() -> str:
    return "\n".join(p.read_text() for p in sorted(WORKFLOWS.glob("*.y*ml"))).lower()


def _adr_011() -> str:
    text = PAGES["ADR-011"].read_text()
    return text.split("## ADR-011:", 1)[1].split("\n## ADR-012", 1)[0]


def test_the_checks_no_workflow_runs_are_listed_as_planned() -> None:
    for name, pattern in PLANNED.items():
        assert not re.search(pattern, _workflows()), f"a workflow runs {name}: update the pages"
    brief = PAGES["brief"].read_text()
    assert "Planned, not built: CodeQL, gitleaks, zizmor and an SBOM." in brief
    assert "CI runs CodeQL" not in brief and "checked by zizmor" not in brief
    assert "- **Planned, not built:** CodeQL, gitleaks, zizmor and an SBOM." in _adr_011()
    security = PAGES["security"].read_text()
    planned = security.split("Planned, not built:", 1)[1]
    for row in ("| CodeQL |", "| gitleaks |", "| zizmor |", "| SBOM |"):
        assert row in planned and row not in security.split("Planned, not built:", 1)[0]


def test_dependabot_version_updates_are_configured_as_the_pages_say() -> None:
    config = (ROOT / ".github/dependabot.yml").read_text()
    ecosystems = re.findall(r'package-ecosystem:\s*"([^"]+)"', config)
    assert sorted(ecosystems) == ["github-actions", "uv"]
    text = "Dependabot version updates are configured for `github-actions` and `uv`"
    assert text in PAGES["brief"].read_text() and text in _adr_011()
    assert "| Dependabot version updates |" in PAGES["security"].read_text()


def test_the_controls_in_place_name_the_test_that_checks_them() -> None:
    assert (ROOT / "tests/test_workflow_hardening.py").exists()
    pinned = "every action is pinned to a commit SHA, checked by `tests/test_workflow_hardening.py`"
    assert pinned in PAGES["brief"].read_text() and pinned in _adr_011()
