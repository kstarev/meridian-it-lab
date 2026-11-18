"""Property-based tests for the sealed-set guards (hypothesis).

The rule for all of them: a sealed set is refused unless the unseal name is exactly its own,
and unsealing one set never unseals another. Every name here is made up, and each guard is
given a stand-in list of sealed names, so the tests do not depend on which real set is sealed
today. Nothing here reads, loads or runs a sealed set. The runs are fixed (derandomize) and
keep no example database.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tier0 import sealed as world_guard
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.knowledge import sealed as knowledge_guard
from tier0.models.spend import ITEM_ENV

SETTINGS = settings(derandomize=True, max_examples=300, database=None, deadline=None)

NAMES = ("made_up_a", "made_up_b", "made_up_c", "tuning_made_up")
# Unseal names that are close to a set's name but are not it.
NEAR = ("made_up_a ", " made_up_a", "MADE_UP_A", "made_up", "made_up_a1", "made_up_a\n", "", "*")
SEEDS = {name: 7000 + i for i, name in enumerate(NAMES)}

names = st.sampled_from(NAMES)
flags = st.none() | st.sampled_from(NAMES + NEAR) | st.text(max_size=10)
sealed_lists = st.frozensets(names)
setups = st.sampled_from(sorted(ALL_SETUPS))


@contextmanager
def month_seals(sealed: frozenset[str]) -> Iterator[None]:
    """The month runner with `sealed` as its sealed sets, each at its own made-up seed."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mo, "SEALED_SETS", sealed)
        patch.setattr(mo, "DEFAULT_SEEDS", SEEDS)
        patch.setattr(mo, "SPENT_SETS", {})
        yield


def _world(pool: str) -> Any:
    return SimpleNamespace(message_pool=pool, seed=11)


# --- tier0.sealed and tier0.knowledge.sealed ----------------------------------------------------


