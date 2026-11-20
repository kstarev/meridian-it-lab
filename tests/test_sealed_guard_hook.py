"""The Claude Code hook blocks reads and searches of sealed files, and nothing else
(docs/sealed-search-guard.md). It must never block the sealed runner, the tests or CI.

Never opens a real sealed file: the cases run against a throwaway repository, whose fake
sealed files hold dummy text written here.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import sealed_guard  # noqa: E402

FAKE = "pools/held_out_fake.json"
NAMES_PY = "dataset/src/meridian_dataset/names.py"
COMMIT = ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"]


def _tree(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("repo")
    _tree(
        root,
        {
            FAKE: '["sealed wording"]\n',
            "pools/tuning.json": '["tuning wording"]\n',
            "src/code.py": "x = 1\n",
            "docs/a.md": "held_out_fake.json is sealed\n",
            "tier0/src/tier0/evals/month.py": "y = 2\n",
            "old/kept.md": "kept\n",
            "old/held_out_gone.json": '["sealed wording"]\n',  # deleted by the second commit
            ".rgignore": "held_out_*\n",  # as in the repository: Grep on a folder relies on it
        },
    )
    (root / "links").mkdir()
    (root / "links" / "to_sealed.json").symlink_to("../pools/held_out_fake.json")
    (root / "linkdir").symlink_to("pools")
    (root / "src" / "loop").symlink_to(".")  # a symlink loop in a clean folder
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        COMMIT,
        ["rm", "-q", "old/held_out_gone.json"],
        COMMIT,
    ):
        subprocess.run(["git", *cmd], cwd=root, check=True)  # noqa: S603, S607
    return str(root)


def _bash(command: str) -> tuple[str, dict]:
    return ("Bash", {"command": command})


BLOCKED = [
    # 1. Read of a sealed file, of a symlink to one, or of one in another case (macOS)
    ("Read", {"file_path": FAKE}),
    ("Read", {"file_path": "/x/dataset/src/meridian_dataset/pools/held_out_v6.json"}),
    ("Read", {"file_path": "links/to_sealed.json"}),
    ("Read", {"file_path": "pools/HELD_OUT_FAKE.JSON"}),
    # 2. Grep on a sealed file, or with a glob that names sealed files
    ("Grep", {"pattern": "foo", "path": FAKE}),
    ("Grep", {"pattern": "foo", "glob": "held_out_*.json"}),
    # allowed before, blocked now: Grep with no path, in a folder that holds a sealed file
    ("Grep", {"pattern": "foo"}),
    # 3. a program that prints file content, given a sealed file
    _bash(f"cat {FAKE}"),
    _bash(f"head -5 {FAKE}"),
    _bash("zcat a/b/held_out_redteam1.json.gz | head"),
    _bash(f"jq . {FAKE}"),
    _bash(f"grep foo {FAKE}"),
    _bash(f"grep -e foo {FAKE}"),
    _bash(f"sed -n 1,3p {FAKE}"),
    _bash("cat pools/*.json"),
    _bash(f"cat < {FAKE}"),
    _bash(f"FOO=1 cat {FAKE}"),
    _bash(f"ls pools && cat {FAKE}"),
    # 4. git commands that print a named sealed file
    _bash(f"git show HEAD:{FAKE}"),
    _bash(f"git diff HEAD~1 -- {FAKE}"),
    _bash(f"git log -p -- {FAKE}"),
    _bash(f"git blame {FAKE}"),
    # 5. searches whose paths cover a sealed file
    _bash("git grep -n foo"),
    _bash("git grep foo -- pools"),
    _bash("git grep foo -- ."),
    _bash("git -C . grep foo"),
    _bash("grep -rn foo ."),
    _bash("grep -R foo"),
    _bash("grep -rn foo pools"),
    _bash("rg --no-ignore foo"),
    _bash("rg -uuu foo pools"),
    # allowed before the path rule (owner's ruling 3, 2026-11-19), blocked now: no path,
    # a path that doesn't resolve, and pathspec magic the hook doesn't resolve
    _bash("rg foo"),
    _bash("grep -rn foo /somewhere/outside/the/repo"),
    _bash("git grep -n foo -- ':!*held_out_*'"),
    _bash("git grep -n foo -- . ':(exclude,glob)**/held_out_*'"),
    # a real pipe or list after a quoted word is still a pipe or list
    _bash("cat dataset/src/meridian_dataset/month/pools/held_out_month2.json | head"),
    _bash('grep x "held_out_v3.json" | wc -l'),
    _bash('echo "a|b"; cat held_out_x.json'),
    _bash("echo 'a && b' && cat held_out_x.json"),
    _bash(f'git grep -E "foo|bar" -- docs && cat {FAKE}'),
    # a quoted command is judged as a command
    _bash(f"bash -c 'ls; cat {FAKE}'"),
    _bash(f'echo "$(ls; cat {FAKE})"'),
    # quotes that don't balance: split as before, ignoring quotes
    _bash(f'echo "a | cat {FAKE}'),
    _bash(f"cat {FAKE} 'x"),
]

ALLOWED = [
    # the sealed runner and its wrappers
    _bash(
        "uv run python -m tier0.evals.month run --set held_out_month2 "
        "--final-test=held_out_month2 --setup haiku-3d2 --k 3 --picker knowledge --out out/month"
    ),
    _bash("caffeinate -i python3 /x/sealed_month.py run haiku-3d2"),
    _bash("zsh /x/run_sealed_month.sh"),
    _bash("uv run --locked python -m meridian_access.redteam --unseal held_out_redteam1"),
    _bash("python -m tier0.evals.knowledge run --set held_out_k1 --final-test held_out_k1"),
    # a count-only check written inline: code is never blocked
    _bash(f"python3 -c \"import json; print(len(json.load(open('{FAKE}'))))\""),
    # the tests
    _bash("make test"),
    _bash("make lint"),
    _bash("uv run --locked pytest"),
    _bash("uv run pytest dataset/tests/test_held_out_v6_world.py"),
    _bash("pytest -k held_out -q"),
    # CI and git housekeeping
    _bash("gh run view 1 --log"),
    _bash("gh pr checks 79"),
    _bash("git push -u origin some-branch"),
    _bash(f"git add {FAKE}"),
    _bash("git commit -m 'seal held_out_month2.json'"),
    _bash("git status"),
    _bash(f"git log --oneline -- {FAKE}"),
    _bash("git diff --stat"),
    _bash("git ls-files pools"),
    # names, sizes, digests and copies
    _bash("ls pools"),
    _bash(f"shasum -a 256 {FAKE}"),
    _bash(f"wc -c {FAKE}"),
    _bash("cp /tmp/x/held_out_month2.json pools/"),
    _bash("git ls-files | grep held_out"),
    # searches that don't cover a sealed file
    _bash("python tools/search.py foo"),
    _bash("git grep foo -- src docs"),
    _bash("git grep foo HEAD -- src"),
    _bash("git grep -n foo src"),
    _bash("grep -rn foo src"),
    _bash("rg --no-ignore foo src"),
    _bash("grep held_out_fake.json docs/a.md"),
    _bash("grep -n held_out docs/a.md | head"),
    _bash("cat docs/a.md | grep held_out_fake.json"),
    _bash("cat pools/tuning.json"),
    # a quoted |, ; or && is not a pipe or list
    _bash('git grep -E "foo|bar" -- tier0/src/tier0/evals/month.py'),
    _bash("rg 'a|b' tier0/src"),
    _bash('git grep -n "a;b" -- src'),
    _bash("git grep -n 'a && b' -- src docs"),
    _bash("grep -rn 'x|y' src | head"),
    _bash(r"git grep -n foo\|bar -- src"),
    # a set the owner opened
    _bash("cat dataset/src/meridian_dataset/month/pools/held_out_month1.json"),
    ("Read", {"file_path": "dataset/src/meridian_dataset/month/pools/held_out_month1.json"}),
    # code that also holds blind wording: left out of searches, not blocked here
    ("Read", {"file_path": NAMES_PY}),
    # other reads, and other tools
    ("Read", {"file_path": "pools/tuning.json"}),
    ("Grep", {"pattern": "held_out_", "path": "src"}),
    ("Grep", {"pattern": "foo", "path": "src", "glob": "*.py"}),
    # Glob lists names only, never content
    ("Glob", {"pattern": "**/held_out_*"}),
    ("Edit", {"file_path": FAKE, "old_string": "a", "new_string": "b"}),
    ("SomeNewTool", {"file_path": FAKE}),
]


def _id(case: tuple[str, dict]) -> str:
    return f"{case[0]}:{json.dumps(case[1])[:70]}"


@pytest.mark.parametrize("case", BLOCKED, ids=_id)
def test_the_hook_blocks_reads_and_searches_of_sealed_files(case: tuple[str, dict], repo: str):
    reason = sealed_guard.decide(*case, cwd=repo)
    assert reason
    assert "tools/search.py" in reason
    assert "sealed wording" not in reason


@pytest.mark.parametrize("case", ALLOWED, ids=_id)
def test_the_hook_allows_everything_else(case: tuple[str, dict], repo: str):
    assert sealed_guard.decide(*case, cwd=repo) is None


def test_the_lists_are_long_enough_to_mean_something():
    assert len(BLOCKED) >= 25
    assert len(ALLOWED) >= 40


# --- Rule 5: a search limited to explicit paths (owner's ruling 3, 2026-11-19) --------------
# Allowed only if the hook resolves every path (globs, `..`, `~`, symlinks, a `cd`) and none is
# or holds a sealed file. Each blocked case also says why, so a block for the wrong reason
# fails. `{out}` is a folder outside the repository; `~` is a fake home folder.
SEALED = "a sealed file ("
UNSURE = "can't be checked"

PATH_LIMITED_ALLOWED = [
    "grep -n foo src/code.py",  # a plain file
    "grep -rn foo src",  # a folder with no sealed file (and a symlink loop in it)
    "grep -n foo src/*.py",  # a glob that matches only non-sealed files
    "grep -rn foo **/*.py",
    "grep -rn foo src/../docs",
    "grep -rn foo {out}/clean",  # outside the repository
    "grep -rn foo ~/clean",
    "cd && grep -rn foo clean",  # cd with no folder goes home
    "rg foo src docs",
    "rg -g '*.json' -n foo src",
    "rg --no-ignore -uu foo src",
    "git grep -n foo -- src docs",
    "git grep -n foo -- '*.py'",  # git's own wildcard, resolved by git
    "git grep -n -e foo --and -e bar -- src",
    "git -C src grep -n foo -- .",
    "git grep foo HEAD -- old",  # no sealed file under old/ in HEAD either
    "grep -rn --include='*.py' -A 2 foo src 2>/dev/null | head -5",
    "grep -rn foo src > out.txt 2>&1",
    "timeout 10 grep -rn foo src",
    "cd src && grep -rn foo .",
    "cd src && cd .. && grep -rn foo docs",
    "find src -name '*.py' -exec grep -n foo {} +",
    "ls pools | rg held_out",  # reads a pipe: no path needed
    "grep foo < docs/a.md",
    "bash -c 'grep -rn foo src'",
    "x=$(grep -rln foo src)",
    "git ls-files | xargs wc -l",
    # a here-document's lines are text, not commands
    "cat > notes.md <<'EOF'\ngrep -rn foo .\nrg foo\nEOF",
    "git commit -m \"$(cat <<'EOF'\nWhy `grep -rn foo .` is blocked\nEOF\n)\"",
]

PATH_LIMITED_BLOCKED = [
    # a path that is, or holds, a sealed file
    ("grep -n foo pools/*.json", SEALED),  # a glob that matches a sealed file
    ("grep -n foo p[^x]ols/*", SEALED),  # zsh's [^x] class (Python's fnmatch reads it as ^)
    ("grep -rn foo **/*.json", SEALED),
    ("grep -rn foo src/../pools", SEALED),  # .. into a folder that holds one
    ("grep -n foo links/to_sealed.json", SEALED),  # a symlink to a sealed file
    ("grep -rn foo pools", SEALED),  # a folder that holds one
    ("grep -rn foo links", SEALED),  # a folder that holds a symlink to one
    ("grep -rn foo linkdir", SEALED),  # a symlink to a folder that holds one
    ("grep -n foo pools/HELD_OUT_FAKE.JSON", SEALED),  # macOS ignores case
    ("grep -rn foo {out}/sealed", SEALED),
    ("grep -rn foo ~/sealed", SEALED),
    ("grep -rn foo ~/clean/../sealed", SEALED),
    ("grep -rn -f pools/held_out_fake.json src", SEALED),  # patterns from a sealed file
    ("rg -g '*.json' foo pools", SEALED),  # -g overrides .rgignore
    ("rg foo pools", SEALED),
    ("git grep foo -- pools", SEALED),
    ("git grep foo -- links", SEALED),  # a tracked symlink to a sealed file
    ("git grep foo -- '*.json'", SEALED),
    ("git grep foo HEAD~1 -- old", "HEAD~1:old/held_out_gone.json"),  # deleted since
    ("cd pools && grep -rn foo .", SEALED),
    ("cd src; grep -rn foo ../pools", SEALED),
    ("cd src; grep -rn foo .", SEALED),  # if the cd fails, `.` is the repository
    ("find pools -name '*.json' -exec grep -n foo {} +", SEALED),
    ("bash -c 'grep -rn foo pools'", SEALED),
    ("x=$(grep -rln foo pools)", SEALED),
    # no path: the whole folder
    ("grep -rn foo", UNSURE),
    ("grep --directories=recurse foo", UNSURE),
    ("rg foo", UNSURE),
    ("git grep foo", UNSURE),
    ("git grep foo --", UNSURE),
    ("git grep foo HEAD", UNSURE),
    ("find -exec grep foo {} +", UNSURE),
    # a path that doesn't resolve
    ("grep -rn foo nowhere", UNSURE),
    ("grep -n foo src/*.md", UNSURE),  # a glob that matches nothing
    ("cd - && grep -rn foo src", UNSURE),
    ("cd $DIR && grep -rn foo src", UNSURE),
    # what the hook can't parse
    ('grep -rn foo "src', UNSURE),
    ("grep -rn foo $(echo src)", UNSURE),
    ("grep -rn foo `echo src`", UNSURE),
    ("grep -rn foo $DIR", UNSURE),
    ("grep -rn foo {src,docs}", UNSURE),
    ("grep -rn foo <<EOF\nx\nEOF", UNSURE),
    ("git ls-files | xargs grep foo", "xargs would hand grep"),
    # options that widen the search, or that the hook doesn't know
    ("grep --frobnicate foo src", UNSURE),
    ("grep -rn --recur foo src", UNSURE),  # abbreviations are not read
    ("grep -z foo src/code.py", UNSURE),  # ugrep reads inside archives with -z
    ("zgrep foo src/code.py", UNSURE),
    ("rg --pre cat foo src", UNSURE),
    ("git grep --no-index foo -- src", UNSURE),
    ("git grep --untracked foo -- src", UNSURE),
    ("git grep -O foo -- src", UNSURE),
    ("git grep foo -- ':(glob)src/**'", UNSURE),
    ("git --git-dir=.git grep foo -- src", UNSURE),
    ("env -C pools grep -rn foo .", UNSURE),
    ("GIT_DIR=.git git grep foo -- src", "environment variable"),
]


@pytest.fixture
def places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A folder outside the repository and a fake home folder, each with a clean folder and
    a folder that holds a fake sealed file."""
    for top in ("out", "home"):
        _tree(
            tmp_path / top,
            {"clean/notes.md": "foo\n", "sealed/held_out_elsewhere.json": '["sealed wording"]\n'},
        )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return {"out": str(tmp_path / "out")}


