"""`unseal`'s checks on a stand-in set: the name, the SHA-256 and the name field (red-team.md).

The sealed set is never opened here. Each test points `SET_PATH` at a small file it makes, or
at a file that does not exist, and gives `SET_NAME` a made-up name. They kill the mutants that
survived the mutation run of 2026-11-18 (docs/evals/2026-11-18-safety-mutation-testing.md):
no test noticed when the SHA-256 check or the name-field check was removed.
"""

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access.redteam import cases as rc
from meridian_access.redteam.cases import SetError

SETTINGS = settings(derandomize=True, max_examples=200, database=None, deadline=None)
STAND_IN = "made_up_redteam"
REAL_NAME = rc.SET_NAME


def _stand_in(
    folder: Path,
    patch: pytest.MonkeyPatch,
    *,
    name_field: str = STAND_IN,
    stored: bytes | None = None,
) -> None:
    """Make `unseal` read a made-up set: a valid, empty one unless told otherwise. The pinned
    SHA-256 is always the valid set's; `stored` puts other bytes in the file."""
    data = json.dumps({"format": rc.FORMAT, "name": name_field, "cases": []}).encode()
    path = folder / f"{STAND_IN}.json.gz"
    path.write_bytes(gzip.compress(data if stored is None else stored))
    patch.setattr(rc, "SET_NAME", STAND_IN)
    patch.setattr(rc, "SET_PATH", path)
    patch.setattr(rc, "SET_SHA256", hashlib.sha256(data).hexdigest())


def test_the_stand_in_opens_by_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stand_in(tmp_path, monkeypatch)
    assert rc.unseal(STAND_IN) == ()
    assert rc._sealed_bytes() == gzip.decompress(rc.SET_PATH.read_bytes())


@pytest.mark.parametrize("change", ["one more byte", "one byte less", "another set"])
def test_a_file_that_is_not_the_sealed_bytes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    good = json.dumps({"format": rc.FORMAT, "name": STAND_IN, "cases": []}).encode()
    stored = {
        "one more byte": good + b" ",
        "one byte less": good[:-1],
        # Valid, with the right name, and still not the bytes that were sealed.
        "another set": json.dumps({"cases": [], "format": rc.FORMAT, "name": STAND_IN}).encode(),
    }[change]
    _stand_in(tmp_path, monkeypatch, stored=stored)
    with pytest.raises(SetError, match="seal of made_up_redteam is broken"):
        rc.unseal(STAND_IN)
    with pytest.raises(SetError, match="SHA-256 does not match"):
        rc._sealed_bytes()


def test_sealed_bytes_that_name_another_set_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SHA-256 matches, so only the name field can stop this.
    _stand_in(tmp_path, monkeypatch, name_field="made_up_other")
    with pytest.raises(SetError, match="name field does not match"):
        rc.unseal(STAND_IN)
    _stand_in(tmp_path, monkeypatch, name_field=REAL_NAME)
    with pytest.raises(SetError, match="name field does not match"):
        rc.unseal(STAND_IN)


NEAR = (
    "",
    "made_up_redteam ",
    " made_up_redteam",
    "MADE_UP_REDTEAM",
    "made_up_redteam1",
    "made_up",
    "made_up_redteam.json.gz",
    REAL_NAME,
    "*",
)


@SETTINGS
@given(name=st.sampled_from(NEAR) | st.text(max_size=20))
def test_any_other_name_is_refused_before_the_file_is_read(name: str) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(rc, "SET_NAME", STAND_IN)
        # Reading this path would raise FileNotFoundError, not SetError.
        patch.setattr(rc, "SET_PATH", Path("no-such-folder") / "no-such-set.json.gz")
        if name != STAND_IN:
            with pytest.raises(SetError, match="opens only by its own name, made_up_redteam"):
                rc.unseal(name)
        else:
            with pytest.raises(FileNotFoundError):
                rc.unseal(name)
