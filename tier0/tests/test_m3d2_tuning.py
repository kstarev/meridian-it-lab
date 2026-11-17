"""The dry-run report on part 2's tuning world, from a stub run (tuning data only)."""

import collections
import json

import anyio

from meridian_dataset import TUNING_3D2_SEED
from tier0.agent import Features
from tier0.evals import m3d2_tuning as tun
from tier0.evals.runner import run_eval


def test_every_tuning_ticket_has_a_label() -> None:
    counts = collections.Counter(group for group, _ in tun.tuning_labels().values())
    assert counts == {
        "everyday": 9,
        "colleague": 16,
        "outsider": 4,
        "reset": 14,
        "several": 12,
        "repeat": 16,
    }


def test_the_report_renders_from_a_saved_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = anyio.run(lambda: run_eval(TUNING_3D2_SEED, k=1, features=Features(tier_c=True)))
    data = {**report.to_dict(), "setup": "haiku-3d2"}
    (tmp_path / "haiku-3d2").mkdir()
    (tmp_path / "haiku-3d2" / "report.json").write_text(json.dumps(data))
    by_arm = tun.load(tmp_path)
    assert set(by_arm) == {"haiku-3d2"}
    n = by_arm["haiku-3d2"]
    assert (n["reset_prepared"].of, n["repeat_over"].of, n["duplicate"].of) == (14, 4, 3)
    md = tun.render(by_arm)
    # Named by the prompts the report says ran: the stub run used the defaults, T1 and C1.
    assert "| haiku-3d2 (T1/C1) |" in md and "tuning score, and optimistic" in md
