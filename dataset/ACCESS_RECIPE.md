# Access-as-Code recipe, format 2

A recipe is one JSON file. It describes a fake company's access mess: how many of each problem, and what the mess looks like. The generator builds the whole world from it. You do not need to read any code.

You decide what the world looks like. The kinds of finding and the expected actions are a fixed list. You pick from that list.

## Get the lists first

Run this and keep the output next to you:

    uv run python -m meridian_dataset.access.recipe_tools catalog

It prints the departments, role ids, app ids, countries, field names, finding types, actions, event kinds, display styles and username placeholders. It prints nothing else.

## The file

The top level is one object. An unknown key is an error, so a typo fails at once. Every value has a fixed type. Dates are `YYYY-MM-DD`. Days are whole numbers from 0 to 29: day 0 is 2026-11-01. The world is the state on 2026-12-01.

| Key | Type | Notes |
|---|---|---|
| `format` | `2` | Required. |
| `name` | string | Required. |
| `seed` | integer | Optional. The command line uses it when you give no seed. |
| `headcount` | integer | Required, at least 100. People who work for the company, current or just terminated by a bulk import. |
| `contractors` | integer | Default 0. At most a quarter of the headcount. |
| `orphaned_accounts` | integer | Default 0. Accounts with no active person. |
| `orphaned_former_employees` | integer | Default 0. How many of those belong to former employees. Cannot exceed `orphaned_accounts`. |
| `movers` | integer | Default 0. People who changed role and kept old access. |
| `leavers_with_access` | integer | Default 0. |
| `rehires` | integer | Default 0. |
| `contractors_past_end` | integer | Default 0. Cannot exceed `contractors`. |
| `leave_of_absence` | integer | Default 0. |
| `mass_termination` | object | Default none. `events` (0 or 1), `department`, `people`, `day` (1 to 29). |
| `drift` | integer | Default 0. Access granted by hand outside the process. |
| `break_glass` | integer | Default 0. |
| `name_changes` | integer | Default 0. |
| `duplicate_name_pairs` | integer | Default 0. Two people with the same name. |
| `names` | object | See below. |
| `username_patterns` | list of strings | See below. |
| `date_formats` | list | Required. See below. |
| `export_layouts` | list | Required. See below. |
| `exports` | object | See below. |
| `custom_cases` | list | Default empty. See below. |

Case people never count toward the counts above, except that a case person who is a current worker takes one place in the headcount.

### names

    "names": {
      "extra_given": ["Zoë", "D'Arcy"],
      "extra_family": ["O'Brien", "Nguyễn", "Ковалёв"],
      "extra_share": 0.1,
      "display_styles": ["first_last", "last_comma_first"]
    }

- `extra_given` and `extra_family` are added to the built-in names. A name is 1 to 40 characters: any Unicode letters, spaces, apostrophes, hyphens and periods.
- `extra_share` is the share of generated people (0 to 1) who get a given name, a family name or both from these lists. It needs at least one list.
- `display_styles` is a non-empty subset of `first_last`, `last_comma_first`, `upper_first_last`, `preferred_last`, `first_initial_last`. Each export uses one of them for its display names. Default `["preferred_last"]`, which means "preferred name or first name, then family name".
- Emails are built from an ASCII form of the name. Accents are dropped. Apostrophes, periods, spaces and hyphens are dropped. Letters with no ASCII form (for example Cyrillic) become `u` plus their code points in hex. The email is `first.last@meridian.example`. The second person with the same address gets the number 2, and so on.

### username_patterns

A list of templates for exports that list usernames and no email. Placeholders:

- `{first}` and `{last}`: the ASCII form of the names, lowercase.
- `{f}` and `{l}`: their first letters.
- `{n}`: the duplicate number from the email, or an empty string.

You may add literal characters: letters, digits, `_`, `.`, `-`, `+`. The result is lowercased. Any other placeholder is an error. Username-only exports take the patterns in turn. If two people in one export get the same username, the second gets a number added. Default: `["{first}.{last}{n}"]`.

### date_formats

A non-empty list. Each item has a unique `name` and exactly one of:

- `strftime`: a Python format such as `"%d.%m.%y"`. `%-d` and `%-m` (no zero padding) work on every platform.
- `special`: `"excel_serial"` (days since 1899-12-30) or `"unix_epoch"` (seconds at midnight UTC).

