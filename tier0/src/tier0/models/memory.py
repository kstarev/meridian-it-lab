"""Memory headroom before a big local model runs (ADR-031).

On a 32 GB Mac a 15-20 GB model leaves little room, and a run that runs the machine out
of memory gets killed part-way. So before each big-model run tier0 checks what macOS can
hand out (free, inactive and speculative pages, from `vm_stat`) and warns, and logs,
when less than about 4 GB would be left once the model is loaded. The run goes on even
if macOS has to swap (the user's call, 2026-11-12); it is never paused.
"""

import re
import subprocess

import httpx2

from tier0.models.base import ModelError
from tier0.models.ollama import DEFAULT_OLLAMA_URL, _tagged

GB = 10**9
HEADROOM_BYTES = 4 * GB
_RECLAIMABLE = ("Pages free", "Pages inactive", "Pages speculative")


def _read_vm_stat() -> str | None:
    """vm_stat's output, or None when it isn't there (not a Mac) or fails."""
    try:
        return subprocess.run(
            ["/usr/bin/vm_stat"], capture_output=True, text=True, check=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def available_bytes(vm_stat: str | None = None) -> int | None:
    """Bytes macOS can hand out now, or None when vm_stat isn't there (not a Mac)."""
    vm_stat = _read_vm_stat() if vm_stat is None else vm_stat
    if vm_stat is None:
        return None
    page = re.search(r"page size of (\d+) bytes", vm_stat)
    if page is None:
        return None
    pages = 0
    for label in _RECLAIMABLE:
        found = re.search(rf"^{label}:\s+(\d+)\.", vm_stat, re.MULTILINE)
        if found is None:
            return None
        pages += int(found.group(1))
    return pages * int(page.group(1))


# A local run counts as swapped only above this many pages swapped out during it: 4,096
# pages, 64 MiB at this Mac's 16 KiB pages. Picked from the dev sets (ADR-031): the swap
# seen during Claude runs, which comes from other processes, was 328-2,952 pages in 5 of
# its 6 cases (one outlier at 30,956), while 63 of qwen's 70 swapping v2 runs were above.
SWAP_THRESHOLD_PAGES = 4096


def swapouts(vm_stat: str | None = None) -> int | None:
    """macOS's running count of pages swapped out to disk, or None when it can't be read.
    A run whose count goes up made the Mac swap, so its latency measures the machine, not
    the model (the user's rule, 2026-11-12)."""
    vm_stat = _read_vm_stat() if vm_stat is None else vm_stat
    if vm_stat is None:
        return None
    found = re.search(r"^Swapouts:\s+(\d+)\.", vm_stat, re.MULTILINE)
    return int(found.group(1)) if found else None


def model_bytes_to_load(
    model: str, *, base_url: str = DEFAULT_OLLAMA_URL, client: httpx2.Client | None = None
) -> int:
    """What loading the model will still take: 0 if Ollama already has it loaded, else its
    size on disk (a lower bound; the context cache comes on top)."""
    client = client or httpx2.Client(base_url=base_url, timeout=30.0)
    wanted = _tagged(model)
    try:
        loaded = client.get("/api/ps")
        loaded.raise_for_status()
        if any(m["name"] == wanted for m in loaded.json().get("models", [])):
            return 0
        tags = client.get("/api/tags")
        tags.raise_for_status()
        return next(int(m["size"]) for m in tags.json()["models"] if m["name"] == wanted)
    except httpx2.HTTPError as exc:
        raise ModelError(f"Ollama could not report model sizes: {exc}") from exc
    except StopIteration:
        raise ModelError(f"Ollama does not list the model {model!r}") from None
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelError(f"Ollama returned an unexpected response: {exc}") from exc


def headroom(
    model: str,
    *,
    available: int | None = None,
    to_load: int | None = None,
    base_url: str = DEFAULT_OLLAMA_URL,
    client: httpx2.Client | None = None,
) -> tuple[bool, str]:
    """Whether at least HEADROOM_BYTES would stay free with the model loaded, and a
    message saying how much is free and how much is needed."""
    available = available_bytes() if available is None else available
    if available is None:
        return True, "free memory unknown (no vm_stat); not checked"
    if to_load is None:
        to_load = model_bytes_to_load(model, base_url=base_url, client=client)
    left = available - to_load
    message = (
        f"{available / GB:.1f} GB free, {model} needs {to_load / GB:.1f} GB more to load, "
        f"so {left / GB:.1f} GB would be left (at least {HEADROOM_BYTES / GB:.0f} GB wanted)"
    )
    return left >= HEADROOM_BYTES, message
