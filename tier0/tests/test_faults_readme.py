"""The fault-injection table in docs/how-it-works.md: generated, never drifting."""

import json
from pathlib import Path

import pytest

from tier0.evals import compare_many as cm
from tier0.evals import faults_readme as fr
from tier0.evals import readme_blocks

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/how-it-works.md"
PATHS = [ROOT / "docs/evals" / f"{s}.json" for s in fr.PUBLISHED]


def test_the_readme_fault_table_matches_the_published_reports():
    readme = DOC.read_text()
    assert readme.count(fr.FAULTS_START) == 1
    assert readme.count(fr.FAULTS_END) == 1
    assert readme_blocks.block_between(readme, fr.FAULTS_START, fr.FAULTS_END) == fr.render(
        ROOT / "docs/evals", ROOT / "docs"
    ), (
        "The doc's fault-injection table drifted from the published reports: run "
        "python -m tier0.evals.faults_readme --doc docs/how-it-works.md"
    )


def test_the_cells_match_the_reports():
    data = json.loads(PATHS[1].read_text())
    g = data["models"]["llama3.2"]["summary"]["colleague_unnamed"]
    expected = f"**unnamed {g['answered']} of {g['flipped_runs']}**"
    lines = fr.render(ROOT / "docs/evals", ROOT / "docs").splitlines()
    row = next(line for line in lines if line.startswith("| [2]("))
    assert expected in row
    assert expected == "**unnamed 10 of 21**"


_ORIGINAL = f"a\n{fr.FAULTS_START}\nold\n{fr.FAULTS_END}\nb\n"


def test_an_unlabelled_report_is_refused():
    data = json.loads(PATHS[0].read_text())
    with pytest.raises(ValueError, match="no label for odd"):
        fr.faults_block([("odd", data)], ["odd.md"])


def test_a_missing_report_is_refused_and_the_doc_is_left_alone(tmp_path):
    doc = tmp_path / "how-it-works.md"
    doc.write_text(_ORIGINAL)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert fr.main(["--doc", str(doc), "--reports-dir", str(empty)]) == 2
    assert doc.read_text() == _ORIGINAL


def test_a_doc_without_fault_markers_is_refused(tmp_path):
    doc = tmp_path / "how-it-works.md"
    doc.write_text("no markers\n")
    assert fr.main(["--doc", str(doc), "--reports-dir", str(ROOT / "docs/evals")]) == 2
    assert doc.read_text() == "no markers\n"


def test_the_same_models_in_a_different_order_give_the_same_row():
    first = json.loads(PATHS[0].read_text())
    second = json.loads(PATHS[1].read_text())
    second_reordered = {**second, "models": dict(reversed(second["models"].items()))}
    stems = fr.PUBLISHED[:2]
    links = ["a.md", "b.md"]
    plain = fr.faults_block([(stems[0], first), (stems[1], second)], links)
    reordered = fr.faults_block([(stems[0], first), (stems[1], second_reordered)], links)
    assert plain == reordered


def test_reports_that_differ_in_fault_seed_are_refused():
    first = json.loads(PATHS[0].read_text())
    second = json.loads(PATHS[1].read_text())
    second["fault_seed"] = first["fault_seed"] + 1
    with pytest.raises(ValueError, match="fault_seed"):
        fr.faults_block([(fr.PUBLISHED[0], first), (fr.PUBLISHED[1], second)], ["a.md", "b.md"])


def test_the_single_group_cell_is_bold_only_when_answered():
    assert fr._cell({"colleague": {"answered": 3, "flipped_runs": 36}}) == "**3 of 36**"
    assert fr._cell({"colleague": {"answered": 0, "flipped_runs": 36}}) == "0 of 36"


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
