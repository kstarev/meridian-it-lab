"""Mock of GitHub for the memory repo, meridian-memory (ADR-010, ADR-035). In memory, no network.

It keeps the rules that make a guide's approval a person's act:

- **main is protected.** Nobody pushes to it. It changes only when a pull request is merged.
- **CODEOWNERS is the IT team** (`* @meridian/it-team`). A merge needs an approving review from
  a code owner, no code owner still asking for changes, and the person who merges must be a
  code owner too.
- **No self-approval.** GitHub never lets a pull request's author approve it.
- **A bot can't approve or merge.** Only a person can: the learning loop's GitHub App, which
  opens the pull requests, is refused both.
- **Only the App's pull requests can be merged.** A required check fails any other (ADR-010).
- **A draft can't approve itself.** A required check fails a pull request whose guide file
  already says `approved: true`.
- **The merge is the approval.** Merging writes `approved: true`, who approved it and the date
  into each guide file the pull request changes. Until then a drafted guide says
  `approved: false`, and code never posts it (`tier0.knowledge.answer.postable`).

Every call is logged with the identity that made it, so tests can check what the job did.
The state can be saved to a JSON file, so the drafting command can be run twice against the
same mock repo.
"""

import fnmatch
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tier0.clock import Clock, format_time

MAIN = "main"
CODEOWNERS_PATH = ".github/CODEOWNERS"
GUIDE_DIR = "runbooks/"
APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
COMMENT = "COMMENT"
REVIEW_EVENTS = (APPROVE, REQUEST_CHANGES, COMMENT)
OPEN = "open"
CLOSED = "closed"
MERGED = "merged"

# Why a call was refused.
MAIN_IS_PROTECTED = "main_is_protected"
NO_SUCH_BRANCH = "no_such_branch"
NO_SUCH_PULL = "no_such_pull"
PULL_EXISTS = "pull_exists"
NOT_OPEN = "not_open"
AUTHOR_CANNOT_APPROVE = "author_cannot_approve"
BOT_CANNOT_APPROVE = "bot_cannot_approve"
BOT_CANNOT_MERGE = "bot_cannot_merge"
NOT_A_CODE_OWNER = "not_a_code_owner"
NO_CODE_OWNER_APPROVAL = "no_code_owner_approval"
CHANGES_REQUESTED = "changes_requested"
NOT_OPENED_BY_APP = "not_opened_by_app"
DRAFT_CLAIMS_APPROVAL = "draft_claims_approval"
UNKNOWN_EVENT = "unknown_event"
BRANCH_HAS_PULL = "branch_has_pull"


