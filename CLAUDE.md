# Rules for helpers in this repo

- Sealed sets are never opened, printed, searched or diffed. They are the files whose name
  starts with `held_out_`.
- Search with `python tools/search.py PATTERN` (or `make search q='pattern'`). It leaves the
  sealed files out, and `dataset/src/meridian_dataset/names.py`, which holds blind wording.
- Checks on a sealed set print counts, ids and enums only, never wording.
- A set the owner opened as a dev set is listed in `tools/sealed_paths.py` (`OPENED`).
- A hook (`tools/sealed_guard.py`) blocks reads and searches of sealed files. It never blocks a
  program that runs code. `MERIDIAN_SEALED_GUARD=off` turns it off.

More: [docs/sealed-search-guard.md](docs/sealed-search-guard.md).