@pytest.mark.parametrize("command", PATH_LIMITED_ALLOWED)
def test_a_search_limited_to_clean_paths_is_allowed(command: str, repo: str, places: dict):
    command = command.replace("{out}", places["out"])
    assert sealed_guard.decide("Bash", {"command": command}, cwd=repo) is None


@pytest.mark.parametrize(("command", "why"), PATH_LIMITED_BLOCKED)
def test_a_search_is_blocked_unless_every_path_resolves_clean(
    command: str, why: str, repo: str, places: dict
):
    command = command.replace("{out}", places["out"])
    reason = sealed_guard.decide("Bash", {"command": command}, cwd=repo)
    assert reason
    assert why in reason
    assert "tools/search.py" in reason
    assert "sealed wording" not in reason


def test_the_path_lists_cover_each_case_the_owner_named():
    assert len(PATH_LIMITED_ALLOWED) >= 25
    assert len(PATH_LIMITED_BLOCKED) >= 50
    assert sum(why == SEALED for _, why in PATH_LIMITED_BLOCKED) >= 20
    assert sum(why == UNSURE for _, why in PATH_LIMITED_BLOCKED) >= 25


@pytest.mark.parametrize(
    "call",
    [("Bash", {"command": "grep -rn foo src docs"}), ("Grep", {"pattern": "foo", "glob": "*.py"})],
)
def test_a_folder_too_big_to_check_is_blocked(monkeypatch, repo, call):
    monkeypatch.setattr(sealed_guard, "MAX_ENTRIES", 2)
    reason = sealed_guard.decide(*call, cwd=repo)
    assert reason
    assert "too many files" in reason


