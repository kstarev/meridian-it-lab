"""Message pools never share wording (ADR-013, ADR-014, ADR-015).

Every pair of pools is checked. Assertions compare counts only, never sets of
strings, so a failing test can't print held-out wording to whoever reads the output.
"""

import itertools
import re

import pytest

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, HOLDOUT_V2_SEED, generate
from meridian_dataset.generator import split_for_seed
from meridian_dataset.names import MESSAGE_POOLS, TUNING_POOL, MessagePool

SCENARIOS = ("normal", "duplicate", "colleague", "unauthenticated")
# Every field that holds wording, including self-descriptions (ADR-029).
TEMPLATE_FIELDS = (*SCENARIOS, "self_described")
PAIRS = list(itertools.combinations(sorted(MESSAGE_POOLS), 2))
HELD_OUT_NAMES = sorted(name for name in MESSAGE_POOLS if name != TUNING_POOL.name)


def _templates(pool: MessagePool) -> list[str]:
    return [t for field in TEMPLATE_FIELDS for t in getattr(pool, field)]


def _sentences(text: str) -> set[str]:
    """Lower-case sentences with placeholders and punctuation removed."""
    text = re.sub(r"\{[a-z_]+\}", " ", text.lower())
    parts = re.split(r"(?<=[.?!])\s+", text)
    return {" ".join(re.findall(r"[a-z0-9']+", p)) for p in parts} - {""}


def _pool_sentences(pool: MessagePool) -> set[str]:
    return set().union(*(_sentences(t) for t in _templates(pool)))


def _generated_texts(name: str) -> set[str]:
    seeds = (DEFAULT_SEED, *range(30))
    return {t.text for seed in seeds for t in generate(seed, split=name).tickets}


@pytest.mark.parametrize("name", sorted(MESSAGE_POOLS))
def test_every_scenario_has_wording(name: str) -> None:
    pool = MESSAGE_POOLS[name]
    for scenario in SCENARIOS:
        assert len(getattr(pool, scenario)) > 0, f"{name} has no {scenario} wording"


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_no_template_is_shared(a: str, b: str) -> None:
    shared = len(set(_templates(MESSAGE_POOLS[a])) & set(_templates(MESSAGE_POOLS[b])))
    assert shared == 0, f"{shared} template(s) shared between {a} and {b}"


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_no_sentence_is_shared(a: str, b: str) -> None:
    sentences_a = _pool_sentences(MESSAGE_POOLS[a])
    sentences_b = _pool_sentences(MESSAGE_POOLS[b])
    assert len(sentences_a) > 0 and len(sentences_b) > 0
    shared = len(sentences_a & sentences_b)
    assert shared == 0, f"{shared} sentence(s) shared between {a} and {b}"


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_no_generated_ticket_text_is_shared(a: str, b: str) -> None:
    shared = len(_generated_texts(a) & _generated_texts(b))
    assert shared == 0, f"{shared} generated ticket text(s) shared between {a} and {b}"


def test_pool_follows_the_seed() -> None:
    assert split_for_seed(DEFAULT_SEED) == "tuning"
    assert split_for_seed(HOLDOUT_SEED) == "held_out_v1"
    assert generate(DEFAULT_SEED).message_pool == "tuning"
    assert generate(HOLDOUT_SEED).message_pool == "held_out_v1"
    assert split_for_seed(HOLDOUT_V2_SEED) == "held_out_v2"
    assert generate(HOLDOUT_V2_SEED).message_pool == "held_out_v2"


@pytest.mark.parametrize("name", sorted(MESSAGE_POOLS))
def test_templates_keep_the_scenario_shape(name: str) -> None:
    pool = MESSAGE_POOLS[name]

    def fields(template: str) -> list[str]:
        return re.findall(r"\{([a-z_]+)\}", template)

    bad = sum(fields(t) != ["other_email"] for t in pool.duplicate)
    # A colleague request names the target once and may add a first.last guess (ADR-018),
    # or names nobody at all (ADR-023).
    bad += sum(
        sorted(fields(t)) not in ([], ["target"], ["target", "target_guess"])
        for t in pool.colleague
    )
    # An outsider may claim to be Alex Kim, a named employee ({claimed_name}), or a vendor.
    bad += sum(fields(t) not in ([], ["claimed_name"]) for t in pool.unauthenticated)
    bad += sum(fields(t) != [] for t in pool.normal)
    bad += sum(fields(t) != [] for t in pool.self_described)
    assert bad == 0, f"{bad} template(s) in {name} break their scenario's shape"


def test_unknown_split_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown split"):
        generate(DEFAULT_SEED, split="test")


# Tuning is left out: it predates this check and must not change (it repeats one sentence).
@pytest.mark.parametrize("name", HELD_OUT_NAMES)
def test_no_sentence_repeats_within_a_pool(name: str) -> None:
    pool = MESSAGE_POOLS[name]
    all_sentences = [s for t in _templates(pool) for s in _sentences(t)]
    repeated = len(all_sentences) - len(set(all_sentences))
    assert repeated == 0, f"{repeated} sentence(s) repeat inside {name}"


def test_tuning_has_unnamed_colleague_requests() -> None:
    """Requests that name nobody the mention check could look up (ADR-023)."""
    unnamed = [t for t in MESSAGE_POOLS["tuning"].colleague if "{" not in t]
    assert len(unnamed) == 4
