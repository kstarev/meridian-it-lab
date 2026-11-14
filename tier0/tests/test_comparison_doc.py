"""The comparison blocks in docs/how-it-works.md: rebuilt from the published comparisons."""

import json
import shutil
from pathlib import Path

from tier0.evals import compare, compare_many, comparison_doc, readme_blocks

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/how-it-works.md"
EVALS = ROOT / "docs/evals"
DOCS = ROOT / "docs"
COMMAND = "python -m tier0.evals.comparison_doc --doc docs/how-it-works.md"


def test_the_m2_block_matches_the_published_comparison():
    text = DOC.read_text()
    assert text.count(compare.README_START) == text.count(compare.README_END) == 1
    assert readme_blocks.block_between(
        text, compare.README_START, compare.README_END
    ) == comparison_doc.render_m2(EVALS, DOCS), f"milestone 2's block drifted: run {COMMAND}"


def test_the_m3_block_matches_the_published_comparison():
    text = DOC.read_text()
    assert text.count(compare_many.README_START) == text.count(compare_many.README_END) == 1
    assert readme_blocks.block_between(
        text, compare_many.README_START, compare_many.README_END
    ) == comparison_doc.render_m3(EVALS, DOCS), f"milestone 3's block drifted: run {COMMAND}"


_MARKERS = (
    f"top\n{compare.README_START}\nold m2\n{compare.README_END}\nmid\n"
    f"{compare_many.README_START}\nold m3\n{compare_many.README_END}\nend\n"
)


def _evals_copy(tmp_path: Path) -> Path:
    evals = tmp_path / "docs/evals"
    evals.mkdir(parents=True)
    names = [comparison_doc.M2, comparison_doc.M3]
    for comparison in (comparison_doc.M2, comparison_doc.M3):
        names.append(Path(comparison).with_suffix(".md").name)
        names += [Path(p).name for p in json.loads((EVALS / comparison).read_text())["reports"]]
    for name in names:
        if (EVALS / name).exists():
            shutil.copy(EVALS / name, evals / name)
    return evals


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def test_the_cli_writes_both_blocks_and_never_touches_the_evals_dir(tmp_path):
    evals = _evals_copy(tmp_path)
    doc = tmp_path / "docs/how-it-works.md"
    doc.write_text(_MARKERS)
    before = _snapshot(evals)
    assert comparison_doc.main(["--doc", str(doc)]) == 0
    assert _snapshot(evals) == before
    text = doc.read_text()
    assert comparison_doc.render_m2(evals, doc.parent) in text
    assert comparison_doc.render_m3(evals, doc.parent) in text
    assert "old m2" not in text and "old m3" not in text
    assert text.startswith("top\n") and text.endswith("\nend\n")


def test_the_cli_refuses_a_doc_without_the_markers(tmp_path):
    evals = _evals_copy(tmp_path)
    doc = tmp_path / "docs/how-it-works.md"
    original = f"top\n{compare.README_START}\nold\n{compare.README_END}\nend\n"
    doc.write_text(original)
    before = _snapshot(evals)
    assert comparison_doc.main(["--doc", str(doc)]) == 2
    assert doc.read_text() == original
    assert _snapshot(evals) == before


def test_the_cli_refuses_a_missing_comparison(tmp_path):
    doc = tmp_path / "docs/how-it-works.md"
    doc.parent.mkdir()
    doc.write_text(_MARKERS)
    assert comparison_doc.main(["--doc", str(doc)]) == 2
    assert doc.read_text() == _MARKERS