# --- Rule 2: the Grep tool -----------------------------------------------------------------
# Claude Code's Grep runs ripgrep. A `glob` overrides `.rgignore` as `rg -g` does, so with a
# glob or type, or with no path, the hook walks the path (the session's folder by default) and
# blocks if any file the glob or type may let in is sealed. A glob judges a file by its own
# name; a `type` counts as any file. Each blocked case also says why.
NAMED = "names sealed files"

GREP_TOOL_ALLOWED = [
    {"pattern": "foo", "glob": "*.py"},  # no path: the repository, but no sealed .py file
    {"pattern": "foo", "path": ".", "glob": "*.md"},
    {"pattern": "foo", "path": ".", "glob": "*.{py,md}"},
    {"pattern": "foo", "path": ".", "glob": "!*.md *.py"},  # a `!` glob only leaves files out
    {"pattern": "foo", "path": ".", "glob": "pools"},  # a folder's name lets in no file below
    {"pattern": "foo", "path": "pools", "glob": "*.md"},  # holds a sealed file the glob can't reach
    {"pattern": "foo", "path": "pools", "glob": "tuning.json"},
    {"pattern": "foo", "path": "pools/tuning.json", "glob": "*.json"},
    {"pattern": "foo", "path": "src", "glob": "*.json"},  # a clean folder, a symlink loop in it
    {"pattern": "foo", "path": "src", "type": "py"},
    # a path and neither glob nor type: as before, `.rgignore` leaves sealed files out
    {"pattern": "foo", "path": "pools"},
    {"pattern": "foo", "path": "."},
]