Each format must render 2026-11-03 to a non-empty string. Exports take the formats in turn, so every format is used.

### export_layouts

The 95 exports of apps that have no single sign-on each use one layout. A layout:

    {
      "name": "E",
      "delimiter": ";",
      "columns": [
        {"header": "Mail-Adresse", "field": "email"},
        {"header": "Letzter Login", "field": "last_login"}
      ],
      "preamble": ["Export by the vendor", "Report: all users"],
      "footer": ["End of report"],
      "quote_all": false,
      "role_labels": {"member": "Mitglied", "admin": "Administrator"},
      "status_labels": {"active": "Aktiv", "inactive": "Gesperrt"}
    }

- `name`: unique.
- `delimiter`: one of `,` `;` tab `|`.
- `columns`: header text and a `field`. The fields are `email`, `username`, `full_name`, `display_name`, `first_name`, `last_name`, `role_level`, `last_login`, `created`, `status`, `department`, `title`.
- A layout needs an `email` or a `username` column. A layout with `username` and no `email` is username-only.
- `preamble`: optional lines written before the header. `footer`: optional lines after the last row. Each is one line.
- `quote_all`: optional, quote every cell.
- `role_labels` (`member`, `admin`) and `status_labels` (`active`, `inactive`) are optional. Defaults: `member`, `admin`, `active`, `inactive`.
- Dates in `last_login` and `created` use the export's date format. An account that never logged in has an empty `last_login`.
- Lines end with `\r\n`.

Every layout must be used by at least one export. If you list more layouts than there are exports to hold them, the check fails.

### exports

    "exports": {
      "username_only": 20,
      "encodings": {"cp1252": 3, "utf-16": 2},
      "noise": {
        "blank_rows_max": 2,
        "mixed_case_email_share": 0.08,
        "trailing_space_share": 0.06,
        "duplicate_row_share": 0.05
      }
    }

- `username_only`: how many exports use a username-only layout. It must be at least the number of username-only layouts, and above 0 if you have any. If you have none, it must be 0.
- `encodings`: Python codec names and how many exports get each. The rest are `utf-8`. An export can only get a codec that can write all its text. If too few exports can, the check fails and names the codec. Legacy codecs such as `cp1252` go to exports that really need them when possible.
- `noise`: `blank_rows_max` blank rows per export (0 up to the number); the three shares are the chance per row (0 to 1). Mixed-case and trailing-space apply to the email cell, and together cannot pass 1. A duplicate row repeats the row right after it.

## Access policy (role → apps)

The policy every worker's standard access follows. It's an input, as a real IT team's access policy is, and the same for every recipe: no recipe field changes it. The answer key and any tool reviewing the world read the same table. `recipe_tools catalog` prints it too, and a test keeps this copy in step with the code.

<!-- access-policy:start -->
Every current worker has an Okta user, a Google user and a Slack user.

Birthright SSO apps, for every employee: amber-ledger, birch-vault, cobalt-notes, dune-hub, ember-atlas, fjord-docs, garnet-desk, harbor-pulse.
Contractors get the first 6 of them: amber-ledger, birch-vault, cobalt-notes, dune-hub, ember-atlas, fjord-docs.

SSO access is a group assignment with the `member` role. Non-SSO apps are rows in that app's export.

