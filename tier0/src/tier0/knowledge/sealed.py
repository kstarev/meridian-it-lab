"""The sealed knowledge sets stay sealed until their final test (ADR-035, like ADR-029).

The how-to test's questions were written blind by a helper that never saw the code, the prompts
or the tuning questions. Nothing may run the picker on it before the prompts are frozen and the
owner says go, so results on it can't leak into the tuning. The guide screen's set, held_out_g2,
is checked by code alone, once, with the owner's go. A set is unsealed only by its own name.
"""

# held_out_g2 is the guide screen fix's set of guides (docs/plans/milestone-4-guide-screen-fix.md).
# It is registered before it is written, so nothing can run on it by accident once it is.
SEALED_SETS: frozenset[str] = frozenset({"held_out_k1", "held_out_g2"})


class SealedSetError(RuntimeError):
    """Raised when something tries to run the picker on a sealed question set."""


def require_unsealed(set_name: str, *, final_test: str | None = None) -> None:
    """Refuse a sealed question set unless `final_test` names exactly that set."""
    if set_name in SEALED_SETS and final_test != set_name:
        raise SealedSetError(
            f"{set_name} is sealed until its final test (ADR-035); "
            f"pass --final-test={set_name} to unseal it, and only with the owner's go"
        )
