"""The Claude Code hook blocks reads and searches of sealed files, and nothing else
(docs/sealed-search-guard.md). It must never block the sealed runner, the tests or CI.

Never opens a real sealed file: the cases run against a throwaway repository.
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


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("repo")
    for path, text in (
        (FAKE, '["sealed wording"]\n'),
        ("pools/tuning.json", '["tuning wording"]\n'),
        ("src/code.py", "x = 1\n"),
        ("docs/a.md", "held_out_fake.json is sealed\n"),
    ):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"],
    ):
        subprocess.run(["git", *cmd], cwd=root, check=True)  # noqa: S603, S607
    return str(root)


def _bash(command: str) -> tuple[str, dict]:
    return ("Bash", {"command": command})


BLOCKED = [
    # 1. Read of a sealed file
    ("Read", {"file_path": FAKE}),
    ("Read", {"file_path": "/x/dataset/src/meridian_dataset/pools/held_out_v6.json"}),
    # 2. Grep on a sealed file, or with a glob that names sealed files
    ("Grep", {"pattern": "foo", "path": FAKE}),
    ("Grep", {"pattern": "foo", "glob": "held_out_*.json"}),
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
    # a real pipe or list after a quoted word is still a pipe or list
    _bash("cat dataset/src/meridian_dataset/knowledge/pools/held_out_k1.json | head"),
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
    _bash("git grep -n foo -- ':!*held_out_*'"),
    _bash("git grep -n foo -- . ':(exclude,glob)**/held_out_*'"),
    _bash("git grep foo -- src docs"),
    _bash("git grep foo HEAD -- src"),
    _bash("git grep -n foo src"),
    _bash("grep -rn foo src"),
    _bash("grep -rn foo /somewhere/outside/the/repo"),
    _bash("rg foo"),
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
    ("Read", {"file_path": "dataset/src/meridian_dataset/month/pools/held_out_month2.json"}),
    ("Read", {"file_path": "dataset/src/meridian_dataset/month/pools/held_out_month2b.json"}),
    # code that also holds blind wording: left out of searches, not blocked here
    ("Read", {"file_path": NAMES_PY}),
    # other reads, and other tools
    ("Read", {"file_path": "pools/tuning.json"}),
    ("Grep", {"pattern": "held_out_", "path": "src"}),
    ("Grep", {"pattern": "foo"}),
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
