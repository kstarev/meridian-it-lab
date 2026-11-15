# OAR-005: Offline only: the radar reads export-shaped files, never an API, and never message content

**Date:** 2026-11-15 · **Status:** Proposed; for the owner's review.

**Context:** The lab brief puts all four projects "running offline on files shaped like Okta, Google Workspace, Slack, Jamf, Intune and Linear exports", and rules out live APIs and real tenants. The dataset (milestone 1) and the radar's loaders have to agree on which files exist and what's in them, so that shape is decided here. Grants are also sensitive: a scope can let an app read every message, so the radar must report what an app *may* reach without ever reading it itself.

**Decision:**

- **Inputs are files, nothing else.** "Shaped like" means the same field names and nesting for the fields the radar reads, not every field the real API returns.

  | File | Shaped like | Fields the radar reads | From |
  |---|---|---|---|
  | `google_tokens.json` | The Google Admin SDK Directory API's `tokens.list` response (`"kind": "admin#directory#tokenList"`), every user's tokens merged into one `items` list | `userKey`, `clientId`, `displayText`, `scopes` | 0 |
  | `google_app_access.json` | The Admin console's list of accessed third-party apps (app access control) | App name, client ID, verified or not, number of users, access setting (trusted, limited, blocked) | 1 |
  | `slack_integration_logs.json` | The Slack Web API's `team.integrationLogs` response | User ID, app ID, change type (added, removed, expanded), scopes, date | 1 |
  | `slack_apps.json` | Slack's admin list of approved and restricted apps | App ID, name, in the App Directory or not, internal or not, scopes | 1 |
  | `people.json` | A few HRIS fields from the Access world ([OAR-003](003-grants-enter-the-shared-dataset.md)) | Employee ID, work email, status, end date | 1 |
  | `oauth-radar/policy/*` | IT's own reviewed files: banned apps, app catalogue, scoring table | As each file's schema says | 2 |

  Milestone 1 sets the exact fields with the generator, and may change this table by a new ADR if a shape doesn't fit.
- **Milestone 0 reads one shape,** the Google token export, plus a small banned-app list, in `meridian_oauth_radar.load`.
- **No network and no credentials.** The package imports no network or AI library, and a test reads the source to check ([`test_the_package_imports_no_network_or_ai_library`](../../tests/test_guards.py)). The lab's test-wide guard blocks the network at run time too. No file holds a token value: `tokens.list` returns none, and the Slack shapes carry none. Access-as-Code's repo-wide test for Slack token strings covers these files as well.
- **Never message or file content.** Every shape above is metadata: who, which app, which scopes, when. The loader reads only the named fields and ignores the rest, so nothing else can reach a rule ([`test_extra_fields_never_reach_a_grant`](../../tests/test_load.py)). A `Grant` has no field for content ([`test_a_grant_has_no_field_for_content`](../../tests/test_model.py)). When a scope lets an app read every message, the finding says so. The radar never reads one.
- **Nothing is dropped silently.** A file or item the loader can't read stops the load, with the file and item number ([`test_a_bad_item_stops_the_load_and_says_where`](../../tests/test_load.py)), because a skipped grant would be a missed finding. A later milestone may list bad rows in the report instead, as Access-as-Code does, but never drop them.

**Consequences:**

- The radar runs anywhere, with no setup and nothing to leak.
- Real exports have quirks these shapes won't: extra fields, renamed columns, paging, partial files. The results say so.
- A real deployment would need a collector with read-only admin credentials to produce these files. That's out of the lab's scope, and would be a new ADR.
