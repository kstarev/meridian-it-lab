"""The pool review command, run on tuning wording only (held-out pools are for people)."""

import pytest

from meridian_dataset.names import TUNING_POOL
from meridian_dataset.review import main, render


def test_review_prints_every_tuning_template(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tuning"]) == 0
    out = capsys.readouterr().out
    for template in (*TUNING_POOL.normal, *TUNING_POOL.colleague, *TUNING_POOL.unauthenticated):
        assert template in out
    assert render("tuning").startswith("Pool tuning")


def test_review_refuses_an_unknown_pool() -> None:
    with pytest.raises(SystemExit):
        main(["nope"])