| Role | SSO apps beyond birthright | Non-SSO apps (export rows) |
|---|---|---|
| eng-backend | indigo-track, maple-forms, quartz-ledger | umber-relay, sable-track, quartz-vault, onyx-canvas, maple-hub, indigo-sheets |
| eng-frontend | nimbus-studio, raven-vault, vale-docs, amber-board | vale-ledger, tundra-signal, raven-notes, pebble-board, nimbus-atlas, pebble-docs |
| eng-sre | sable-notes, willow-desk, birch-forms, fjord-ledger, juniper-atlas | willow-vault, umber-canvas, sable-hub, quartz-forms, onyx-docs, willow-forms |
| eng-data | yarrow-pulse, cobalt-studio, garnet-vault, kestrel-docs, onyx-signal, sable-studio | yarrow-notes, vale-board, tundra-atlas, raven-studio, ember-docs |
| sales-ae | dune-sheets, harbor-notes, lumen-desk, pebble-canvas, tundra-sheets, lumen-board, pebble-relay | zephyr-hub, willow-forms, umber-docs, sable-sheets, lumen-forms |
| sales-sdr | indigo-hub, maple-pulse, quartz-board, indigo-track, maple-forms, quartz-ledger, umber-atlas, zephyr-track | amber-desk, yarrow-studio, vale-desk, tundra-relay, sable-hub |
| sales-ops | nimbus-track, raven-forms, juniper-signal | birch-pulse, zephyr-sheets, willow-pulse, umber-ledger, amber-forms |
| cs-csm | sable-studio, kestrel-canvas, onyx-sheets, sable-notes | cobalt-track, amber-vault, yarrow-track, vale-vault, harbor-hub |
| cs-support | lumen-board, pebble-relay, tundra-hub, yarrow-pulse, cobalt-studio | dune-signal, birch-notes, zephyr-signal, willow-notes, onyx-canvas |
| cs-onboarding | quartz-ledger, umber-atlas, zephyr-track, dune-sheets, harbor-notes, lumen-desk | ember-canvas, cobalt-hub, amber-forms, yarrow-hub, vale-vault |
| mkt-pmm | vale-docs, amber-board, ember-relay, indigo-hub, maple-pulse, quartz-board, indigo-track | fjord-board, dune-atlas, birch-studio, zephyr-atlas, dune-canvas |
| mkt-content | birch-forms, fjord-ledger, juniper-atlas, nimbus-track, raven-forms, juniper-signal, nimbus-studio, raven-vault | garnet-forms, ember-docs, cobalt-sheets, amber-pulse, kestrel-vault |
| mkt-growth | garnet-vault, kestrel-docs, onyx-signal | harbor-studio, fjord-desk, dune-relay, birch-track, willow-vault |
| fin-analyst | lumen-desk, pebble-canvas, tundra-sheets, lumen-board | indigo-sheets, garnet-pulse, ember-ledger, cobalt-signal, ember-canvas |
| fin-accountant | quartz-board, indigo-track, maple-forms, quartz-ledger, umber-atlas | juniper-relay, harbor-track, fjord-vault, dune-canvas, lumen-vault |
| ppl-partner | juniper-signal, nimbus-studio, raven-vault, vale-docs, amber-board, ember-relay | kestrel-ledger, indigo-signal, garnet-notes, ember-board, sable-track |
| ppl-recruiter | onyx-sheets, sable-notes, willow-desk, birch-forms, fjord-ledger, juniper-atlas, nimbus-track | lumen-vault, juniper-canvas, harbor-hub, fjord-forms, amber-vault |
| it-lead | tundra-hub, yarrow-pulse, cobalt-studio, garnet-vault, kestrel-docs, onyx-signal, sable-studio, kestrel-canvas | maple-notes, kestrel-board, indigo-atlas, garnet-studio, harbor-track |
| it-engineer | zephyr-track, dune-sheets, harbor-notes | nimbus-hub, lumen-forms, juniper-docs, harbor-sheets, onyx-relay |
| prod-pm | ember-relay, indigo-hub, maple-pulse, quartz-board | onyx-atlas, maple-studio, kestrel-desk, indigo-relay, vale-desk |
| prod-analyst | juniper-atlas, nimbus-track, raven-forms, juniper-signal, nimbus-studio | pebble-docs, nimbus-sheets, lumen-pulse, juniper-ledger, dune-relay |
| des-product | onyx-signal, sable-studio, kestrel-canvas, onyx-sheets, sable-notes, willow-desk | quartz-desk, onyx-relay, maple-track, kestrel-vault, kestrel-desk |
| des-brand | tundra-sheets, lumen-board, pebble-relay, tundra-hub, yarrow-pulse, cobalt-studio, garnet-vault | raven-pulse, pebble-ledger, nimbus-signal, lumen-notes, raven-studio |
<!-- access-policy:end -->

## Public expected-action rules

The answer key follows these rules. Your custom cases should follow them too, so the key stays unambiguous.

