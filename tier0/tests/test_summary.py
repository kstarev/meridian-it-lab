"""The README's held-out v3 summary: generated from the published comparison, never drifting."""

import json
import shutil
from pathlib import Path

import pytest

from tier0.evals import compare_many as cm
from tier0.evals import summary

ROOT = Path(__file__).resolve().parents[2]
COMPARISON = ROOT / "docs/evals/2026-11-12-held-out-v3-comparison.json"


def _block(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end) + len(end)]


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
    assert _block(readme, cm.SUMMARY_START, cm.SUMMARY_END) == summary.render(COMPARISON, ROOT), (
        "README's v3 summary drifted from the published comparison: run python -m "
        "tier0.evals.summary docs/evals/2026-11-12-held-out-v3-comparison.json "
        "--readme README.md"
    )


def test_the_summary_agrees_with_the_generated_comparison_block():
    readme = (ROOT / "README.md").read_text()
    mine = _rows(_block(readme, cm.SUMMARY_START, cm.SUMMARY_END))
    m3 = _rows(_block(readme, cm.README_START, cm.README_END))
    assert len(mine) == 7
    for setup, cells in mine.items():
        assert setup in m3
        # pass^3, everyday, policy violations, data exposed, cost, latency
        for cell in (cells[2], cells[3], cells[4], cells[5], cells[8], cells[9]):
            assert cell in m3[setup], (setup, cell)


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
    readme.write_text("intro\n" + cm.SUMMARY_START + "\nold\n" + cm.SUMMARY_END + "\noutro\n")
    assert summary.main([str(copied), "--readme", str(readme)]) == 0
    text = readme.read_text()
    assert summary.render(copied, tmp_path) in text
    assert "intro" in text and "outro" in text
    assert "old" not in text
    assert "](docs/evals/2026-11-12-held-out-v3-ollama-llama3.2.md)" in text


def test_cli_refuses_an_incomplete_comparison(tmp_path):
    copied = _copy(tmp_path, keep=6)
    readme = tmp_path / "README.md"
    original = "intro\n" + cm.SUMMARY_START + "\nold\n" + cm.SUMMARY_END + "\noutro\n"
    readme.write_text(original)
    assert summary.main([str(copied), "--readme", str(readme)]) == 2
    assert readme.read_text() == original
