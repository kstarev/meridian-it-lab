"""Claude Code PreToolUse hook: block reads and searches of sealed files, and nothing else.

Reads one JSON object on stdin. Exit 0 allows. Exit 2 blocks, with a reason on stderr.

A sealed file is a `held_out_*` file whose set is not in OPENED (tools/sealed_paths.py).
The hook blocks only these (docs/sealed-search-guard.md lists them with examples):
  1. Read of a sealed file.
  2. Grep whose path is a sealed file, or whose glob names `held_out_`.
  3. Bash: a program that prints file content, with a sealed file among its arguments.
  4. Bash: `git show`, `git diff`, `git blame`, `git cat-file` or `git log -p` naming a sealed
     file.
  5. Bash: `git grep`, `grep -r` or `rg --no-ignore` whose paths cover a sealed file.

It never blocks a program that runs code (python, pytest, make, uv, gh, a shell script), so
the sealed runner, the tests and CI are not touched.

Turn it off: start Claude Code with MERIDIAN_SEALED_GUARD=off, or remove the hook from
.claude/settings.json.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys

from sealed_paths import OPENED, is_sealed

OFF_ENV = "MERIDIAN_SEALED_GUARD"
USE = "use `python tools/search.py PATTERN` (or `make search q=...`), which leaves sealed paths out"

# --- The rules, in one place -------------------------------------------------------------
# A sealed data file named inside a word (the set's name is group 1).
SEALED_FILE_RE = re.compile(r"(held_out_[A-Za-z0-9_]+?)\.(?:json\.gz|py\.gz|json)\b")
# Programs that print a file's content. The file must be one of the program's own arguments.
READERS = frozenset(
    {
        *("cat", "head", "tail", "less", "more", "bat", "nl", "tac", "cut", "sort", "uniq"),
        *("sed", "awk", "grep", "egrep", "fgrep", "rg", "ag", "jq", "diff", "cmp"),
        *("strings", "xxd", "hexdump", "od", "zcat", "gzcat", "gunzip"),
        *("open", "code", "vim", "vi", "nano"),
    }
)
# For these the first plain argument is a pattern or a script, not a file.
PATTERN_FIRST = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "jq"})
PATTERN_FLAGS = frozenset({"-e", "-f", "--regexp", "--file"})  # the next word is the pattern
GREPS = frozenset({"grep", "egrep", "fgrep"})
GIT_READERS = frozenset({"show", "diff", "blame", "cat-file"})
# Words that come before the real program and are skipped, with their own flags.
WRAPPERS = frozenset({"sudo", "command", "time", "nohup", "exec", "caffeinate"})
SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[|;&\n]")  # only when the quotes don't balance
SEPARATORS = frozenset("|;&\n")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RECURSIVE_RE = re.compile(r"-[A-Za-z]*[rR][A-Za-z]*|--recursive|--dereference-recursive")
RG_NO_IGNORE_RE = re.compile(r"--no-ignore\S*|-u+|--unrestricted")
GIT_EXCLUDE_RE = re.compile(r"^:(?:!|\^|\(exclude)")


def _sealed(path: str) -> bool:
    return is_sealed(path, OPENED, extra=False)


def _names_sealed(word: str, cwd: str) -> str | None:
    """The sealed file this word names, or None. Globs are expanded as the shell would."""
    for m in SEALED_FILE_RE.finditer(word):
        if m[1] not in OPENED:
            return m[0]
    path = word.split(":", 1)[-1].lstrip("<>")  # REV:path, and a redirect written as <path
    if not path:
        return None
    if _sealed(path):
        return path
    if any(ch in path for ch in "*?["):
        return next((h for h in glob.glob(os.path.join(cwd, path)) if _sealed(h)), None)
    return None


def _covers_sealed(paths: list[str], cwd: str) -> str | None:
    """A sealed file that git lists under these pathspecs, or None. It asks git, so the answer
    follows git's own pathspec rules, excludes included."""
    try:
        listed = subprocess.run(  # noqa: S603
            ["git", "-C", cwd, "ls-files", "--", *(paths or ["."])],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None  # not a repository, or a pathspec git rejects: nothing to guard
    return next((p for p in listed if _sealed(p)), None)


def _split(command: str) -> list[str]:
    """The command cut at each `|`, `||`, `&`, `&&`, `;` and newline that is not quoted or
    escaped. Raises ValueError when the quotes don't balance."""
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    chars = iter(command)
    for ch in chars:
        if ch == "\\" and quote != "'":
            nxt = next(chars, None)
            if nxt is None:
                raise ValueError("a trailing backslash")
            current += [ch, nxt]
        elif quote:
            current.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            current.append(ch)
            quote = ch
        elif ch in SEPARATORS:
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
    if quote:
        raise ValueError("unbalanced quotes")
    return [*segments, "".join(current)]


def _command(command: str, cwd: str) -> str | None:
    """The reason to block this shell command, or None."""
    try:
        segments = _split(command)
    except ValueError:  # can't parse it: split as before, ignoring quotes
        segments = SEGMENT_SPLIT_RE.split(command)
    return next((r for s in segments if (r := _segment(s, cwd))), None)


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and (ASSIGNMENT_RE.match(words[0]) or words[0] in WRAPPERS):
        words.pop(0)
        while words and words[0].startswith("-"):  # a wrapper's own flags
            words.pop(0)
    return words


def _git(words: list[str], cwd: str) -> str | None:
    args = words[1:]
    while args and args[0].startswith("-"):  # git -C dir, git -c key=value
        args = args[2:] if args[0] in ("-C", "-c") else args[1:]
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub in GIT_READERS or (sub == "log" and any(w in ("-p", "-u", "--patch") for w in rest)):
        named = next((s for w in rest if (s := _names_sealed(w, cwd))), None)
        return f"git {sub} would print a sealed file ({named})" if named else None
    if sub != "grep":
        return None
    if "--" in rest:
        paths = rest[rest.index("--") + 1 :]
    else:  # the plain words after the pattern that exist as paths; a revision is not one
        plain = [w for w in rest if not w.startswith("-")]
        paths = [w for w in plain[1:] if os.path.exists(os.path.join(cwd, w))]
    if paths and all(GIT_EXCLUDE_RE.match(p) for p in paths):
        paths = [".", *paths]
    hit = _covers_sealed(paths, cwd)
    return f"git grep would search a sealed file ({hit})" if hit else None


def _files(program: str, args: list[str]) -> list[str]:
    """The arguments that are files: not flags, and not the pattern or script."""
    plain: list[str] = []
    pattern_given = False
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word in PATTERN_FLAGS:
            pattern_given = skip = True
        elif not word.startswith("-"):
            plain.append(word)
    return plain[1:] if program in PATTERN_FIRST and not pattern_given else plain


def _segment(segment: str, cwd: str) -> str | None:
    words = _words(segment)
    if not words:
        return None
    # A quoted word may be a command of its own (`bash -c 'ls; cat x'`): judge it too.
    for word in words:
        if SEPARATORS & set(word) and (reason := _command(word, cwd)):
            return reason
    program = words[0].rsplit("/", 1)[-1]
    if program == "git":
        return _git(words, cwd)
    if program not in READERS:
        return None  # python, pytest, make, uv, gh, a script: never blocked
    files = _files(program, words[1:])
    named = next((s for w in files if (s := _names_sealed(w, cwd))), None)
    if named:
        return f"{program} would print a sealed file ({named})"
    flags = [w for w in words[1:] if w.startswith("-")]
    recursive = program in GREPS and any(RECURSIVE_RE.fullmatch(f) for f in flags)
    no_ignore = program == "rg" and any(RG_NO_IGNORE_RE.fullmatch(f) for f in flags)
    if recursive or no_ignore:
        hit = _covers_sealed(files, cwd)
        if hit:
            return f"{program} would search a sealed file ({hit})"
    return None


def decide(tool_name: str, tool_input: dict, cwd: str | None = None) -> str | None:
    """The reason to block this tool call, or None to allow it."""
    cwd = cwd or os.getcwd()
    reason: str | None = None
    if tool_name == "Read":
        path = str(tool_input.get("file_path", ""))
        if _sealed(path):
            reason = f"Read of a sealed file ({path})"
    elif tool_name == "Grep":
        path = str(tool_input.get("path") or "")
        if _sealed(path):
            reason = f"Grep on a sealed file ({path})"
        elif "held_out_" in str(tool_input.get("glob") or ""):
            reason = "Grep with a glob that names sealed files"
    elif tool_name == "Bash":
        reason = _command(str(tool_input.get("command", "")), cwd)
    return f"blocked by tools/sealed_guard.py: {reason}; {USE}" if reason else None


def main() -> int:
    if os.environ.get(OFF_ENV, "").lower() in ("off", "0", "false", "no"):
        return 0
    try:
        data = json.loads(sys.stdin.read())
        tool_name, tool_input = data["tool_name"], data.get("tool_input") or {}
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return 0
        cwd = data.get("cwd")
        reason = decide(tool_name, tool_input, cwd if isinstance(cwd, str) else None)
    except Exception:  # never break a session on a bad payload
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