GREP_TOOL_BLOCKED = [
    # no path: the session's folder holds a sealed file
    ({"pattern": "foo"}, SEALED),
    ({"pattern": "foo", "glob": "*.json"}, SEALED),
    ({"pattern": "foo", "type": "py"}, SEALED),  # a type counts as any file
    # a glob that may reach a sealed file below the path
    ({"pattern": "foo", "path": ".", "glob": "*.json"}, SEALED),
    ({"pattern": "foo", "path": "pools", "glob": "*.json"}, SEALED),
    ({"pattern": "foo", "path": "pools", "glob": "*.{md,json}"}, SEALED),
    ({"pattern": "foo", "path": "pools", "glob": "*.md *.json"}, SEALED),
    ({"pattern": "foo", "path": "pools", "glob": "*.md,*.json"}, SEALED),
    ({"pattern": "foo", "path": "pools", "glob": "**"}, SEALED),
    ({"pattern": "foo", "path": ".", "glob": "pools/"}, SEALED),
    ({"pattern": "foo", "path": ".", "glob": "pools/*"}, SEALED),
    ({"pattern": "foo", "path": ".", "glob": "**/*.JSON"}, SEALED),  # case doesn't matter
    ({"pattern": "foo", "path": ".", "glob": "held_ou[t]_*"}, SEALED),
    ({"pattern": "foo", "path": ".", "glob": "held\\_out_*"}, SEALED),
    ({"pattern": "foo", "path": "pools", "glob": "!*.md"}, SEALED),  # only `!`: any file
    ({"pattern": "foo", "path": "links", "glob": "*.json"}, SEALED),  # a symlink to one
    ({"pattern": "foo", "path": "linkdir", "glob": "*.json"}, SEALED),  # a symlinked folder
    ({"pattern": "foo", "path": "pools", "type": "json"}, SEALED),
    # a sealed path, or a symlink to one (a symlink was allowed before)
    ({"pattern": "foo", "path": FAKE, "glob": "*.md"}, SEALED),
    ({"pattern": "foo", "path": "links/to_sealed.json"}, SEALED),
    ({"pattern": "foo", "path": "src", "glob": "held_out_*"}, NAMED),
    # what the hook can't check
    ({"pattern": "foo", "path": "nowhere", "glob": "*.py"}, UNSURE),
    ({"pattern": "foo", "path": "src", "glob": "{a,b}" * 11}, UNSURE),  # 2,048 globs
]


