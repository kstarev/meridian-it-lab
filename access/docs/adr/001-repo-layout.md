# AAC-001: `access/` in the lab repo, with its own package and the shared dataset

**Date:** 2026-11-14 · **Status:** Accepted; built, with the open checks listed in [MILESTONES.md](../../MILESTONES.md).

**Context:** Access-as-Code is the lab's second project (lab brief, "In scope"). It needs the same fake Meridian, the same standards (pinned dependencies, one lockfile, offline tests, CI on every pull request) and its own space, so tier0's code, tests and published results stay untouched.

**Decision:**

- **A new top-level folder, `access/`,** in the meridian-it-lab repo. From milestone 2 it's a uv workspace member with its own Python package, `meridian_access` (not `access`, which is too generic to import safely), under the same `uv.lock`, ruff settings and pytest rules (lab ADR-012).
- **Layout,** filled in milestone by milestone:

  | Path | What lives there | From |
  |---|---|---|
  | `access/BRIEF.md`, `access/MILESTONES.md` | The brief and the milestone plan | 1 |
  | `access/docs/adr/` | These decisions, one file each | 1 |
  | `access/src/meridian_access/` | Code: `policy`, `desired`, `collectors`, `matching`, `plan`, `apply`, `drift`, `mocks`, `ai`, `evals` | 2–6 |
  | `access/policy/` | Policy as code (YAML): roles, apps, birthright apps, exceptions, break-glass allowlist, settings such as N; plus the reviewed maps (job title → role, account → person) | 3 |
  | `access/tests/` | Tests, added to the root pytest `testpaths` | 2 |
  | `access/plan/` | The current plan (`plan.json`) and its summary, written only by the bot on the plan branch | 5 |
  | `dataset/src/meridian_dataset/access/` | The access world generator, inside the shared dataset package ([AAC-004](004-extend-the-shared-dataset.md)) | 2 |
  | `.github/workflows/access-*.yml` | The nightly plan, apply and paid-eval workflows (GitHub only reads workflows from the root) | 5 |
  | `.github/CODEOWNERS` | `access/policy/` and `access/plan/` owned by the IT team | 5 |
  | `site/access/` | The results page, deployed by the existing Pages workflow | 7 |

- **Make targets** are prefixed `access-` (`make access-data`, `access-plan`, `access-eval`). Every target runs offline on fake data with no credentials, like tier0's. The only exception is the paid model runs, which run on GitHub ([AAC-008](008-runs-on-github-only.md)).
- **Decisions** are numbered AAC-001 onwards, in this folder, so tier0's decision log and the project site's count of tier0 decisions stay as they are.

**Consequences:**

- One clone, one `uv sync`, one CI run covers both projects, and the shared dataset can't drift between them.
- tier0's tests run on every Access-as-Code pull request too, so a dataset change that breaks tier0 is caught.
- The repo grows; a reader who only wants Access-as-Code starts at `access/BRIEF.md`.
