"""Count-only overlap checks between message sets (ADR-013, ADR-045).

The two sentence splitters are the ones the existing count-only tests use:
`tests/test_message_pools.py` (every sentence) and `tests/test_tuning_several.py` (sentences
of 3 words or more). Every function here returns a number, never wording, so a check against
a sealed set can't print it.
"""

import re
from collections.abc import Callable, Iterable

from meridian_dataset.names import MessagePool

type Splitter = Callable[[str], set[str]]

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def sentences(text: str) -> set[str]:
    """Every sentence, lower case, placeholders and punctuation removed."""
    text = _PLACEHOLDER.sub(" ", text.lower())
    parts = re.split(r"(?<=[.?!])\s+", text)
    return {" ".join(re.findall(r"[a-z0-9']+", p)) for p in parts} - {""}


def long_sentences(text: str) -> set[str]:
    """Sentences of 3 words or more, lower case, placeholders removed."""
    text = _PLACEHOLDER.sub(" ", text.lower())
    return {" ".join(s.split()) for s in re.split(r"[.!?\n]+", text) if len(s.split()) >= 3}


SPLITTERS: tuple[Splitter, ...] = (sentences, long_sentences)


def pool_texts(pool: MessagePool) -> list[str]:
    """Every template in a pool, part 2's groups included, with each chain's messages."""
    groups = ("normal", "self_described", "duplicate", "colleague", "unauthenticated")
    texts = [t for g in groups for t in getattr(pool, g)]
    texts += [*getattr(pool, "reset", ()), *getattr(pool, "several", ())]
    texts += [m for chain in getattr(pool, "repeat", ()) for m in chain]
    return texts


def shared_count(texts: Iterable[str], others: Iterable[str], split: Splitter) -> int:
    """How many distinct sentences of `texts` also appear in `others`."""
    mine = set().union(*(split(t) for t in texts))
    theirs = set().union(*(split(t) for t in others))
    return len(mine & theirs)


def repeated_count(texts: Iterable[str], split: Splitter) -> int:
    """How many sentences repeat inside one set of texts."""
    found = [s for t in texts for s in split(t)]
    return len(found) - len(set(found))
