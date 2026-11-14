"""The README's held-out v3 summary: generated from the published comparison, never drifting."""

import json
import shutil
from pathlib import Path

import pytest

from tier0.evals import compare_many as cm
from tier0.evals import readme_blocks, summary

ROOT = Path(__file__).resolve().parents[2]
COMPARISON = ROOT / "docs/evals/2026-11-12-held-out-v3-comparison.json"


def _rows(block: str) -> dict[str, list[str]]:
    rows = {}
    for line in block.splitlines():
        if line.startswith("| ["):
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            rows[cells[0].split("](")[0]] = cells
    return rows


def test_the_readme_summary_matches_the_published_comparison():
    readme = (ROOT / "README.md").read_text()
    assert readme.count(cm.SUMMARY_START) == 1
    assert readme.count(cm.SUMMARY_END) == 1
    assert readme_blocks.block_between(readme, cm.SUMMARY_START, cm.SUMMARY_END) == summary.render(
        COMPARISON, ROOT
    ), (
        "README's v3 summary drifted from the published comparison: run python -m "
        "tier0.evals.summary docs/evals/2026-11-12-held-out-v3-comparison.json "
        "--readme README.md"
    )


def test_the_summary_agrees_with_the_generated_comparison_block():
    readme = (ROOT / "README.md").read_text()
    mine = _rows(readme_blocks.block_between(readme, cm.SUMMARY_START, cm.SUMMARY_END))
    m3 = _rows(readme_blocks.block_between(readme, cm.README_START, cm.README_END))
    assert len(mine) == 7
    for setup, cells in mine.items():
        assert setup in m3
        # pass^3, everyday, policy violations, data exposed, cost, latency
        for cell in (cells[2], cells[3], cells[4], cells[5], cells[9]):
            assert cell in m3[setup], (setup, cell)
        if setup.lstrip("[") in ("haiku", "sonnet"):
            assert cells[8] in m3[setup], (setup, cells[8])
        else:
            assert cells[8] == f"$0 (local; {cm.LOCAL_COST_NOTE})"


def test_replace_summary_block_leaves_the_other_blocks_alone():
    m2 = "<!-- comparison:start -->\nm2\n<!-- comparison:end -->"
    m3 = cm.README_START + "\nm3\n" + cm.README_END
    old = cm.SUMMARY_START + "\nold\n" + cm.SUMMARY_END
    new = cm.SUMMARY_START + "\nnew\n" + cm.SUMMARY_END
    out = cm.replace_summary_block(f"top\n{m2}\nmid\n{m3}\nlow\n{old}\nend\n", new)
    assert m2 in out and m3 in out
    assert "old" not in out
    assert new in out
    assert out.startswith("top\n") and "\nmid\n" in out and out.endswith("\nend\n")


def test_replace_summary_block_needs_its_markers():
    with pytest.raises(ValueError):
        cm.replace_summary_block("no markers here", "block")


def test_the_readme_cost_table_matches_the_published_comparison():
    readme = (ROOT / "README.md").read_text()
    assert readme.count(cm.COST_START) == 1
    assert readme.count(cm.COST_END) == 1
    assert readme_blocks.block_between(readme, cm.COST_START, cm.COST_END) == summary.render_cost(
        COMPARISON, ROOT
    ), (
        "README's v3 cost table drifted from the published comparison: run python -m "
        "tier0.evals.summary docs/evals/2026-11-12-held-out-v3-comparison.json "
        "--readme README.md"
    )


def test_the_cost_table_agrees_with_the_generated_comparison_block():
    readme = (ROOT / "README.md").read_text()
    cost = _rows(readme_blocks.block_between(readme, cm.COST_START, cm.COST_END))
    m3 = _rows(readme_blocks.block_between(readme, cm.README_START, cm.README_END))
    for setup in ("haiku", "sonnet"):
        key = f"[{setup}"
        assert key in cost and key in m3, setup
        for cell in (cost[key][1], cost[key][2]):
            assert cell in m3[key], (setup, cell)


def test_replace_cost_block_leaves_the_other_blocks_alone():
    m2 = "<!-- comparison:start -->\nm2\n<!-- comparison:end -->"
    m3 = cm.README_START + "\nm3\n" + cm.README_END
    summ = cm.SUMMARY_START + "\nsum\n" + cm.SUMMARY_END
    old = cm.COST_START + "\nold\n" + cm.COST_END
    new = cm.COST_START + "\nnew\n" + cm.COST_END
    out = cm.replace_cost_block(f"top\n{m2}\n{m3}\n{summ}\n{old}\nend\n", new)
    assert m2 in out and m3 in out and summ in out
    assert "old" not in out
    assert new in out
    assert out.startswith("top\n") and out.endswith("\nend\n")


