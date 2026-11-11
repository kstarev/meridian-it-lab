"""Held-out sets stay sealed until their own final comparison (ADR-015, ADR-029).

Running any model on a sealed set, the stub included, before the fixes it is meant to
judge are done would let its results leak into those fixes. Every agent run goes through
offline_runtime(), which calls require_unsealed() first, so the seal is enforced in code.

Each set is unsealed by name (--final-comparison=held_out_v3), so a run that unseals one
set can never unseal another by accident.
"""

from meridian_dataset import World

# Held-out v2 was spent in milestone 2's final comparison (ADR-027) and is now a plain dev
# set like v1 (ADR-029), so only v3 is sealed.
SEALED_POOLS = frozenset({"held_out_v3"})


class SealedSetError(RuntimeError):
    """Raised when something tries to run the agent on a sealed set."""


def require_unsealed(world: World, *, final_comparison: str | None = None) -> None:
    """Refuse a sealed world unless `final_comparison` names exactly its set."""
    if world.message_pool in SEALED_POOLS and final_comparison != world.message_pool:
        raise SealedSetError(
            f"{world.message_pool} (seed {world.seed}) is sealed until its final comparison "
            f"(ADR-015, ADR-029); pass --final-comparison={world.message_pool} to unseal it"
        )
