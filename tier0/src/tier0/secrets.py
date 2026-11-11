"""The Anthropic API key comes from the repo's `.env` file and nowhere else (milestone 3).

The shell environment and any `ant auth login` profile are ignored on purpose, so a
paid call only ever happens with the key the user put in `.env` for this lab. The key
is returned to the caller and never logged, traced or written anywhere.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOTENV = REPO_ROOT / ".env"
KEY_NAME = "ANTHROPIC_API_KEY"


class MissingKeyError(RuntimeError):
    """No key in .env; the message never contains a key."""


def read_api_key(path: Path = DOTENV, name: str = KEY_NAME) -> str:
    """The value of `name` in a KEY=VALUE .env file (quotes and `export` allowed)."""
    if not path.is_file():
        raise MissingKeyError(f"no {path.name} file: put {name}=... in {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    raise MissingKeyError(f"{name} is not set in {path}")