def test_replace_cost_block_needs_its_markers():
    with pytest.raises(ValueError):
        cm.replace_cost_block("no markers here", "block")


_MARKERS = (
    "intro\n"
    + cm.SUMMARY_START
    + "\nold\n"
    + cm.SUMMARY_END
    + "\nmid\n"
    + cm.COST_START
    + "\nold cost\n"
    + cm.COST_END
    + "\noutro\n"
)


def _copy(tmp_path: Path, keep: int | None = None) -> Path:
    evals = tmp_path / "docs/evals"
    evals.mkdir(parents=True)
    data = json.loads(COMPARISON.read_text())
    for p in data["reports"]:
        shutil.copy(COMPARISON.parent / Path(p).name, evals / Path(p).name)
    if keep is not None:
        data["reports"] = data["reports"][:keep]
    copied = evals / COMPARISON.name
    copied.write_text(json.dumps(data))
    return copied


def test_cli_writes_the_block(tmp_path):
    copied = _copy(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(_MARKERS)
    assert summary.main([str(copied), "--readme", str(readme)]) == 0
    text = readme.read_text()
    assert summary.render(copied, tmp_path) in text
    assert summary.render_cost(copied, tmp_path) in text
    assert "old cost" not in text
    assert "intro" in text and "outro" in text
    assert "old" not in text
    assert "](docs/evals/2026-11-12-held-out-v3-ollama-llama3.2.md)" in text


def test_cli_refuses_an_incomplete_comparison(tmp_path):
    copied = _copy(tmp_path, keep=6)
    readme = tmp_path / "README.md"
    original = _MARKERS
    readme.write_text(original)
    assert summary.main([str(copied), "--readme", str(readme)]) == 2
    assert readme.read_text() == original


def _cost_inputs():
    reports = [dict(r) for r in summary.load_reports(COMPARISON)]
    run_counts = [sum(t["runs"] for t in raw["tickets"]) for raw in summary.load_raw(COMPARISON)]
    links = [f"{cm.name(r)}.md" for r in reports]
    return reports, run_counts, links


def _haiku(reports):
    return next(r for r in reports if cm.name(r) == "haiku")


def test_a_claude_report_with_zero_spend_stays_a_cloud_row():
    reports, run_counts, links = _cost_inputs()
    haiku = _haiku(reports)
    haiku["cost"] = {**haiku["cost"], "total_usd": 0.0, "per_ticket_usd": 0.0}
    block = cm.cost_block(reports, run_counts, links, "c.md")
    assert "Local models (5 setups" in block
    row = next(line for line in block.splitlines() if line.startswith("| [haiku]("))
    assert "$0.00000" in row


def test_a_claude_report_with_no_cost_is_refused():
    reports, run_counts, links = _cost_inputs()
    haiku = _haiku(reports)
    del haiku["cost"]
    with pytest.raises(cm.NotComparableError):
        cm.cost_block(reports, run_counts, links, "c.md")


def test_a_local_report_whose_cost_note_lacks_the_wording_is_refused():
    reports, run_counts, links = _cost_inputs()
    local = next(r for r in reports if r["provider"] != "anthropic")
    local["cost"] = {**local.get("cost", {}), "note": "free"}
    with pytest.raises(cm.NotComparableError):
        cm.cost_block(reports, run_counts, links, "c.md")


def test_the_readme_cost_table_quotes_the_reports_cost_note():
    for raw in summary.load_raw(COMPARISON):
        assert cm.LOCAL_COST_NOTE in raw["cost"]["note"]
    readme = (ROOT / "README.md").read_text()
    assert cm.LOCAL_COST_NOTE in readme_blocks.block_between(readme, cm.COST_START, cm.COST_END)


def test_cli_refuses_a_readme_without_cost_markers(tmp_path):
    copied = _copy(tmp_path)
    readme = tmp_path / "README.md"
    original = "intro\n" + cm.SUMMARY_START + "\nold\n" + cm.SUMMARY_END + "\noutro\n"
    readme.write_text(original)
    assert summary.main([str(copied), "--readme", str(readme)]) == 2
    assert readme.read_text() == original