- A row in a non-SSO export can't be changed by API, so its action is `flag`.
- A leaver (terminated during the month): the Okta user and the Google user get `suspend`; Slack and SSO app access get `remove`.
- A former employee (terminated before the month) with leftover access gets type `orphaned_account` and the same actions as a leaver.
- An account nobody owns is an `orphaned_account`: `remove` for Okta, Google, Slack and SSO app access, `flag` for an export row. An orphaned Okta user's app access gets its own `remove` finding (system `okta`, with the app id).
- A person on leave: `suspend` (Okta, Google, Slack), never remove.
- A mover: `remove` old-role SSO access the new role doesn't include, `add` the new-role apps they are missing (in a username-only export the account is the username that export's pattern gives them, with the duplicate number and any in-export collision suffix), `flag` old export rows.
- A contractor past their end date: `remove` API-backed access, `flag` export rows.
- Break-glass accounts: `never_touch`, with a finding in every system they exist in (Okta and Google).
- A bulk HR error affecting many people: one `block_two_approvers` finding with system `hris`, and no removals for those people. Other bad HR data is `hr_data_error` with `flag`.
- Access granted by hand outside the process: `remove`. A privilege raised by hand on an app the person already has: `flag`.

## Custom cases

A case is a small, hand-written HR edge case. It lists people, HR events, accounts and findings. The generator adds them after the normal problems, in the order you list them. Refs (`"ref"`) are short names that you choose. They only have meaning inside one case.

    {
      "id": "converted-contractor",
      "description": "A contractor became an employee and kept a contractor-only app.",
      "people": [
        {
          "ref": "noor",
          "given_name": "Noor",
          "family_name": "Haddad",
          "preferred_name": "Noor",
          "role_id": "eng-backend",
          "employment_type": "employee",
          "country": "DE",
          "status": "active",
          "start_date": "2025-03-01",
          "end_date": "",
          "job_title": "Sr. Backend Eng",
          "work_email": "",
          "current_worker": true,
          "true_role_id": "eng-backend",
          "standard_access": true
        }
      ],
      "events": [
        {"day": 12, "ref": "noor", "kind": "field_change",
         "fields": {"employment_type": "employee"}, "source": "hr"}
      ],
      "accounts": [
        {"ref": "noor", "system": "<a non-SSO app id>", "state": "active",
         "role": "member", "last_login_day": 20},
        {"ref": null, "system": "slack", "account": "legacy-bot", "last_login_day": -1}
      ],
      "findings": [
        {"type": "mover_stale_access", "ref": "noor", "system": "<the same app id>",
         "app_id": "<the same app id>", "expected_action": "remove", "day": 12,
         "detail": "Kept a contractor-only app after conversion."},
        {"type": "orphaned_account", "ref": null, "account": "legacy-bot",
         "system": "slack", "expected_action": "remove", "day": 0}
      ]
    }

### people

Required: `ref`, `given_name`, `family_name`, `role_id`, `employment_type` (`employee` or `contractor`), `country` (a country from the catalog), `status` (`active`, `on_leave` or `terminated`), `start_date`.