class GitHubRefused(Exception):
    """GitHub said no. `code` says why, in one word."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class PullRequest:
    number: int
    branch: str
    title: str
    body: str
    author: str
    created_at: str
    files: dict[str, str]  # path -> content, the files this pull request changes
    requested_reviewers: tuple[str, ...]
    state: str = OPEN
    reviews: list[tuple[str, str]] = field(default_factory=list)  # (login, event)
    merged_by: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "branch": self.branch,
            "title": self.title,
            "body": self.body,
            "author": self.author,
            "state": self.state,
            "files": dict(self.files),
            "requested_reviewers": list(self.requested_reviewers),
            "reviews": [list(r) for r in self.reviews],
            "merged_by": self.merged_by,
        }


@dataclass(frozen=True, slots=True)
class Call:
    actor: str
    method: str
    target: str


class MemoryRepo:
    """The memory repo's state: main, the bot's branches, pull requests and the IT team."""

    def __init__(
        self,
        *,
        files: Mapping[str, str],
        teams: Mapping[str, tuple[str, ...]],
        app: str,
        clock: Clock,
    ) -> None:
        self.main: dict[str, str] = dict(files)
        self.branches: dict[str, dict[str, str]] = {}
        self.pulls: list[PullRequest] = []
        self.teams = {name: tuple(members) for name, members in teams.items()}
        self.app = app
        self.clock = clock
        self.calls: list[Call] = []

    def session(self, actor: str) -> "GitHubSession":
        """A client that acts as `actor`, like a token for that identity."""
        return GitHubSession(self, actor)

    # ------------------------------------------------------------------ rules

    def is_bot(self, login: str) -> bool:
        return login == self.app or login.endswith("[bot]")

    def code_owners(self, path: str) -> tuple[str, ...]:
        """People who own `path` by main's CODEOWNERS; the last matching line wins."""
        owners: tuple[str, ...] = ()
        for line in self.main.get(CODEOWNERS_PATH, "").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            pattern, *names = line.split()
            if _matches(pattern, path):
                owners = tuple(m for n in names for m in self._members(n))
        return owners

    def _members(self, name: str) -> tuple[str, ...]:
        name = name.removeprefix("@")
        if "/" in name:
            return self.teams.get(name, ())
        return (name,)

    def owners_of(self, pull: PullRequest) -> set[str]:
        """Code owners who may approve every file the pull request changes."""
        sets = [set(self.code_owners(path)) for path in pull.files]
        return set.intersection(*sets) if sets else set()

    def pull(self, number: int) -> PullRequest:
        for p in self.pulls:
            if p.number == number:
                return p
        raise GitHubRefused(NO_SUCH_PULL)

    # ------------------------------------------------------------- persistence

    def to_json(self) -> str:
        return json.dumps(
            {
                "main": self.main,
                "branches": self.branches,
                "pulls": [
                    {**asdict(p), "requested_reviewers": list(p.requested_reviewers)}
                    for p in self.pulls
                ],
                "teams": {k: list(v) for k, v in self.teams.items()},
                "app": self.app,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str, clock: Clock) -> "MemoryRepo":
        raw = json.loads(text)
        repo = cls(
            files=raw["main"],
            teams={k: tuple(v) for k, v in raw["teams"].items()},
            app=raw["app"],
            clock=clock,
        )
        repo.branches = {k: dict(v) for k, v in raw["branches"].items()}
        for p in raw["pulls"]:
            repo.pulls.append(
                PullRequest(
                    **{
                        **p,
                        "requested_reviewers": tuple(p["requested_reviewers"]),
                        "reviews": [tuple(r) for r in p["reviews"]],
                    }
                )
            )
        return repo

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path, clock: Clock) -> "MemoryRepo":
        return cls.from_json(path.read_text(encoding="utf-8"), clock)


def _matches(pattern: str, path: str) -> bool:
    if pattern == "*":
        return True
    pattern = pattern.removeprefix("/")
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatch(path, pattern)