@pytest.mark.parametrize("tool_input", GREP_TOOL_ALLOWED, ids=json.dumps)
def test_grep_with_a_glob_that_cant_reach_a_sealed_file_is_allowed(tool_input: dict, repo: str):
    assert sealed_guard.decide("Grep", tool_input, cwd=repo) is None


@pytest.mark.parametrize(("tool_input", "why"), GREP_TOOL_BLOCKED, ids=json.dumps)
def test_grep_that_may_reach_a_sealed_file_is_blocked(tool_input: dict, why: str, repo: str):
    reason = sealed_guard.decide("Grep", tool_input, cwd=repo)
    assert reason
    assert why in reason
    assert "tools/search.py" in reason
    assert "sealed wording" not in reason


def test_grep_reads_an_opened_set_but_not_a_sealed_one_beside_it(tmp_path: Path):
    opened = sorted(sealed_guard.OPENED)[0]
    _tree(
        tmp_path,
        {
            f"opened/{opened}.json": '["dev wording"]\n',
            "opened/notes.md": "foo\n",
            f"mixed/{opened}.json": '["dev wording"]\n',
            "mixed/held_out_fake9.json": '["sealed wording"]\n',
        },
    )
    top = str(tmp_path)
    for tool_input in (
        {"pattern": "foo", "path": "opened", "glob": "*.json"},
        {"pattern": "foo", "path": "opened", "type": "json"},
        {"pattern": "foo", "path": f"opened/{opened}.json", "glob": "*.json"},
    ):
        assert sealed_guard.decide("Grep", tool_input, cwd=top) is None
    assert sealed_guard.decide("Grep", {"pattern": "foo"}, cwd=str(tmp_path / "opened")) is None
    for tool_input in (
        {"pattern": "foo", "path": "mixed", "glob": "*.json"},
        {"pattern": "foo", "path": "mixed", "type": "json"},
        {"pattern": "foo"},
    ):
        reason = sealed_guard.decide("Grep", tool_input, cwd=top)
        assert reason
        assert "held_out_fake9.json" in reason
        assert opened not in reason