Optional: `preferred_name`, `end_date`, `leave_start`, `leave_end`, `job_title` (free text; default is one of the role's usual titles), `work_email` (default `first.last@meridian.example`, numbered if taken), `current_worker` (default false), `true_role_id` (default `role_id`), `standard_access` (default false).

- A person with `status: "on_leave"` needs `leave_start`.
- Case people get fresh employee ids.
- `current_worker: true` counts toward the headcount and gets a true role in the answer key. The generator makes fewer ordinary workers so the headcount stays the same. A current worker cannot be in IT.
- `standard_access: true` gives the normal accounts for the role: an Okta user with the standard single sign-on assignments, a Google user, a Slack user, and rows in the role's non-SSO exports.

### events

`day` (0 to 29), `ref`, `kind` (`hire`, `move`, `terminate`, `leave_start`, `leave_end`, `rehire`, `name_change`, `field_change`), optional `fields` (text to text) and `source` (default `hr`). Events appear in the HR feed. The person's record is exactly what you wrote in `people`.

### accounts

- `ref`: a person, or `null` for an account with no owner.
- `system`: `okta`, `google`, `slack` or an app id.
- `account`: the account name. Required when `ref` is null. Otherwise the default is the person's email, or for Slack the part before the `@`, or for an export row the email (or the username in a username-only export).
- `state`: `active` (default), `suspended` or `deprovisioned`.
- `role`: `member` (default) or `admin`.
- `last_login_day`: 0 to 29, or -1 for never. Default 29.
- `granted_by_hand_day`: only for an SSO app. Logs the access as a human admin grant on that day.

System rules:

- `okta`, `google`, `slack` make that user. A new Okta, Google or Slack account may not reuse a name that already exists.
- An SSO app id gives that person access to the app in Okta. The person needs an Okta user: set `standard_access`, or add an `okta` account for the same ref. With `ref: null`, `account` must name an `okta` account in the same case with no ref.
- A non-SSO app id adds a row to that app's export.
- Every account except an SSO app access gets an owner in the answer key: the person's employee id, or "" if `ref` is null.

### findings

`type` (`orphaned_account`, `mover_stale_access`, `leaver_access_left`, `rehire`, `contractor_past_end`, `leave_of_absence`, `hris_mass_termination`, `drift`, `break_glass`, `hr_data_error`), `system`, `expected_action` (`remove`, `suspend`, `add`, `flag`, `never_touch`, `block_two_approvers`), `day` (0 to 29). Optional: `ref`, `account`, `app_id`, `detail`.

- An SSO app access is `system: "okta"` with an `app_id`. An Okta user is `system: "okta"` with no `app_id`. Google and Slack are systems. A non-SSO app row is `system` and `app_id` both the app id. A finding about the HR feed uses `system: "hris"`.
- `account` may be empty if `ref` is set. Then it means that person's account in that system.
- Unless the action is `add` (or the system is `hris`), the account must exist: an account in the case, or a standard account of a person with `standard_access`.
- The answer key gets the finding as written, with the ref turned into an employee id and the account written exactly as it appears in the files.

## Checks that fail with a clear message

Unknown keys. Counts that are not whole non-negative numbers, or that do not add up. A format other than 2. Unknown finding types, actions, event kinds, fields, countries, roles or apps. Unknown placeholders. Bad dates or day numbers. Missing refs. A layout without `email` or `username`. A bad delimiter. An unknown codec, or too few exports that can hold it. A layout or date format that no export could use. A finding about an account that does not exist.

## Answer-key checks

`check` and `seal` also check the answer key against the world the recipe builds, and fail if any check finds a problem. The message names the checks and their counts only.

- Every finding and owner points to a person and an account that exist (an `add` finding may name an account that doesn't exist yet).
- No finding is listed twice, and no account gets two findings with different types or actions.
- Per problem type, the key holds what the counts above plant plus what your custom cases list. Drift, mass termination and HR data errors count per finding; the others per distinct person. Orphaned accounts count distinct accounts (Okta, Google and Slack users and export rows), not the app-access findings of an orphaned Okta user. Break-glass counts distinct identities (logins), however many systems each appears in.
- Problems visible in the world itself have a finding: a bulk HR import, every break-glass account (and nothing else touches them), every account nobody owns, active accounts of terminated people, people on leave with active accounts, grants made by hand in the Okta log, and contractors past their end date.
- Every assignment of an orphaned Okta user has an app-level `orphaned_account` finding.
- Every Okta and Google account of a break-glass identity has a `break_glass` finding in that system.
- Every privilege change in the Okta log (by hand, through the admin console) points at an assignment that exists and has the admin role; privilege drift never creates an assignment.

## Check and seal

Check a recipe. It builds the world in memory and prints `ok`, the recipe digest and counts only (people, exports, layouts used, date formats used, custom cases, findings by type). It prints no names or accounts. On a problem it prints the error and exits with 1.

    uv run python -m meridian_dataset.access.recipe_tools check path/to/recipe.json

Seal a recipe. It runs the check, then writes `recipes/<name>.json.gz` inside the package. The same recipe always makes the same bytes. It refuses if the file exists, if the name is `tuning`, or if the name does not start with `held_out_`. It prints the path and the digest, never the content.

    uv run python -m meridian_dataset.access.recipe_tools seal path/to/recipe.json --name held_out_example

The digest is the SHA-256 of the recipe as canonical JSON: sorted keys, no spaces, UTF-8, no ASCII escaping.

A recipe file can be plain `.json` or `.json.gz` (gzip of the canonical JSON).
