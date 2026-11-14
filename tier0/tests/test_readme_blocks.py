"""The shared marker helpers for the generated README blocks."""

import pytest

from tier0.evals import readme_blocks

START, END = "<!-- x:start -->", "<!-- x:end -->"


def test_replace_between_replaces_only_its_own_block():
    text = f"top\n{START}\nold\n{END}\nbottom\n<!-- y:start -->\nkeep\n<!-- y:end -->\n"
    new = f"{START}\nnew\n{END}"
    out = readme_blocks.replace_between(text, new, START, END, "x")
    assert out == f"top\n{new}\nbottom\n<!-- y:start -->\nkeep\n<!-- y:end -->\n"


@pytest.mark.parametrize(
    "text",
    ["no markers", f"only {START}", f"only {END}", f"{END} backwards {START}"],
)
def test_replace_between_refuses_missing_or_misordered_markers(text):
    with pytest.raises(ValueError, match="README has no x markers"):
        readme_blocks.replace_between(text, "block", START, END, "x")


def test_block_between_returns_the_block_with_both_markers():
    text = f"top\n{START}\nbody\n{END}\nbottom"
    assert readme_blocks.block_between(text, START, END) == f"{START}\nbody\n{END}"