class GitHubSession:
    """The GitHub API as one identity sees it. Every call is logged with that identity."""

    def __init__(self, repo: MemoryRepo, actor: str) -> None:
        self._repo = repo
        self.actor = actor

    def _log(self, method: str, target: str | int) -> None:
        self._repo.calls.append(Call(self.actor, method, str(target)))

    # ------------------------------------------------------------------ reads

    def file(self, path: str, ref: str = MAIN) -> str | None:
        self._log("get_file", f"{ref}:{path}")
        if ref == MAIN:
            return self._repo.main.get(path)
        branch = self._repo.branches.get(ref)
        if branch is None:
            raise GitHubRefused(NO_SUCH_BRANCH)
        return branch.get(path, self._repo.main.get(path))

    def files(self, prefix: str, ref: str = MAIN) -> dict[str, str]:
        self._log("list_files", f"{ref}:{prefix}")
        if ref == MAIN:
            tree = dict(self._repo.main)
        else:
            if ref not in self._repo.branches:
                raise GitHubRefused(NO_SUCH_BRANCH)
            tree = {**self._repo.main, **self._repo.branches[ref]}
        return {p: c for p, c in sorted(tree.items()) if p.startswith(prefix)}

    def pulls(self) -> list[dict[str, Any]]:
        """Every pull request, open, closed or merged, oldest first."""
        self._log("list_pulls", "all")
        return [p.view() for p in self._repo.pulls]

    # ----------------------------------------------------------------- writes

    def push_branch(self, branch: str, files: Mapping[str, str], message: str) -> None:
        """One commit on top of main with `files`, force-pushed to `branch`. main is never a
        target, and a branch that has an open or merged pull request is not overwritten."""
        self._log("push_branch", branch)
        if branch == MAIN:
            raise GitHubRefused(MAIN_IS_PROTECTED)
        if any(p.branch == branch and p.state != CLOSED for p in self._repo.pulls):
            raise GitHubRefused(BRANCH_HAS_PULL)
        self._repo.branches[branch] = dict(files)

    def push_main(self, files: Mapping[str, str], message: str) -> None:
        """Always refused: the ruleset requires a pull request (ADR-010)."""
        self._log("push_main", MAIN)
        raise GitHubRefused(MAIN_IS_PROTECTED)

    def open_pull(self, branch: str, title: str, body: str) -> dict[str, Any]:
        self._log("open_pull", branch)
        if branch not in self._repo.branches:
            raise GitHubRefused(NO_SUCH_BRANCH)
        if any(p.branch == branch and p.state == OPEN for p in self._repo.pulls):
            raise GitHubRefused(PULL_EXISTS)
        files = dict(self._repo.branches[branch])
        owners = sorted({o for path in files for o in self._repo.code_owners(path)})
        pull = PullRequest(
            number=len(self._repo.pulls) + 1,
            branch=branch,
            title=title,
            body=body,
            author=self.actor,
            created_at=format_time(self._repo.clock.now()),
            files=files,
            requested_reviewers=tuple(o for o in owners if o != self.actor),
        )
        self._repo.pulls.append(pull)
        return pull.view()

    def review(self, number: int, event: str) -> None:
        """Approve, request changes or comment. Only a code owner who is a person and not the
        author may review."""
        self._log("review", number)
        pull = self._repo.pull(number)
        if event not in REVIEW_EVENTS:
            raise GitHubRefused(UNKNOWN_EVENT)
        if pull.state != OPEN:
            raise GitHubRefused(NOT_OPEN)
        if self.actor == pull.author:
            raise GitHubRefused(AUTHOR_CANNOT_APPROVE)
        if self._repo.is_bot(self.actor):
            raise GitHubRefused(BOT_CANNOT_APPROVE)
        if self.actor not in self._repo.owners_of(pull):
            raise GitHubRefused(NOT_A_CODE_OWNER)
        pull.reviews.append((self.actor, event))

    def merge(self, number: int) -> None:
        """Merge, which approves every guide the pull request changes."""
        self._log("merge", number)
        repo = self._repo
        pull = repo.pull(number)
        if pull.state != OPEN:
            raise GitHubRefused(NOT_OPEN)
        if repo.is_bot(self.actor):
            raise GitHubRefused(BOT_CANNOT_MERGE)
        owners = repo.owners_of(pull)
        if self.actor not in owners:
            raise GitHubRefused(NOT_A_CODE_OWNER)
        if pull.author != repo.app:
            raise GitHubRefused(NOT_OPENED_BY_APP)
        if any(_claims_approval(path, text) for path, text in pull.files.items()):
            raise GitHubRefused(DRAFT_CLAIMS_APPROVAL)
        latest: dict[str, str] = {}
        for login, event in pull.reviews:
            if event != COMMENT:
                latest[login] = event
        if not any(e == APPROVE and r in owners and r != pull.author for r, e in latest.items()):
            raise GitHubRefused(NO_CODE_OWNER_APPROVAL)
        if any(e == REQUEST_CHANGES and r in owners for r, e in latest.items()):
            raise GitHubRefused(CHANGES_REQUESTED)
        today = format_time(repo.clock.now())[:10]
        for path, text in pull.files.items():
            repo.main[path] = _approved(path, text, self.actor, today)
        pull.state = MERGED
        pull.merged_by = self.actor
        repo.branches.pop(pull.branch, None)

    def close(self, number: int) -> None:
        self._log("close", number)
        pull = self._repo.pull(number)
        if pull.state != OPEN:
            raise GitHubRefused(NOT_OPEN)
        pull.state = CLOSED
        self._repo.branches.pop(pull.branch, None)


def _is_guide(path: str) -> bool:
    return path.startswith(GUIDE_DIR) and path.endswith(".json")


def _claims_approval(path: str, text: str) -> bool:
    if not _is_guide(path):
        return False
    try:
        return json.loads(text).get("approved") is not False
    except (json.JSONDecodeError, AttributeError):
        return True


def _approved(path: str, text: str, reviewer: str, today: str) -> str:
    """The file as main gets it on merge: a guide is flipped to approved by the reviewer."""
    if not _is_guide(path):
        return text
    record = json.loads(text)
    record.update(approved=True, approved_by=reviewer, last_reviewed=today)
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"
