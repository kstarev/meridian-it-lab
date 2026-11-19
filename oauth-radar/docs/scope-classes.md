# The scope explainer's classes, checked against Google's lists

**Checked:** 2026-11-19, on Google's public developer and help pages listed below. The table is [`scopes.py`](../src/meridian_oauth_radar/scopes.py); a test checks that this page lists every scope in it with the same class.

**Result: no class in the table is wrong.**

- **Restricted: fully confirmed.** Every scope the table calls restricted is on Google's list of restricted scopes [3], and no other scope in the table is on it. This is the class the policy engine acts on (a dormant grant with a restricted scope gets a notice, then a plan).
- **Sensitive and non-sensitive: confirmed where a page says so.** Gmail, Drive, Docs, Sheets and Slides publish a class for each scope [1, 2, 4, 5, 6], and every one matches. For Calendar and Contacts, Google's sensitive-scope page gives "reading events stored in Google Calendar" and "storing a new contact in Google Contacts" as examples of sensitive scopes [7]. For the sign-in scopes, it says the scopes "necessary for Google Sign-In are pre-filled in the Non-sensitive scopes section" [7], and the sign-in scopes are `openid`, `email` and `profile` [8].
- **Not confirmed by any public page:** `userinfo.email` and `userinfo.profile` by name, `contacts.readonly`, `directory.readonly`, `tasks`, and the three Admin SDK scopes. Google's pages for Calendar [9], Tasks [10], the People API [11], the Admin SDK [12] and the full scope list [13] give no class. Their classes stand as written; none of them is on the restricted list [3].
- **`admin` is the radar's own class, not Google's.** It marks Admin SDK scopes, which reach other people's accounts, so the rubric counts them as broad (OAR-008). Google doesn't list them as restricted [3].

| Scope | Class in the table | What Google's pages say | Source |
|---|---|---|---|
| `openid` | non-sensitive | Confirmed: a sign-in scope, pre-filled as non-sensitive | [7], [8] |
| `email` | non-sensitive | Confirmed: a sign-in scope, pre-filled as non-sensitive | [7], [8] |
| `profile` | non-sensitive | Confirmed: a sign-in scope, pre-filled as non-sensitive | [7], [8] |
| `https://www.googleapis.com/auth/userinfo.email` | non-sensitive | Not confirmed by name; the URL form of `email`. Not restricted | [3], [13] |
| `https://www.googleapis.com/auth/userinfo.profile` | non-sensitive | Not confirmed by name; the URL form of `profile`. Not restricted | [3], [13] |
| `https://mail.google.com/` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.readonly` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.modify` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.compose` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.insert` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.metadata` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.settings.basic` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.settings.sharing` | restricted | Confirmed: restricted | [1], [3] |
| `https://www.googleapis.com/auth/gmail.send` | sensitive | Confirmed: sensitive | [1] |
| `https://www.googleapis.com/auth/gmail.labels` | non-sensitive | Confirmed: non-sensitive | [1] |
| `https://www.googleapis.com/auth/drive` | restricted | Confirmed: restricted | [2], [3] |
| `https://www.googleapis.com/auth/drive.readonly` | restricted | Confirmed: restricted | [2], [3] |
| `https://www.googleapis.com/auth/drive.metadata.readonly` | restricted | Confirmed: restricted | [2], [3] |
| `https://www.googleapis.com/auth/drive.file` | non-sensitive | Confirmed: non-sensitive (recommended) | [2], [4], [5], [6] |
| `https://www.googleapis.com/auth/documents` | sensitive | Confirmed: sensitive | [5] |
| `https://www.googleapis.com/auth/documents.readonly` | sensitive | Confirmed: sensitive | [5] |
| `https://www.googleapis.com/auth/spreadsheets` | sensitive | Confirmed: sensitive | [4], [6] |
| `https://www.googleapis.com/auth/spreadsheets.readonly` | sensitive | Confirmed: sensitive | [4], [6] |
| `https://www.googleapis.com/auth/presentations` | sensitive | Confirmed: sensitive | [6] |
| `https://www.googleapis.com/auth/calendar` | sensitive | Confirmed by Google's example (reading Calendar events is sensitive); not restricted | [3], [7] |
| `https://www.googleapis.com/auth/calendar.readonly` | sensitive | Confirmed by Google's example (reading Calendar events is sensitive); not restricted | [3], [7] |
| `https://www.googleapis.com/auth/calendar.events` | sensitive | Confirmed by Google's example (reading Calendar events is sensitive); not restricted | [3], [7] |
| `https://www.googleapis.com/auth/calendar.events.readonly` | sensitive | Confirmed by Google's example (reading Calendar events is sensitive); not restricted | [3], [7] |
| `https://www.googleapis.com/auth/contacts` | sensitive | Confirmed by Google's example (storing a new contact is sensitive); not restricted | [3], [7] |
| `https://www.googleapis.com/auth/contacts.readonly` | sensitive | Not confirmed; not restricted | [3] |
| `https://www.googleapis.com/auth/directory.readonly` | sensitive | Not confirmed; not restricted | [3] |
| `https://www.googleapis.com/auth/tasks` | sensitive | Not confirmed; not restricted | [3] |
| `https://www.googleapis.com/auth/admin.directory.user` | admin | The radar's own class; Google's not confirmed; not restricted | [3], [12] |
| `https://www.googleapis.com/auth/admin.directory.user.readonly` | admin | The radar's own class; Google's not confirmed; not restricted | [3], [12] |
| `https://www.googleapis.com/auth/admin.directory.group` | admin | The radar's own class; Google's not confirmed; not restricted | [3], [12] |

## Sources (all checked 2026-11-19)

1. Gmail API scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
2. Drive API scopes: https://developers.google.com/workspace/drive/api/guides/api-specific-auth
3. Google Cloud help, restricted scope verification (the list of restricted scopes): https://support.google.com/cloud/answer/13464325
4. Sheets API scopes: https://developers.google.com/workspace/sheets/api/scopes
5. Docs API scopes: https://developers.google.com/workspace/docs/api/auth
6. Slides API scopes: https://developers.google.com/workspace/slides/api/scopes
7. Sensitive scope verification: https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
8. OpenID Connect (the `scope` parameter for sign-in): https://developers.google.com/identity/openid-connect/openid-connect
9. Calendar API scopes (no class given): https://developers.google.com/workspace/calendar/api/auth
10. Tasks API scopes (no class given): https://developers.google.com/workspace/tasks/auth
11. People API, authorizing requests (no class given): https://developers.google.com/people/v1/how-tos/authorizing
12. Admin SDK Directory API, authorizing requests (no class given): https://developers.google.com/workspace/admin/directory/v1/guides/authorizing
13. OAuth 2.0 Scopes for Google APIs (every scope and what it allows; no class given): https://developers.google.com/identity/protocols/oauth2/scopes

Google changes these lists. Check them again before the classes are relied on outside the tuning world.
