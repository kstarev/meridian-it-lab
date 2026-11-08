"""Write a generated world to JSON files.

The answer key is a separate file because the agent must never see it. No
timestamps come from the wall clock, so the same seed gives identical bytes.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from meridian_dataset.model import World


def _dump(obj: Any) -> bytes:
    """Stable JSON: sorted keys, fixed indent, UTF-8, `\\n` newlines."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_dataset(world: World, out_dir: Path) -> dict[str, Path]:
    """Write the four files into out_dir and return {file name: path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    everything = asdict(world)
    del everything["tickets"]
    del everything["answer_key"]
    key = asdict(world.answer_key)
    contents = {
        "world.json": everything,
        "tickets.json": {
            "seed": world.seed,
            "generator_version": world.generator_version,
            "tickets": [asdict(t) for t in world.tickets],
        },
        "answer_key.json": {
            "seed": key["seed"],
            "generator_version": key["generator_version"],
            "entries": key["entries"],
            "planted_problems": key["planted_problems"],
        },
    }
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for name, obj in contents.items():
        data = _dump(obj)
        path = out_dir / name
        path.write_bytes(data)
        paths[name] = path
        hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = out_dir / "manifest.json"
    manifest.write_bytes(
        _dump({"seed": world.seed, "generator_version": world.generator_version, "files": hashes})
    )
    paths["manifest.json"] = manifest
    return paths
