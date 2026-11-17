"""Held-out sets stay sealed until their own final comparison (ADR-015, ADR-029).

Running any model on a sealed set, the stub included, before the fixes it is meant to
judge are done would let its results leak into those fixes. Every agent run goes through
offline_runtime(), which calls require_unsealed() first, so the seal is enforced in code.

Each set is unsealed by name (--final-comparison=<its pool name>), so a run that unseals one
set can never unseal another by accident.
"""

from meridian_dataset import World

# Held-out v2 was spent in milestone 2's final comparison (ADR-027) and v3 in milestone 3's
# (2026-11-12, ADR-030); both are plain dev sets like v1. Held-out v4 and v4b (milestone 3b,
# ADR-033, ADR-038) were written blind, sealed before the TKT-004 fix existed, and spent in
# their one paired run on 2026-11-15; they are dev sets too (ADR-039). Held-out v5 (milestone
# 3d part 1, docs/plans/milestone-3d-injection-relayed.md) was written blind and sealed on
# 2026-11-15, before any 3d prompt was scored. Held-out v6 (milestone 3d part 2,
# docs/plans/milestone-3d-part-2.md) was written blind and sealed on 2026-11-17, before any
# part 2 prompt was scored and before any real model ran on part 2's tuning world.
SEALED_POOLS: frozenset[str] = frozenset({"held_out_v5", "held_out_v6"})


class SealedSetError(RuntimeError):
    """Raised when something tries to run the agent on a sealed set."""


def require_unsealed(world: World, *, final_comparison: str | None = None) -> None:
    """Refuse a sealed world unless `final_comparison` names exactly its set."""
    if world.message_pool in SEALED_POOLS and final_comparison != world.message_pool:
        raise SealedSetError(
            f"{world.message_pool} (seed {world.seed}) is sealed until its final comparison "
            f"(ADR-015, ADR-029); pass --final-comparison={world.message_pool} to unseal it"
        )
