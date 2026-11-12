"""Watch a big local model's pace against its timing estimate (ADR-031).

`m3_selection time` times 10 calls per big model and saves the mean here. During a
tuning, dev-set or v3 run, every call is timed; once the last 10 calls average more than
3x the estimate, one `SLOW:` line is printed (and logged), so the user hears about it
instead of anyone waiting it out. The run itself goes on: stopping it is the user's call.
"""

import json
import sys
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tier0.models import ModelAdapter, ModelRequest, ModelResponse

TIMINGS = Path("out/m3/timing")
LOG = Path("out/m3/watch.jsonl")
SLOW_FACTOR = 3.0
WINDOW = 10


def _timing_path(model: str) -> Path:
    return TIMINGS / f"{model.replace(':', '-')}.json"


def save_timing(model: str, timing: dict[str, Any]) -> Path:
    TIMINGS.mkdir(parents=True, exist_ok=True)
    path = _timing_path(model)
    path.write_text(json.dumps(timing, indent=2) + "\n")
    return path


def expected_seconds(model: str) -> float | None:
    """The timed mean seconds per call, or None when the model was never timed."""
    path = _timing_path(model)
    if not path.exists():
        return None
    return float(json.loads(path.read_text())["seconds_per_call"]["mean"])


def log(event: dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), **event}))
        f.write("\n")


class SlowWatch:
    """Times calls; says SLOW once when the last WINDOW calls average over 3x expected."""

    def __init__(
        self,
        label: str,
        expected: float,
        *,
        factor: float = SLOW_FACTOR,
        window: int = WINDOW,
        say: Callable[[str], None] | None = None,
    ) -> None:
        self.label = label
        self.expected = expected
        self.factor = factor
        self.recent: deque[float] = deque(maxlen=window)
        self.calls = 0
        self.warned = False
        self._say = say or (lambda text: print(text, file=sys.stderr, flush=True))

    def record(self, seconds: float) -> None:
        self.calls += 1
        self.recent.append(seconds)
        # The first call may include loading the model, so only full windows after it.
        if self.warned or self.calls <= self.recent.maxlen:  # type: ignore[operator]
            return
        mean = sum(self.recent) / len(self.recent)
        if mean > self.factor * self.expected:
            self.warned = True
            text = (
                f"SLOW: {self.label} is averaging {mean:.1f} s per call over the last "
                f"{len(self.recent)} calls, {mean / self.expected:.1f}x its timed "
                f"{self.expected:.1f} s (more than {self.factor:g}x)."
            )
            log({"event": "slow", "label": self.label, "mean_s": mean, "expected_s": self.expected})
            self._say(text)


class WatchedModel:
    """A model adapter whose calls are timed by a SlowWatch; everything else passes
    through (seed, temperature, describe...)."""

    def __init__(self, inner: ModelAdapter, watch: SlowWatch) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_watch", watch)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.perf_counter()
        try:
            return self._inner.complete(request)
        finally:
            self._watch.record(time.perf_counter() - started)