@SETTINGS
@given(sealed=sealed_lists, pool=names | st.sampled_from(NEAR), flag=flags)
def test_a_sealed_world_runs_only_when_the_flag_is_exactly_its_own_name(
    sealed: frozenset[str], pool: str, flag: str | None
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(world_guard, "SEALED_POOLS", sealed)
        if pool in sealed and flag != pool:
            with pytest.raises(world_guard.SealedSetError) as refused:
                world_guard.require_unsealed(_world(pool), final_comparison=flag)
            assert f"--final-comparison={pool}" in str(refused.value)
        else:
            assert world_guard.require_unsealed(_world(pool), final_comparison=flag) is None
        if pool in sealed:
            with pytest.raises(world_guard.SealedSetError):
                world_guard.require_unsealed(_world(pool))  # no flag at all


@SETTINGS
@given(sealed=sealed_lists, set_name=names | st.sampled_from(NEAR), flag=flags)
def test_a_sealed_question_set_runs_only_when_the_flag_is_exactly_its_own_name(
    sealed: frozenset[str], set_name: str, flag: str | None
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(knowledge_guard, "SEALED_SETS", sealed)
        if set_name in sealed and flag != set_name:
            with pytest.raises(knowledge_guard.SealedSetError) as refused:
                knowledge_guard.require_unsealed(set_name, final_test=flag)
            assert f"--final-test={set_name}" in str(refused.value)
        else:
            assert knowledge_guard.require_unsealed(set_name, final_test=flag) is None
        if set_name in sealed:
            with pytest.raises(knowledge_guard.SealedSetError):
                knowledge_guard.require_unsealed(set_name)  # no flag at all


@SETTINGS
@given(mine=names, other=names, seed=st.sampled_from(sorted(SEEDS.values())))
def test_unsealing_one_set_never_unseals_another(mine: str, other: str, seed: int) -> None:
    if mine == other:
        return
    both = frozenset({mine, other})
    with pytest.MonkeyPatch.context() as patch, month_seals(both):
        patch.setattr(world_guard, "SEALED_POOLS", both)
        patch.setattr(knowledge_guard, "SEALED_SETS", both)
        # `mine` opens under its own name ...
        assert world_guard.require_unsealed(_world(mine), final_comparison=mine) is None
        assert knowledge_guard.require_unsealed(mine, final_test=mine) is None
        assert mo.check_unseal(mine, mine, SEEDS[mine]) is None
        # ... and that name opens nothing else, at any seed.
        with pytest.raises(world_guard.SealedSetError):
            world_guard.require_unsealed(_world(other), final_comparison=mine)
        with pytest.raises(knowledge_guard.SealedSetError):
            knowledge_guard.require_unsealed(other, final_test=mine)
        assert mo.check_unseal(other, mine, seed) is not None


# --- the month runner's guards ------------------------------------------------------------------


@SETTINGS
@given(
    sealed=sealed_lists,
    set_name=names,
    flag=flags,
    seed=st.sampled_from(sorted(SEEDS.values())) | st.integers(-3, 10**6),
)
def test_the_month_runner_opens_a_sealed_set_only_by_its_own_name_at_its_own_seed(
    sealed: frozenset[str], set_name: str, flag: str | None, seed: int
) -> None:
    with month_seals(sealed):
        reason = mo.check_unseal(set_name, flag, seed)
    if flag is not None and flag != set_name:
        allowed = False  # a flag that names another set runs nothing, sealed or not
    elif set_name in sealed:
        allowed = flag == set_name and seed == SEEDS[set_name]
    else:
        allowed = True
    if allowed:
        assert reason is None
    else:
        assert isinstance(reason, str) and set_name in reason


@SETTINGS
@given(
    sealed=sealed_lists,
    set_name=names,
    setup=setups,
    picker=st.sampled_from(("knowledge", "knowledge", "none", "Knowledge", "knowledge ", "")),
    picker_prompt=st.none() | st.none() | st.sampled_from(("KS5", "KS2", "")),
    own_model_picker=st.booleans(),
    k=st.sampled_from((3, 3, 3, 0, 1, 2, 4, 5, -3)),
)
def test_a_sealed_month_runs_only_as_its_frozen_plan(
    sealed: frozenset[str],
    set_name: str,
    setup: str,
    picker: str,
    picker_prompt: str | None,
    own_model_picker: bool,
    k: int,
) -> None:
    with month_seals(sealed):
        reason = mo.check_sealed_plan(
            set_name,
            ALL_SETUPS[setup],
            picker=picker,
            picker_prompt=picker_prompt,
            own_model_picker=own_model_picker,
            k=k,
        )
    frozen = (
        setup in mo.SEALED_ARMS,
        picker == "knowledge" and picker_prompt is None and not own_model_picker,
        k == mo.SEALED_K,
    )
    if set_name not in sealed or all(frozen):
        assert reason is None
    else:
        # The refusal names the first part of the plan that was changed.
        said = ("frozen arms", "--picker knowledge", f"k = {mo.SEALED_K}")[frozen.index(False)]
        assert isinstance(reason, str) and reason.startswith(f"{set_name} runs only")
        assert said in reason


@SETTINGS
@given(sealed=sealed_lists, set_name=names, setup=setups, prompt_cache=st.booleans())
def test_the_prompt_cache_is_for_claude_setups_and_never_for_a_sealed_set(
    sealed: frozenset[str], set_name: str, setup: str, prompt_cache: bool
) -> None:
    with month_seals(sealed):
        reason = mo.check_prompt_cache(set_name, ALL_SETUPS[setup], prompt_cache)
    claude = ALL_SETUPS[setup].provider == "claude"
    if not prompt_cache or (claude and set_name not in sealed):
        assert reason is None
    elif not claude:
        assert isinstance(reason, str) and "Claude setups" in reason and setup in reason
    else:
        assert isinstance(reason, str) and reason.startswith(f"{set_name} was run without")


ITEMS = (mo.BUDGET_ITEM, "tier0-3b", f"{mo.BUDGET_ITEM} ", mo.BUDGET_ITEM.upper(), "", "tier0-3")


@SETTINGS
@given(
    setup=setups,
    item=st.none() | st.sampled_from(ITEMS) | st.text(max_size=8),
    other=st.dictionaries(st.sampled_from(("PATH", "TIER0_OTHER")), st.sampled_from(ITEMS)),
)
def test_a_claude_month_is_paid_by_its_own_budget_item_only(
    setup: str, item: str | None, other: dict[str, str]
) -> None:
    env = dict(other) if item is None else {**other, ITEM_ENV: item}
    reason = mo.check_budget_item(ALL_SETUPS[setup], env)
    if ALL_SETUPS[setup].provider != "claude" or item == mo.BUDGET_ITEM:
        assert reason is None
    else:
        assert isinstance(reason, str) and f"the budget item {mo.BUDGET_ITEM} " in reason
        assert reason.endswith(f"{ITEM_ENV} is {item or 'not set'}")


def test_the_setups_cover_both_providers_and_both_frozen_arms() -> None:
    # So the properties above see a Claude setup, a local one, a frozen arm and another arm.
    assert {s.provider for s in ALL_SETUPS.values()} == {"claude", "ollama"}
    assert set(mo.SEALED_ARMS) < set(ALL_SETUPS)