def test_grep_through_the_hook_end_to_end(monkeypatch, capsys, repo):
    for tool_input, code in (
        ({"pattern": "foo", "path": "src", "glob": "*.py"}, 0),
        ({"pattern": "foo", "path": "pools", "glob": "*.json"}, 2),
    ):
        payload = {"tool_name": "Grep", "tool_input": tool_input, "cwd": repo}
        assert _run_main(monkeypatch, json.dumps(payload)) == code
    assert "pools/held_out_fake.json" in capsys.readouterr().err


def test_a_path_limited_search_goes_through_the_hook_end_to_end(monkeypatch, capsys, repo):
    for command, code in (("grep -rn foo src", 0), ("grep -rn foo pools", 2)):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": repo}
        assert _run_main(monkeypatch, json.dumps(payload)) == code
    assert "pools/held_out_fake.json" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command", ['echo "a|b', "echo 'a;b", "grep 'x src", 'git grep "foo|bar -- src', "ls \\"]
)
def test_quotes_that_dont_balance_never_crash_the_hook(command: str, repo: str):
    reason = sealed_guard.decide("Bash", {"command": command}, cwd=repo)
    assert reason is None or "tools/search.py" in reason


def _run_main(monkeypatch: pytest.MonkeyPatch, stdin: str) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return sealed_guard.main()


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '{"tool_name": 3}', "{}"])
def test_a_malformed_payload_never_blocks(monkeypatch, capsys, stdin):
    assert _run_main(monkeypatch, stdin) == 0
    out = capsys.readouterr()
    assert out.out == out.err == ""


def test_a_blocked_read_exits_2_with_a_reason(monkeypatch, capsys, repo):
    payload = {"tool_name": "Read", "tool_input": {"file_path": FAKE}, "cwd": repo}
    assert _run_main(monkeypatch, json.dumps(payload)) == 2
    assert "tools/search.py" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false"])
def test_the_switch_turns_the_hook_off(monkeypatch, capsys, repo, value):
    monkeypatch.setenv(sealed_guard.OFF_ENV, value)
    payload = {"tool_name": "Read", "tool_input": {"file_path": FAKE}, "cwd": repo}
    assert _run_main(monkeypatch, json.dumps(payload)) == 0
    assert capsys.readouterr().err == ""


def test_the_settings_wire_the_hook_for_reads_and_searches_only():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    (entry,) = settings["hooks"]["PreToolUse"]
    assert sorted(entry["matcher"].split("|")) == ["Bash", "Grep", "Read"]
    (hook,) = entry["hooks"]
    assert hook["type"] == "command"
    assert "tools/sealed_guard.py" in hook["command"]


