"""Held-out v2 stays sealed until the final comparison (ADR-015).

Running any model on it, the stub included, before milestone 2's fixes are done
would let its results leak into those fixes. Every agent run goes through
offline_runtime(), which calls require_unsealed() first, so the seal is enforced in
code rather than left to discipline.
"""

from meridian_dataset import World

SEALED_POOLS = frozenset({"held_out_v2"})


class SealedSetError(RuntimeError):
    """Raised when something tries to run the agent on a sealed set."""


def require_unsealed(world: World, *, final_comparison: bool = False) -> None:
    if world.message_pool in SEALED_POOLS and not final_comparison:
        raise SealedSetError(
            f"{world.message_pool} (seed {world.seed}) is sealed until the final comparison "
            "(ADR-015); pass --final-comparison to unseal it"
        )
