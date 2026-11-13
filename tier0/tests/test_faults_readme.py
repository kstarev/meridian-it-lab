"""The README's fault-injection table: generated from the published reports, never drifting."""

import json
from pathlib import Path

import pytest

from tier0.evals import compare_many as cm
from tier0.evals import faults_readme as fr

ROOT = Path(__file__).resolve().parents[2]
PATHS = [ROOT / "docs/evals" / f"{s}.json" for s in fr.PUBLISHED]


def _block(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end) + len(end)]


def test_the_readme_fault_table_matches_the_published_reports():
    readme = (ROOT / "README.md").read_text()
    assert readme.count(fr.FAULTS_START) == 1
    assert readme.count(fr.FAULTS_END) == 1
    assert _block(readme, fr.FAULTS_START, fr.FAULTS_END) == fr.render(PATHS, ROOT), (
        "README's fault-injection table drifted from the published reports: run python -m "
        "tier0.evals.faults_readme "
        + " ".join(str(p.relative_to(ROOT)) for p in PATHS)
        + " --readme README.md"
    )


def test_the_cells_match_the_reports():
    data = json.loads(PATHS[1].read_text())
    g = data["models"]["llama3.2"]["summary"]["colleague_unnamed"]
    expected = f"**unnamed {g['answered']} of {g['flipped_runs']}**"
    row = next(line for line in fr.render(PATHS, ROOT).splitlines() if line.startswith("| [2]("))
    assert expected in row
    assert expected == "**unnamed 10 of 21**"


def test_an_unlabelled_report_is_refused(tmp_path):
    odd = tmp_path / "odd.json"
    odd.write_text(PATHS[0].read_text())
    readme = tmp_path / "README.md"
    original = f"a\n{fr.FAULTS_START}\nold\n{fr.FAULTS_END}\nb\n"
    readme.write_text(original)
    assert fr.main([str(odd), "--readme", str(readme)]) == 2
    assert readme.read_text() == original


def test_different_model_sets_are_refused():
    first = json.loads(PATHS[0].read_text())
    second = json.loads(PATHS[0].read_text())
    del second["models"]["llama3.1:8b"]
    with pytest.raises(ValueError):
        fr.faults_block([(fr.PUBLISHED[0], first), (fr.PUBLISHED[0], second)], ["a.md", "b.md"])


def test_replace_faults_block_leaves_the_other_blocks_alone():
    m3 = cm.README_START + "\nm3\n" + cm.README_END
    cost = cm.COST_START + "\ncost\n" + cm.COST_END
    old = fr.FAULTS_START + "\nold\n" + fr.FAULTS_END
    new = fr.FAULTS_START + "\nnew\n" + fr.FAULTS_END
    out = fr.replace_faults_block(f"top\n{m3}\n{old}\n{cost}\nend\n", new)
    assert m3 in out and cost in out and new in out
    assert "old" not in out
    assert out.startswith("top\n") and out.endswith("\nend\n")


def test_replace_faults_block_needs_its_markers():
    with pytest.raises(ValueError):
        fr.replace_faults_block("no markers here", "block")