# --- The review of 2026-11-20: shapes that printed a dummy sealed file and were allowed -------
# Each case below was allowed by the hook before this review and, run in a throwaway repository,
# printed the dummy sealed text. `{blob}` is the hash of the fake sealed file's blob.
REVIEW_BLOCKED = [
    # a reader's option swallowed the file: -e and -f are options of their own for most readers
    (f"cat -e {FAKE}", "cat would print"),
    (f"sort -f {FAKE}", "sort would print"),
    (f"tail -f {FAKE}", "tail would print"),
    (f"xxd -e {FAKE}", "xxd would print"),
    (f"diff -e {FAKE} src/code.py", "diff would print"),
    # a file read through an option: a script file, or `--opt=FILE`
    (f"jq -f {FAKE} -n", "jq would print"),
    (f"sed -f {FAKE} docs/a.md", "sed would print"),
    (f"awk -f {FAKE} docs/a.md", "awk would print"),
    (f"diff --from-file={FAKE} src/code.py", "diff would print"),
    (f"dd if={FAKE} status=none", "dd would print"),
    # a subshell hid the program
    (f"(cat {FAKE})", "cat would print"),
    (f"( cat {FAKE} )", "cat would print"),
    ("(cd pools && cat *.json)", "cat would print"),
    ("(grep -rn foo .)", UNSURE),
    # a command substitution inside double quotes runs too
    (f'echo "$(cat {FAKE})"', "cat would print"),
    ('echo "$(cat pools/*.json)"', "cat would print"),
    ('x="$(grep -rn foo pools)"', SEALED),
    ('echo "`cat pools/*.json`"', "cat would print"),
    # so does one in a here-document whose delimiter is not quoted
    (f"cat <<EOF\n$(cat {FAKE})\nEOF", "cat would print"),
    # `<` feeds a file to any program, or to none
    (f"echo $(<{FAKE})", "with <"),
    (f'echo "$(<{FAKE})"', "with <"),
    (f'while read -r l; do echo "$l"; done < {FAKE}', "with <"),
    (f"tr a a < {FAKE}", "with <"),
    (f"cat<{FAKE}", "with <"),
    (f"<{FAKE} cat", "with <"),
    (f"exec 3<{FAKE}; cat <&3", "with <"),
    (f"git stripspace < {FAKE}", "with <"),
    (f"python3 -c 'import sys; print(sys.stdin.read())' < {FAKE}", "with <"),
    # programs that print a file and were not in the list
    (f"base64 -i {FAKE}", "base64 would print"),
    (f"rev {FAKE}", "rev would print"),
    (f"paste {FAKE}", "paste would print"),
    (f"iconv -f utf-8 -t utf-8 {FAKE}", "iconv would print"),
    ("curl -s file:///x/pools/held_ou%74_fake.json", "curl would print"),
    (f"ex -sc '%p|q!' {FAKE}", "ex would print"),
    (f"zless {FAKE}", "zless would print"),
    (f"tar -cf - {FAKE}", "tar would print"),
    (f"gsed -n p {FAKE}", "gsed would print"),
    ("pcre2grep -r foo pools", UNSURE),
    ("mdfind -onlyin pools foo", UNSURE),
    # wrappers that run the program after them
    (f"xcrun cat {FAKE}", "cat would print"),
    (f"arch -arm64 cat {FAKE}", "cat would print"),
    (f"uv run cat {FAKE}", "cat would print"),
    ("uv run --no-project rg -uuu foo pools", SEALED),
    (f"script -q /dev/null cat {FAKE}", "cat would print"),
    (f"env -S 'cat {FAKE}'", "cat would print"),
    ("uv run --directory pools cat *.json", "a glob in a folder the hook can't follow"),
    # brace lists and zsh globs, expanded before the program runs
    (f"{{cat,{FAKE}}}", "cat would print"),
    ("{grep,-rn,foo,pools}", UNSURE),
    ("cat pools/held_ou{t,x}_fake.json", "cat would print"),
    ("cat pools/held_ou{s..u}_fake.json", "cat would print"),
    ("cat pools/(h)eld_out_fake.json", "cat would print"),
    ("cat pools/(held|x)_out_fake.json", "cat would print"),
    ("cat pools/*(.)", "cat would print"),
    # a program the hook can't name, given a sealed file
    (f"c=cat; $c {FAKE}", "can't name"),
    (f"$'\\x63at' {FAKE}", "cat would print"),
    # find -exec and xargs running a reader through a shell
    ("find pools -name '*.json' -exec sh -c 'cat \"$1\"' _ {} \\;", "find would hand cat"),
    ("git ls-files | xargs sh -c 'cat \"$@\"' _", "xargs would hand cat"),
    # a sealed file run as a script, and shells that read commands from stdin
    (f"source {FAKE}", "as a script"),
    (f". {FAKE}", "as a script"),
    (f"bash {FAKE}", "as a script"),
    (f"sh <<< 'cat {FAKE}'", "cat would print"),
    (f"bash <<'EOF'\ncat {FAKE}\nEOF", "cat would print"),
    ("bash <<'EOF'\ngrep -rn foo pools\nEOF", SEALED),
    (f"echo 'cat {FAKE}' | sh", "name a sealed file"),
    (f"f() {{ cat {FAKE}; }}; f", "cat would print"),
    # cd the hook can't follow, then a glob
    ("d=pools; cd $d && cat *.json", "a glob in a folder the hook can't follow"),
    ("pushd pools >/dev/null && cat *.json", "cat would print"),
    # git: more commands that print a named file
    (f"git annotate {FAKE}", "git annotate would print"),
    (f"git log -L1,1:{FAKE}", "git log would print"),
    (f"git merge-file -p {FAKE} {FAKE} {FAKE}", "git merge-file would print"),
    (f"git archive HEAD {FAKE}", "git archive would print"),
    # git: content past `.gitattributes`' -diff, with no file named
    ("git log -p --text", "past .gitattributes"),
    ("git show -a HEAD", "past .gitattributes"),
    ("git diff --text HEAD~1", "past .gitattributes"),
    ("git format-patch -1 --stdout --text", "past .gitattributes"),
    ("GIT_EXTERNAL_DIFF=cat git diff HEAD~1", "past .gitattributes"),
    ("git -c diff.external=cat diff HEAD~1", "past .gitattributes"),
    ("git --attr-source=HEAD~1 log -p", "past .gitattributes"),
    ("git archive HEAD", "git archive would print"),
    ("git difftool -y -x cat HEAD~1", "git difftool"),
    # git: objects the hook can't name
    ("git cat-file --batch-all-objects --batch", "--batch"),
    (f"echo HEAD:{FAKE} | git cat-file --batch", "--batch"),
    ("git cat-file -p {blob}", "a blob by its hash"),
    ("git cat-file blob {blob}", "a blob by its hash"),
    ("git show {blob}", "a blob by its hash"),
    # git: an alias set on the command line
    (f"git -c alias.peek='!cat' peek {FAKE}", "cat would print"),
    (f"git -c alias.peek=show peek HEAD:{FAKE}", "git show would print"),
    ("git -c alias.g=grep g -n foo", UNSURE),
    # an environment variable that adds options to ripgrep
    ("RIPGREP_CONFIG_PATH=/dev/null rg -n foo src", "environment variable"),
]

