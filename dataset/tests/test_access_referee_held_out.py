"""The sealed blind referee on held_out_a1 (AAC-011). Count-only and yes/no checks.

The builder never reads the referee or the world it runs on. No assertion here has referee or
world content as an operand, so a failure names the check and nothing else. Run with --tb=no.
"""

import gzip
import re
import tempfile
from functools import cache
from pathlib import Path

from meridian_dataset.access import AccessWorld, generate_access, load_recipe
from meridian_dataset.access.referee import (
    HELD_OUT_A1_REFEREE,
    HELD_OUT_A1_REFEREE_SHA256,
    Comparison,
    compare,
    held_out_comparison,
    load_sealed_referee,
    raw_world,
    read_table_block,
    run_referee,
    source_safety,
    split_disagreements,
    split_markdown,
    table_markdown,
    unreadable_exports,
)

LOG = Path(__file__).resolve().parents[2] / "docs/evals/2026-11-15-access-held-out-a1-referee.md"


@cache
def _comparison() -> Comparison:
    return held_out_comparison(None)


def test_the_referee_is_stored_sealed_only() -> None:
    assert HELD_OUT_A1_REFEREE.exists()
    assert not HELD_OUT_A1_REFEREE.with_suffix("").exists()  # no plain .py copy
    gzip.decompress(HELD_OUT_A1_REFEREE.read_bytes())  # a valid gzip, never printed


def test_the_referee_digest_is_pinned() -> None:
    assert len(HELD_OUT_A1_REFEREE_SHA256) == 64
    load_sealed_referee()  # raises "referee digest mismatch" if it changed


def test_the_referee_uses_only_allowed_imports() -> None:
    safety = source_safety(load_sealed_referee())
    assert safety["disallowed_imports"] == 0
    assert safety["forbidden_names"] == 0
    assert safety["suspicious_strings"] == 0


def test_the_logged_review_name_count_matches() -> None:
    review = source_safety(load_sealed_referee())["review_names"]
    logged = f"uses {review} names from the review list" in LOG.read_text(encoding="utf-8")
    assert logged


def test_the_logged_table_matches_the_live_comparison() -> None:
    same = read_table_block(LOG) == table_markdown(_comparison().counts)
    assert same, "regenerate the table in the referee log"


def test_the_referee_has_no_typed_in_identifiers() -> None:
    source = load_sealed_referee()
    employee_ids = sum(1 for _ in re.finditer(r"E\d{5}", source))
    emails = sum(1 for _ in re.finditer(r"[a-z0-9._-]+@meridian\.example", source))
    okta_ids = sum(1 for _ in re.finditer(r"00u[A-Za-z0-9]{10,}", source))
    assert employee_ids == 0
    assert emails == 0
    assert okta_ids == 0


@cache
def _tuning_run() -> tuple[AccessWorld, Comparison]:
    recipe = load_recipe("tuning")
    assert recipe.seed is not None
    world = generate_access(recipe.seed, recipe)
    with tempfile.TemporaryDirectory() as tmp:
        found = run_referee(load_sealed_referee(), raw_world(world, Path(tmp) / "world"))
    return world, compare(world.answer_key.findings, found)


def test_the_logged_tuning_sanity_numbers_match() -> None:
    """On the practice world it was never fitted to, the referee agrees far less, as logged."""
    _, result = _tuning_run()
    agree = sum(row["agree"] for row in result.counts.values())
    text = LOG.read_text(encoding="utf-8")
    assert f"agrees with the generator on {agree} findings" in text
    assert f"It disagrees on {result.total_disagreements}," in text


def test_the_logged_practice_world_split_matches() -> None:
    world, comparison = _tuning_run()
    counts, _ = split_disagreements(world, comparison)
    text = LOG.read_text(encoding="utf-8")
    unreadable = unreadable_exports(world, comparison)
    assert f"{len(unreadable)} exports are unreadable." in text
    only_c = {e.layout for e in world.exports if e.app_id in unreadable} == {"C"}
    assert only_c, "unreadable exports are not all layout C"
    exports_only = unreadable <= {e.app_id for e in world.exports}
    assert exports_only
    start, end = "<!-- referee-split:start -->", "<!-- referee-split:end -->"
    block = text[text.index(start) + len(start) : text.index(end)].strip()
    assert block == split_markdown(counts)
