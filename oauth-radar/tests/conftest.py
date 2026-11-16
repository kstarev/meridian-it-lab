"""Paths to the hand-written fake fixtures. Milestone 0 has no generated world (OAR-003).

`google_tokens.json` holds 6 grants by 5 people to 5 made-up apps; `banned_apps.json` bans
one of them. Every user is @meridian.example and every client ID contains "-fake". The
milestone-0 rules find 4 grants in them, highest score first:

| # | App | User | Rules | Score |
|---|---|---|---|---|
| 1 | NoteSprout AI | sam.okafor | banned_app, broad_scope (full Drive) | 8 |
| 2 | NoteSprout AI | priya.nair | banned_app | 5 |
| 3 | Taskmole Agent | jordan.weber | broad_scope (all mail, every Drive file) | 3 |
| 4 | Wordloom Assistant | alex.kim | broad_scope (full Gmail) | 3 |

Sheetsmith Charts (only files it made, plus Sheets) and Calendar Kite (calendar events,
sign-in) give no finding.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "oauth-radar"
FIXTURES = PROJECT / "tests" / "fixtures"


@pytest.fixture
def tokens_path() -> Path:
    return FIXTURES / "google_tokens.json"


@pytest.fixture
def banned_path() -> Path:
    return FIXTURES / "banned_apps.json"