REVIEW_ALLOWED = [
    # commit messages and PR bodies in a quoted here-document, backticks and parens in them
    "git commit -m \"$(cat <<'EOF'\nTitle\n\nWith `code`, (parens) and don't\n1) item\n"
    '2) `grep -rn foo .` is blocked\nEOF\n)"',
    "gh pr create --title x --body \"$(cat <<'EOF'\n- `cat held_out_x.json` is blocked)\nEOF\n)\"",
    "cat <<EOF > x.md\n$(date)\nEOF",
    # readers on clean files, with the options that used to swallow a file
    "cat -e docs/a.md",
    "sort -f docs/a.md",
    "tail -f docs/a.md",
    "awk -F, -v n=2 '{print $n}' docs/a.md",
    "jq -e . docs/a.md",
    # subshells, substitutions and shells that run nothing sealed
    "(cd src && ls)",
    "(cd src && uv run pytest)",
    'echo "$(git rev-parse HEAD)"',
    'x="$(date)"; echo "$x"',
    "echo ls | sh",
    "bash -c 'make test'",
    "zsh -lc 'ls'",
    "source /dev/null && echo ok",
    "python3 - <<'EOF'\nprint(1)\nEOF",
    "find . -name '*.py' -exec sh -c 'echo \"$1\"' _ {} \\;",
    "case x in a) echo a;; *) echo b;; esac",
    "a=(1 2 3); echo $((1 + 2))",
    # wrappers around programs that run code
    "uv run --locked pytest -q tests/",
    "uv run --locked python -m tier0.evals.month run --set held_out_month2",
    "$HOME/.local/bin/uv run python -m tier0.evals.month run --set held_out_month2",
    "xcrun --find clang",
    "arch -arm64 uv run pytest",
    "pushd src >/dev/null && ls && popd",
    # git that prints no sealed line
    "git show HEAD",
    "git show --stat HEAD",
    "git show HEAD:docs/a.md",
    "git cat-file -p HEAD",
    "git cat-file -t {blob}",
    "git archive -o out.tar HEAD",  # a copy, like cp
    "git archive HEAD src",
    "git log -p -3",
    "git -c core.editor=true rebase --continue",
    "git -c alias.st=status st",
    # names, sizes and copies, as before
    "wc -c pools/held_out_fake.json",
    "cp pools/held_out_fake.json /tmp/",
    "curl -s 'https://api.github.com/repos/x/y?per_page=1'",
    "tar -czf backup.tgz src",
]


def _blob(repo: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "rev-parse", f"HEAD:{FAKE}"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.parametrize(("command", "why"), REVIEW_BLOCKED)
def test_the_review_bypasses_are_blocked(command: str, why: str, repo: str):
    command = command.replace("{blob}", _blob(repo))
    reason = sealed_guard.decide("Bash", {"command": command}, cwd=repo)
    assert reason
    assert why in reason
    assert "tools/search.py" in reason
    assert "sealed wording" not in reason
    assert "could not read" not in reason  # blocked by a rule, not by a crash


@pytest.mark.parametrize("command", REVIEW_ALLOWED)
def test_the_review_fixes_keep_everyday_commands(command: str, repo: str):
    command = command.replace("{blob}", _blob(repo))
    assert sealed_guard.decide("Bash", {"command": command}, cwd=repo) is None


def test_grep_on_a_folder_with_no_rgignore_above_it_is_walked(repo: str, places: dict):
    # Outside the repository no `.rgignore` leaves sealed files out, so the folder is walked.
    sealed = {"pattern": "foo", "path": f"{places['out']}/sealed"}
    reason = sealed_guard.decide("Grep", sealed, cwd=repo)
    assert reason
    assert "held_out_elsewhere.json" in reason
    clean = {"pattern": "foo", "path": f"{places['out']}/clean"}
    assert sealed_guard.decide("Grep", clean, cwd=repo) is None
    # Inside it, `.rgignore` still covers a folder path given with no glob or type.
    assert sealed_guard.decide("Grep", {"pattern": "foo", "path": "pools"}, cwd=repo) is None
