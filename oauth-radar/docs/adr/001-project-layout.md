# OAR-001: `oauth-radar/` in the lab repo, with its own package, brief and decisions

**Date:** 2026-11-15 · **Status:** Proposed; for the owner's review.

**Context:** Shadow AI Radar (`oauth-radar`) is the lab's third project (lab brief, "In scope": "Four projects (tier0, access-as-code, oauth-radar, evidence)"). It needs the same fake Meridian and the same standards as the others: one lockfile, pinned versions, offline tests and CI on every pull request (lab ADR-012). It also needs its own space, so tier0's and Access-as-Code's code, tests, decision logs and published results stay untouched. Access-as-Code set the pattern with `access/` ([AAC-001](../../../access/docs/adr/001-repo-layout.md)).

**Decision:**

- **A new top-level folder, `oauth-radar/`,** named as the lab brief names the project. The folder isn't a Python package, so the hyphen is safe. The import name is `meridian_oauth_radar`, the distribution `meridian-oauth-radar`, like `meridian_access`.
- **A uv workspace member from milestone 0,** under the same `uv.lock`, ruff settings and pytest rules. The root `pyproject.toml` lists it as a member, a dependency and a workspace source, adds `oauth-radar/tests` to the test paths and `oauth-radar/src` to ruff's sources. A test checks all of that ([`test_the_root_workspace_includes_this_package`](../../tests/test_package.py)).
- **Layout,** filled in milestone by milestone ([milestones](../../MILESTONES.md)):

  | Path | What lives there | From |
  |---|---|---|
  | `oauth-radar/BRIEF.md`, `oauth-radar/MILESTONES.md` | The brief and the milestone plan | 0 |
  | `oauth-radar/docs/adr/` | These decisions, one file each | 0 |
  | `oauth-radar/src/meridian_oauth_radar/` | The data model (`__init__.py`), loaders (`load`), rules (`rules`) and the command line (`__main__`); later scoring, the ticket draft and evals | 0–3 |
  | `oauth-radar/tests/` | Tests and hand-written fake fixtures, in the root test run | 0 |
  | `oauth-radar/policy/` | IT's reviewed files: the scoring table, the banned-app list, the app catalogue (which apps are AI, which are reviewed) | 2 |
  | `dataset/src/meridian_dataset/oauth/` | The OAuth grant generator, inside the shared dataset package ([OAR-003](003-grants-enter-the-shared-dataset.md)) | 1 |
  | `docs/evals/` | Published reports, named `<date>-oauth-radar-...` | 2 |

- **Make targets,** when they come in milestone 1, are prefixed `oauth-` (`make oauth-data`, `make oauth-scan`). Every one runs offline on fake data with no credentials.
- **Decisions are numbered OAR-001 onwards, in this folder.** Nothing is added to the lab's `docs/decisions.md`, so its numbering and the site's count of tier0 decisions stay as they are. The lab's records are cited as "lab ADR-NNN", Access-as-Code's as AAC-NNN.
- **Outside this folder,** milestone 0 changes only the root `pyproject.toml`, `uv.lock` and one line in the lab brief that points here. The root CI needs no change: `make lint` and `make test` already cover every workspace member.

**Consequences:**

- One clone, one `uv sync` and one CI run cover all three projects. tier0's and Access-as-Code's tests run on every `oauth-radar` pull request too.
- A reader who only wants this project starts at `oauth-radar/BRIEF.md`.
- The repo grows again, and the root `pyproject.toml` has one more entry to keep in step.
