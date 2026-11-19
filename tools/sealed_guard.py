"""Claude Code PreToolUse hook: block reads and searches of sealed files, and nothing else.

Reads one JSON object on stdin. Exit 0 allows. Exit 2 blocks, with a reason on stderr.

A sealed file is a `held_out_*` file whose set is not in OPENED (tools/sealed_paths.py).
The hook blocks only these (docs/sealed-search-guard.md lists them with examples):
  1. Read of a sealed file, or of a symlink to one.
  2. Grep whose path is a sealed file, or whose glob names `held_out_`.
  3. Bash: a program that prints file content, with a sealed file among its arguments.
  4. Bash: `git show`, `git diff`, `git blame`, `git cat-file` or `git log -p` naming a sealed
     file.
  5. Bash: a search (`grep`, `rg`, `git grep`, `find -exec grep`, ...) unless it names
     explicit paths, the hook resolves every one (globs, `~`, `..`, symlinks, the folder after
     a `cd`), and none is or holds a sealed file. When the hook can't be sure, it blocks.

It never blocks a program that runs code (python, pytest, make, uv, gh, a shell script), so
the sealed runner, the tests and CI are not touched.

Turn it off: start Claude Code with MERIDIAN_SEALED_GUARD=off, or remove the hook from
.claude/settings.json.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field

from sealed_paths import OPENED, is_sealed

OFF_ENV = "MERIDIAN_SEALED_GUARD"
USE = "use `python tools/search.py PATTERN` (or `make search q=...`), which leaves sealed paths out"

# --- The rules, in one place -------------------------------------------------------------
# A sealed data file named inside a word (the set's name is group 1).
SEALED_FILE_RE = re.compile(r"(held_out_[A-Za-z0-9_]+?)\.(?:json\.gz|py\.gz|json)\b")
# Programs that search file content. Rule 5 checks every path they are given.
GREPS = frozenset(
    {"grep", "egrep", "fgrep", "ggrep", "rgrep", "zgrep", "zegrep", "zfgrep", "bzgrep", "xzgrep"}
)
OTHER_SEARCHES = frozenset({"ag", "ack", "ugrep", "ug"})  # options not modelled: any blocks
SEARCHES = GREPS | {"rg"} | OTHER_SEARCHES
# Programs that print a file's content. The file must be one of the program's own arguments.
READERS = frozenset(
    {
        *("cat", "head", "tail", "less", "more", "bat", "nl", "tac", "cut", "sort", "uniq"),
        *("sed", "awk", "jq", "diff", "cmp", "strings", "xxd", "hexdump", "od"),
        *("zcat", "gzcat", "gunzip", "open", "code", "vim", "vi", "nano"),
        *SEARCHES,
    }
)
# For these the first plain argument is a pattern or a script, not a file.
PATTERN_FIRST = frozenset({"sed", "awk", "jq", *SEARCHES})
PATTERN_FLAGS = frozenset({"-e", "-f", "--regexp", "--file"})  # the next word is the pattern
GIT_READERS = frozenset({"show", "diff", "blame", "cat-file"})
GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
)
# Words before the real program: each wrapper's options that take a value, and how many plain
# words it takes before the program (timeout's duration).
WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "sudo": (frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"}), 0),
    "doas": (frozenset({"-u", "-C"}), 0),
    "env": (frozenset({"-u", "-C", "-S", "-P"}), 0),
    "nice": (frozenset({"-n"}), 0),
    "ionice": (frozenset({"-c", "-n", "-p"}), 0),
    "timeout": (frozenset({"-s", "-k"}), 1),
    "exec": (frozenset({"-a"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e"}), 0),
    **{
        w: (frozenset(), 0)
        for w in ("command", "builtin", "time", "nohup", "caffeinate", "noglob", "nocorrect")
    },
}
CHDIR_FLAGS = frozenset({"-C", "-D", "--chdir"})  # a wrapper option that changes folder
KEYWORDS = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!", "{"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
CD = frozenset({"cd", "pushd", "popd"})
FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[|;&\n]")  # only when the quotes don't balance
SEPARATORS = frozenset("|;&\n")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# An environment variable that changes what grep or git grep reads.
RISKY_ENV_RE = re.compile(
    r"\b(?:GREP_OPTIONS|GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE|GIT_COMMON_DIR|GIT_NAMESPACE"
    r"|GIT_CONFIG\w*|GIT_\w*PATHSPECS)\b"
)
SEARCH_WORD_RE = re.compile(rf"\b(?:{'|'.join(sorted(SEARCHES))})\b")
GLOB_CHARS = frozenset("*?[")
EXPANDS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789{(@*#?$!-")
MAX_ENTRIES = 300_000  # files and folders looked at per tool call, at most
MAX_SECONDS = 5.0
MAX_MATCHES = 20_000  # paths one glob may expand to

# Search options, by program. Short options: flags, and those that take a value. Long options:
# False takes no value, True takes one, None may take one after `=`, BLOCK is never allowed.
# Anything not listed blocks. `grep` here may be GNU grep, BSD grep or ugrep (macOS); an option
# whose meaning differs between them (-J -M -O -Q -z) is left out, so it blocks.
BLOCK = "block"


def _kinds(kind: bool | str | None, names: str) -> dict[str, bool | str | None]:
    return dict.fromkeys(names.split(), kind)


GREP_FLAGS = frozenset("abcEFGhHiIjklLnopPqrRsSTuUvVwWxXyYZ0123456789")
GREP_VALUES = frozenset("ABCDdefgKmNt")
GREP_LONG: dict[str, bool | str | None] = {
    **_kinds(
        False,
        (
            "extended-regexp fixed-strings basic-regexp perl-regexp ignore-case no-ignore-case "
            "word-regexp line-regexp no-messages invert-match version help byte-offset "
            "line-number line-buffered with-filename no-filename only-matching quiet silent text "
            "recursive dereference-recursive files-without-match files-with-matches count "
            "initial-tab null null-data no-group-separator"
        ),
    ),
    **_kinds(
        True,
        (
            "regexp file max-count label binary-files directories devices include exclude "
            "exclude-from exclude-dir include-dir after-context before-context context "
            "group-separator"
        ),
    ),
    "color": None,
    "colour": None,
}
RG_FLAGS = frozenset(".0FHILNPSUVabchilnopqsuvwxz")
RG_VALUES = frozenset("ABCEMTdefgjmrt")
RG_LONG: dict[str, bool | str | None] = {
    **_kinds(
        False,
        (
            "auto-hybrid-regex binary block-buffered byte-offset case-sensitive column count "
            "count-matches crlf debug files files-with-matches files-without-match fixed-strings "
            "follow glob-case-insensitive heading help hidden ignore-case "
            "ignore-file-case-insensitive include-zero invert-match json line-buffered "
            "line-number line-regexp max-columns-preview mmap multiline multiline-dotall "
            "no-config no-filename no-ignore no-ignore-dot no-ignore-exclude no-ignore-files "
            "no-ignore-global no-ignore-messages no-ignore-parent no-ignore-vcs no-line-number "
            "no-messages no-pcre2-unicode no-require-git no-unicode null null-data "
            "one-file-system only-matching passthru pcre2 pcre2-version pretty quiet search-zip "
            "smart-case sort-files stats stop-on-nonmatch text trace trim type-list unrestricted "
            "version vimgrep with-filename word-regexp"
        ),
    ),
    **_kinds(
        True,
        (
            "after-context before-context color colors context context-separator dfa-size-limit "
            "encoding engine field-context-separator field-match-separator file generate glob "
            "hyperlink-format iglob ignore-file max-columns max-count max-depth max-filesize "
            "path-separator regex-size-limit regexp replace sort sortr threads type type-add "
            "type-clear type-not"
        ),
    ),
    **_kinds(BLOCK, "pre pre-glob hostname-bin"),  # they run a program
}
GIT_GREP_FLAGS = frozenset("viwaIrEGFPnhHlLzocpWq0123456789")
GIT_GREP_VALUES = frozenset("ABCmfe")
GIT_GREP_LONG: dict[str, bool | str | None] = {
    **_kinds(
        False,
        (
            "cached exclude-standard invert-match ignore-case word-regexp text textconv "
            "no-textconv recursive no-recursive extended-regexp basic-regexp fixed-strings "
            "perl-regexp line-number no-line-number column full-name files-with-matches name-only "
            "files-without-match null only-matching count no-color break heading show-function "
            "function-context and or not all-match quiet ( )"
        ),
    ),
    **_kinds(True, "max-depth context before-context after-context threads max-count"),
    "color": None,
    # they reach past tracked files or the explicit paths, or open a pager
    **_kinds(
        BLOCK,
        "no-index untracked no-exclude-standard recurse-submodules open-files-in-pager ext-grep",
    ),
}
GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-p", "--paginate", "-P", "--no-optional-locks"})
GIT_CONFIG_KEYS = ("color.", "grep.", "pager.", "core.pager", "core.quotepath")


class Unsure(Exception):
    """The hook can't be sure what a search reads, so the search is blocked."""


@dataclass(frozen=True)
class Word:
    """One shell word, after quotes are removed."""

    text: str
    pattern: str = ""  # with quoted glob characters escaped; set when `glob`
    glob: bool = False  # holds an unquoted * ? or [
    tilde: bool = False  # starts with an unquoted ~


@dataclass
class Spec:
    """What a search reads: its paths, the files it reads patterns from, and its pattern."""

    paths: list[Word]
    reads: list[Word] = field(default_factory=list)
    recursive: bool = False
    pattern: Word | None = None


_budget = {"left": MAX_ENTRIES, "deadline": float("inf")}


def _tick() -> None:
    _budget["left"] -= 1
    if _budget["left"] < 0 or (
        _budget["left"] % 512 == 0 and time.monotonic() > _budget["deadline"]
    ):
        raise Unsure("too many files to check")


def _sealed(path: str) -> bool:
    """A sealed file's name. The case doesn't matter: macOS opens HELD_OUT_X.JSON too."""
    return is_sealed(path, OPENED, extra=False) or is_sealed(path.lower(), OPENED, extra=False)


def _program(word: str) -> str:
    return word.rsplit("/", 1)[-1].lstrip("=\\")


def _union(*groups: list[str | None]) -> list[str | None]:
    return list(dict.fromkeys(c for group in groups for c in group))


# --- Paths -------------------------------------------------------------------------------
def _loosen(pattern: str) -> str:
    """Each [...] class becomes `?`, any one character, so no shell matches more."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "[":
            j = i + 1
            if pattern[j : j + 1] in ("!", "^"):
                j += 1
            if pattern[j : j + 1] == "]":
                j += 1
            end = pattern.find("]", j)
            if end >= 0 and "/" not in pattern[i:end]:
                out.append("?")
                i = end + 1
                continue
            out.append("[[]")
        else:
            out.append(pattern[i])
        i += 1
    return "".join(out)


def _dirs_below(top: str) -> list[str]:
    """Every folder below `top`, not following symlinks, as `**` does in zsh and bash."""
    found: list[str] = []
    stack = [top]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                listed = list(entries)
        except OSError:
            continue
        for entry in listed:
            _tick()
            if entry.is_dir(follow_symlinks=False):
                found.append(entry.path)
                stack.append(entry.path)
    return found


def _expand(pattern: str, base: str) -> list[str]:
    """The paths a shell glob may expand to, from `base`, and a few more: no shell matches a
    path this leaves out. Classes match any character, case and dot files don't matter, and
    `**` stands for every folder below."""
    pattern = _loosen(pattern)
    if pattern.startswith("/"):
        base = "/"
    current = [base]
    for part in (p for p in pattern.split("/") if p):
        if "**" in part:
            if part.strip("*"):  # `a**b` is no recursive glob; read it as `*`
                part = re.sub(r"\*+", "*", part)
            else:
                current = [p for d in current for p in (d, *_dirs_below(d))]
                continue
        if GLOB_CHARS & set(part):
            match = re.compile(fnmatch.translate(part), re.IGNORECASE).match
            found = []
            for folder in current:
                try:
                    names = os.listdir(folder)
                except OSError:
                    continue
                found += [os.path.join(folder, n) for n in names if match(n)]
            current = found
        else:
            current = [os.path.join(folder, part) for folder in current]
        if len(current) > MAX_MATCHES:
            raise Unsure(f"{pattern} matches too many paths")
    return [p for p in current if os.path.lexists(p)]


def _holds_sealed(path: str) -> str | None:
    """`path` if it is a sealed file or a symlink to one; else a sealed file, or a symlink to
    one, anywhere below it, following every symlink; else None."""
    if _sealed(path):
        return path
    real = os.path.realpath(path)
    if _sealed(real):
        return f"{path} -> {real}"
    if not os.path.isdir(real):
        return None
    seen: set[tuple[int, int]] = set()
    stack = [real]
    while stack:
        top = stack.pop()
        try:
            stat = os.stat(top)
            if (stat.st_dev, stat.st_ino) in seen:
                continue
            seen.add((stat.st_dev, stat.st_ino))
            with os.scandir(top) as entries:
                listed = list(entries)
        except OSError as exc:
            raise Unsure(f"can't list {top}") from exc
        for entry in listed:
            _tick()
            if _sealed(entry.name):
                return entry.path
            if entry.is_symlink():
                target = os.path.realpath(entry.path)
                if _sealed(target):
                    return f"{entry.path} -> {target}"
                if os.path.isdir(target):
                    stack.append(target)
            elif entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
    return None


def _candidates(word: Word, cwds: list[str | None]) -> list[str]:
    """Every existing path this word may stand for, from each folder the shell may be in.
    Raises Unsure when there is none, or when the folder is one the hook can't follow."""
    text = os.path.expanduser(word.text) if word.tilde else word.text
    found: list[str] = []
    for cwd in cwds:
        if cwd is None and not os.path.isabs(text):
            raise Unsure(f"{word.text} is relative to a folder the hook can't follow")
        base = cwd or "/"
        if word.glob:
            found += _expand(os.path.expanduser(word.pattern) if word.tilde else word.pattern, base)
        path = os.path.join(base, text)
        if os.path.lexists(path):
            found.append(path)
    if not found:
        raise Unsure(f"{word.text} doesn't resolve to a file")
    return found


def _check(word: Word, cwds: list[str | None]) -> str | None:
    """A sealed file this path argument is, or holds, or None. Raises Unsure."""
    return next((hit for p in _candidates(word, cwds) if (hit := _holds_sealed(p))), None)


def _names_sealed(word: str, cwd: str | None) -> str | None:
    """The sealed file this word names, or None. Globs are expanded as the shell would, and
    a symlink counts as the file it points to."""
    for m in SEALED_FILE_RE.finditer(word):
        if m[1] not in OPENED:
            return m[0]
    path = word.split(":", 1)[-1].lstrip("<>")  # REV:path, and a redirect written as <path
    if not path:
        return None
    if _sealed(path):
        return path
    if cwd is None:
        return None
    full = os.path.expanduser(path)
    if GLOB_CHARS & set(path):
        try:
            hits = (h for h in _expand(full, cwd) if _sealed(h) or _sealed(os.path.realpath(h)))
            return next(hits, None)
        except Unsure:
            return f"{path} (too many matches to check)"
    real = os.path.realpath(os.path.join(cwd, full))
    return f"{path} -> {real}" if _sealed(real) else None


def _git_lines(cwd: str, *args: str) -> list[str]:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", cwd, *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unsure(f"git can't list what {' '.join(args[:2])} covers") from exc
    return [p for p in out.split("\0") if p]


# --- Shell words -------------------------------------------------------------------------
def _strip_heredocs(command: str) -> str:
    """The command without the lines of its here-documents: they are text, not commands."""
    out: list[str] = []
    pending: list[tuple[str, bool]] = []  # (delimiter, strip leading tabs)
    quote = ""
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and quote != "'":
            out.append(command[i : i + 2])
            i += 2
            continue
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif command.startswith("<<", i) and not command.startswith("<<<", i):
            strip = command.startswith("-", i + 2)
            j = i + 2 + strip
            while j < n and command[j] in " \t":
                j += 1
            k = j
            while k < n and command[k] not in " \t\n;|&<>()":
                k += 1
            pending.append((re.sub(r"[\"'\\]", "", command[j:k]), strip))
            out.append(command[i:k])
            i = k
            continue
        elif ch == "\n" and pending:
            out.append(ch)
            i += 1
            for delimiter, strip_tabs in pending:  # skip each document, up to its last line
                while i < n:
                    end = command.find("\n", i)
                    end = n if end < 0 else end
                    line = command[i:end]
                    i = end + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                        break
            pending = []
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split(command: str) -> list[tuple[str, str]]:
    """The command cut at each `|`, `||`, `&`, `&&`, `;` and newline that is not quoted or
    escaped, each part with the separator before it ("" for the first). A `&` or `|` inside a
    redirection (`2>&1`, `&>`, `>|`) is no cut. Raises ValueError when the quotes don't
    balance."""
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    sep = ""
    quote = ""
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and quote != "'":
            if i + 1 >= n:
                raise ValueError("a trailing backslash")
            current += [ch, command[i + 1]]
            i += 2
            continue
        if quote:
            current.append(ch)
            quote = "" if ch == quote else quote
            i += 1
            continue
        if ch in "'\"":
            current.append(ch)
            quote = ch
            i += 1
            continue
        prev = command[i - 1] if i else " "
        nxt = command[i + 1] if i + 1 < n else " "
        redirect = (ch == "&" and (prev in "<>" or nxt == ">")) or (ch == "|" and prev == ">")
        if ch in SEPARATORS and not redirect:
            op = command[i : i + 2] if command[i : i + 2] in ("||", "&&", "|&", ";;") else ch
            parts.append(("".join(current), sep))
            current = []
            sep = "|" if op == "|&" else op
            i += len(op)
            continue
        current.append(ch)
        i += 1
    if quote:
        raise ValueError("unbalanced quotes")
    return [*parts, ("".join(current), sep)]


def _substitutions(command: str) -> list[str]:
    """The commands inside unquoted $(...), `...`, <(...) and >(...). Those inside double
    quotes are left alone: that is where a commit message's here-document lives."""
    bodies: list[str] = []
    i, n = 0, len(command)
    quote = ""
    while i < n:
        ch = command[i]
        if ch == "\\" and quote != "'":
            i += 2
        elif quote:
            quote = "" if ch == quote else quote
            i += 1
        elif ch in "'\"":
            quote = ch
            i += 1
        elif ch == "`":
            end = command.find("`", i + 1)
            end = n if end < 0 else end
            bodies.append(command[i + 1 : end])
            i = end + 1
        elif ch in "$<>" and command.startswith("(", i + 1):
            depth, j, inner = 1, i + 2, ""
            while j < n and depth:
                c = command[j]
                if inner:
                    inner = "" if c == inner else inner
                elif c in "'\"":
                    inner = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                j += 1
            bodies.append(command[i + 2 : j - 1 if not depth else n])
            i = j
        else:
            i += 1
    return bodies


def _prefix_len(words: list[str]) -> int:
    """How many words come before the real program: assignments, keywords, and wrappers with
    their own options."""
    i = 0
    while i < len(words):
        word = words[i]
        if ASSIGNMENT_RE.match(word) or word in KEYWORDS:
            i += 1
            continue
        wrapper = WRAPPERS.get(_program(word))
        if wrapper is None:
            break
        takes_value, plain = wrapper
        i += 1
        while i < len(words) and (words[i].startswith("-") or ASSIGNMENT_RE.match(words[i])):
            i += 2 if words[i] in takes_value else 1
        i += plain
    return i


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    return words[_prefix_len(words) :]


def _double_quoted(segment: str, i: int) -> tuple[str, int]:
    """The text of a double-quoted string that starts after index `i - 1`, and the index
    after its closing quote. Raises Unsure on an expansion inside it."""
    buf: list[str] = []
    n = len(segment)
    while i < n and segment[i] != '"':
        ch = segment[i]
        if ch == "\\" and segment[i + 1 : i + 2] in ("\\", '"', "$", "`", "\n"):
            buf.append(segment[i + 1] if segment[i + 1] != "\n" else "")
            i += 2
            continue
        if ch == "`" or (ch == "$" and segment[i + 1 : i + 2] and segment[i + 1] in EXPANDS):
            raise Unsure("an expansion the hook can't follow")
        buf.append(ch)
        i += 1
    if i >= n:
        raise Unsure("quotes that don't balance")
    return "".join(buf), i + 1


def _brace_list(segment: str, i: int) -> bool:
    """True if the `{` at `i` starts a brace list the shell expands (`{a,b}` or `{1..3}`)."""
    end = i + 1
    while end < len(segment) and segment[end] not in " \t\n}":
        end += 1
    body = segment[i + 1 : end]
    return segment[end : end + 1] == "}" and ("," in body or ".." in body)


REDIRECTS = ("&>>", "&>", "<<<", "<<", "<>", "<&", ">>", ">|", ">&", "<", ">")


def _scan(segment: str) -> tuple[list[Word], list[Word]]:
    """The words of one simple command, quote by quote, and the files it takes input from.
    Raises Unsure on what the hook doesn't model: an expansion, a substitution, a subshell, a
    brace list, a here-document."""
    words: list[Word] = []
    inputs: list[Word] = []
    text: list[str] = []
    pattern: list[str] = []
    state = {"started": False, "quoted": False, "glob": False, "tilde": False}
    target: list[str] = []  # "in" or "out" while a redirection waits for its file

    def add(chars: str, quoted: bool) -> None:
        text.append(chars)
        pattern.append(
            "".join(f"[{c}]" if c in GLOB_CHARS else c for c in chars) if quoted else chars
        )
        state["started"] = True
        state["quoted"] |= quoted

    def finish() -> None:
        if state["started"]:
            word = Word("".join(text), "".join(pattern), state["glob"], state["tilde"])
            if not target:
                words.append(word)
            elif target.pop() == "in":
                inputs.append(word)
        text.clear()
        pattern.clear()
        state.update(started=False, quoted=False, glob=False, tilde=False)

    i, n = 0, len(segment)
    while i < n:
        ch = segment[i]
        if ch in " \t\r\n":
            finish()
            i += 1
        elif ch == "#" and not state["started"]:
            break  # a comment
        elif ch == "\\":
            if i + 1 >= n:
                raise Unsure("a trailing backslash")
            if segment[i + 1] != "\n":
                add(segment[i + 1], True)
            i += 2
        elif ch == "'":
            end = segment.find("'", i + 1)
            if end < 0:
                raise Unsure("quotes that don't balance")
            add(segment[i + 1 : end], True)
            i = end + 1
        elif ch == '"':
            inner, i = _double_quoted(segment, i + 1)
            add(inner, True)
        elif ch == "$" and segment[i + 1 : i + 2] and segment[i + 1] in EXPANDS | {"'", '"'}:
            raise Unsure("an expansion the hook can't follow")
        elif ch == "`":
            raise Unsure("a command substitution")
        elif ch in "()":
            raise Unsure(f"an unquoted {ch}")
        elif ch == "{" and _brace_list(segment, i):
            raise Unsure("a brace list")
        elif ch in "<>&":
            if state["started"] and not state["quoted"] and "".join(text).isdigit():
                text.clear()  # the file descriptor of `2>`
                pattern.clear()
                state["started"] = False
            finish()
            if target:
                raise Unsure("a redirection with no file")
            op = next((r for r in REDIRECTS if segment.startswith(r, i)), None)
            if op is None:
                raise Unsure("an unquoted &")
            if op.startswith("<<"):
                raise Unsure("a here-document")
            i += len(op)
            dup = re.match(r"\d+|-", segment[i:]) if op in (">&", "<&") else None
            if dup:
                i += dup.end()
            elif op == "<&":
                raise Unsure("an input redirection the hook can't follow")
            else:
                target.append("in" if op in ("<", "<>") else "out")
        else:
            if ch in GLOB_CHARS:
                state["glob"] = True
            if ch == "~" and not state["started"]:
                state["tilde"] = True
            add(ch, False)
            i += 1
    finish()
    if target:
        raise Unsure("a redirection with no file")
    return words, inputs


def _parse(
    args: list[Word],
    flags: frozenset[str],
    values: frozenset[str],
    longs: dict[str, bool | str | None],
) -> tuple[list[tuple[str, Word | None]], list[Word], list[Word], bool]:
    """A search's options (name and value), its plain words before `--`, those after, and
    whether there was a `--`. Long options must be spelt out in full. Raises Unsure on an
    option it doesn't know."""
    options: list[tuple[str, Word | None]] = []
    plain: list[Word] = []
    after: list[Word] = []
    dashdash = False
    i = 0
    while i < len(args):
        word, t = args[i], args[i].text
        i += 1
        if dashdash:
            after.append(word)
        elif t == "--":
            dashdash = True
        elif t in ("(", ")") and t in longs:
            options.append((t, None))
        elif t == "-" or not t.startswith("-"):
            plain.append(word)
        elif t.startswith("--"):
            name, eq, value = t[2:].partition("=")
            if name in longs:
                kind = longs[name]
            elif name.startswith("no-") and longs.get(name[3:], BLOCK) != BLOCK:
                kind = False  # --no-X turns a known option off
            else:
                raise Unsure(f"an option the hook doesn't know ({t})")
            if kind == BLOCK:
                raise Unsure(f"{t} reaches past the paths or runs a program")
            if kind is True and not eq:
                if i >= len(args):
                    raise Unsure(f"{t} with no value")
                options.append((name, args[i]))
                i += 1
            elif kind is False and eq:
                raise Unsure(f"{t} takes no value")
            else:
                options.append((name, Word(value) if eq else None))
        else:
            for j, c in enumerate(t[1:], start=1):
                if c in values:
                    if j + 1 < len(t):
                        options.append((c, Word(t[j + 1 :])))
                    elif i < len(args):
                        options.append((c, args[i]))
                        i += 1
                    else:
                        raise Unsure(f"-{c} with no value")
                    break
                if c not in flags:
                    raise Unsure(f"an option the hook doesn't know ({t})")
                options.append((c, None))
    return options, plain, after, dashdash


# --- Rule 5: searches --------------------------------------------------------------------
def _grep_spec(program: str, args: list[Word]) -> Spec | None:
    options, plain, after, _ = _parse(args, GREP_FLAGS, GREP_VALUES, GREP_LONG)
    names = {name for name, _ in options}
    if names & {"help", "version", "V"}:
        return None
    if program not in ("grep", "egrep", "fgrep", "ggrep", "rgrep"):
        raise Unsure(f"{program} reads inside compressed files and archives")
    recursive = program == "rgrep" or bool(names & {"r", "R", "recursive", "dereference-recursive"})
    recursive |= any(
        n in ("d", "directories") and v is not None and v.text not in ("read", "skip")
        for n, v in options
    )
    spec = Spec(plain + after, [v for n, v in options if n in ("f", "file", "exclude-from") and v])
    spec.recursive = recursive
    if not names & {"e", "f", "regexp", "file"} and spec.paths:
        spec.pattern = spec.paths.pop(0)
    return spec


def _rg_spec(args: list[Word]) -> Spec | None:
    options, plain, after, _ = _parse(args, RG_FLAGS, RG_VALUES, RG_LONG)
    names = {name for name, _ in options}
    if names & {"help", "h", "version", "V", "type-list", "pcre2-version", "generate"}:
        return None
    spec = Spec(plain + after, [v for n, v in options if n in ("f", "file", "ignore-file") and v])
    if not names & {"e", "f", "regexp", "file", "files"} and spec.paths:
        spec.pattern = spec.paths.pop(0)
    return spec


def _search(program: str, segment: str, cwds: list[str | None], piped: bool) -> str | None:
    """Rule 5 for grep, rg and the like: the reason to block, or None."""
    try:
        words, inputs = _scan(segment)
        k = _prefix_len([w.text for w in words])
        if k >= len(words) or _program(words[k].text) != program:
            raise Unsure("the hook can't find the program")
        if CHDIR_FLAGS & {w.text.split("=", 1)[0] for w in words[:k]}:
            raise Unsure("a wrapper that changes folder")
        args = words[k + 1 :]
        if program in GREPS:
            spec = _grep_spec(program, args)
        elif program == "rg":
            spec = _rg_spec(args)
        elif any(w.text.startswith("-") and w.text != "-" for w in args):
            raise Unsure(f"{program}'s options aren't modelled")
        else:
            spec = Spec(args[1:], pattern=args[0] if args else None)
        if spec is None:
            return None  # --help or --version: nothing is read
        if not spec.paths and (spec.recursive or not (piped or inputs)):
            raise Unsure("no path given, so it would search the whole folder")
        for word in [*spec.paths, *spec.reads, *inputs]:
            if word.text != "-" and (hit := _check(word, cwds)):
                return f"{program} would search a sealed file ({hit})"
        if spec.pattern is not None and spec.pattern.glob:  # the shell may expand it too
            for cwd in cwds:
                if cwd is None:
                    raise Unsure("a glob in a folder the hook can't follow")
                for path in _expand(spec.pattern.pattern, cwd):
                    if hit := _holds_sealed(path):
                        return f"{program} would search a sealed file ({hit})"
    except Unsure as exc:
        return f"{program} can't be checked ({exc}), so it counts as a search of sealed files"
    return None


def _chdir(word: Word, cwd: str | None) -> str:
    text = os.path.expanduser(word.text) if word.tilde else word.text
    if word.glob or (cwd is None and not os.path.isabs(text)):
        raise Unsure(f"a folder the hook can't follow ({word.text})")
    path = os.path.realpath(os.path.join(cwd or "/", text))
    if not os.path.isdir(path):
        raise Unsure(f"{word.text} is not a folder")
    return path


def _revs_and_paths(words: list[Word], cwds: list[str | None]) -> tuple[list[Word], list[Word]]:
    """git grep's own rule when there is no `--`: a word that is a file is a path, any other
    a revision, and once one path is seen the rest are paths."""
    revs: list[Word] = []
    paths: list[Word] = []
    for word in words:
        if paths or GLOB_CHARS & set(word.text) or word.text.startswith(":"):
            paths.append(word)
            continue
        text = os.path.expanduser(word.text) if word.tilde else word.text
        exists = [c is not None and os.path.lexists(os.path.join(c, text)) for c in cwds]
        if all(exists):
            paths.append(word)
        elif not any(exists):
            revs.append(word)
        else:
            raise Unsure(f"{word.text} is a path from one folder and not another")
    return revs, paths


def _git_grep(segment: str, cwds: list[str | None]) -> str | None:
    """Rule 5 for git grep: the reason to block, or None."""
    try:
        words, _ = _scan(segment)
        k = _prefix_len([w.text for w in words])
        if k >= len(words) or _program(words[k].text) != "git":
            raise Unsure("the hook can't find the program")
        if CHDIR_FLAGS & {w.text.split("=", 1)[0] for w in words[:k]}:
            raise Unsure("a wrapper that changes folder")
        args = words[k + 1 :]
        i = 0
        while i < len(args) and args[i].text.startswith("-"):
            t = args[i].text
            if t in ("-C", "-c"):
                if i + 1 >= len(args):
                    raise Unsure(f"git {t} with no value")
                if t == "-C":
                    cwds = [_chdir(args[i + 1], c) for c in cwds]
                elif not args[i + 1].text.lower().startswith(GIT_CONFIG_KEYS):
                    raise Unsure(f"git -c {args[i + 1].text}")
                i += 2
            elif t in GIT_GLOBAL_FLAGS:
                i += 1
            else:
                raise Unsure(f"git option {t}")
        if i >= len(args) or args[i].text != "grep":
            raise Unsure("a git command the hook can't read")
        options, plain, after, dashdash = _parse(
            args[i + 1 :], GIT_GREP_FLAGS, GIT_GREP_VALUES, GIT_GREP_LONG
        )
        names = {name for name, _ in options}
        if not names & {"e", "f"}:
            if not plain:
                raise Unsure("no pattern")
            plain = plain[1:]
        revs, paths = (plain, after) if dashdash else _revs_and_paths(plain, cwds)
        if not paths:
            raise Unsure("no path given, so it would search the whole folder")
        reads = [v for n, v in options if n == "f" and v]
        hit = _git_grep_hit(revs, paths, reads, cwds)
    except Unsure as exc:
        return f"git grep can't be checked ({exc}), so it counts as a search of sealed files"
    return f"git grep would search a sealed file ({hit})" if hit else None


def _git_grep_hit(
    revs: list[Word], paths: list[Word], reads: list[Word], cwds: list[str | None]
) -> str | None:
    """A sealed file git grep would read, or None: each path on disk, the tracked files it
    covers (git's own pathspec rules, any case), and each revision's files under it."""
    specs: list[str] = []
    for word in paths:
        if word.text.startswith(":"):
            raise Unsure(f"pathspec magic ({word.text})")
        if word.glob:  # the shell expands it first; git gets the names, or the pattern
            for cwd in cwds:
                pattern = os.path.expanduser(word.pattern) if word.tilde else word.pattern
                for path in _expand(pattern, cwd or "/"):
                    if hit := _holds_sealed(path):
                        return hit
        elif not GLOB_CHARS & set(word.text) and (hit := _check(word, cwds)):
            return hit
        specs.append(os.path.expanduser(word.text) if word.tilde else word.text)
    for word in reads:
        if hit := _check(word, cwds):
            return hit
    wildcard = any(GLOB_CHARS & set(s) for s in specs)
    for cwd in cwds:
        if cwd is None:
            raise Unsure("a cd the hook can't follow")
        for name in _git_lines(cwd, "--icase-pathspecs", "ls-files", "-z", "--", *specs):
            if _sealed(name) or _sealed(os.path.realpath(os.path.join(cwd, name))):
                return name
        for rev in revs:
            if named := _names_sealed(rev.text, cwd):
                return named
            if wildcard:
                raise Unsure("a revision with a wildcard path")
            for name in _git_lines(
                cwd, "ls-tree", "-r", "-z", "--name-only", rev.text, "--", *specs
            ):
                if _sealed(name):
                    return f"{rev.text}:{name}"
    return None


def _runs_reader(words: list[str]) -> str | None:
    """The reader or search among these words (xargs's or find -exec's program), or None."""
    for k, word in enumerate(words):
        program = _program(word)
        if program in READERS:
            return program
        if program == "git" and set(words[k + 1 :]) & (GIT_READERS | {"grep", "log"}):
            return "git"
    return None


def _find(segment: str, words: list[str], cwds: list[str | None]) -> str | None:
    """find -exec with a reader: rule 5 on find's starting paths."""
    at = next((k for k, w in enumerate(words) if w in FIND_EXEC), None)
    reader = _runs_reader(words[at + 1 :]) if at is not None else None
    if reader is None:
        return None
    try:
        scanned, _ = _scan(segment)
        args = scanned[_prefix_len([w.text for w in scanned]) + 1 :]
        while args and re.fullmatch(r"-[HLPEXdsx]|-O\d", args[0].text):
            args = args[1:]
        starts: list[Word] = []
        for word in args:
            if word.text.startswith(("-", "!", "(", ")")):
                break
            starts.append(word)
        if not starts:
            raise Unsure("no path given, so find would start from the whole folder")
        for word in starts:
            if hit := _check(word, cwds):
                return f"find would hand {reader} a sealed file ({hit})"
    except Unsure as exc:
        return f"find -exec {reader} can't be checked ({exc}), so it counts as a search"
    return None


# --- Commands ----------------------------------------------------------------------------
def _cd(words: list[str], cwds: list[str | None]) -> list[str | None]:
    """The folders `cd`, `pushd` or `popd` may leave the shell in, from each it may be in.
    None stands for a folder the hook can't follow."""
    args = [w for w in words[1:] if not (w.startswith("-") and w != "-")]
    if words[0] == "popd" or len(args) > 1 or (words[0] == "pushd" and not args):
        return [None]
    target = os.path.expanduser(args[0] if args else "~")
    if target == "-" or "$" in target or "`" in target:
        return [None]
    out: list[str | None] = []
    for cwd in cwds:
        if cwd is None and not os.path.isabs(target):
            out.append(None)
            continue
        path = os.path.realpath(os.path.join(cwd or "/", target))
        out.append(path if os.path.isdir(path) else None)
    return _union(out)


def _command(command: str, cwds: list[str | None], substitutions: bool = True) -> str | None:
    """The reason to block this shell command, or None. `cwds` holds every folder the shell
    may be in; a `cd` the hook can't follow adds None. `substitutions` judges the commands in
    unquoted $(...) and backticks too; a quoted word judged as a command (it may be a commit
    message) leaves them alone."""
    command = _strip_heredocs(command)
    try:
        parts = _split(command)
    except ValueError:  # can't parse it: split as before, ignoring quotes
        parts = [(s, "?") for s in SEGMENT_SPLIT_RE.split(command)]
    here = pool = list(cwds)
    for segment, sep in parts:
        if sep != "&&":  # a new list or pipe: an earlier cd may or may not have run
            here = pool
        words = _words(segment)
        if words and words[0] in CD:
            here = _cd(words, here)
            pool = _union(pool, here)
        elif reason := _segment(segment, words, here, piped=sep == "|"):
            return reason
    if not substitutions:
        return None
    return next((r for body in _substitutions(command) if (r := _command(body, pool))), None)


def _segment(segment: str, words: list[str], cwds: list[str | None], piped: bool) -> str | None:
    if not words:
        return None
    # A quoted word may be a command of its own (`bash -c 'ls; cat x'`): judge it too.
    for word in words:  # (`2>&1` is a word of its own: no command in it)
        if (
            SEPARATORS & set(word)
            and word.strip() != segment.strip()
            and (reason := _command(word, cwds, substitutions=False))
        ):
            return reason
    program = _program(words[0])
    if program == "eval":
        return _command(" ".join(words[1:]), cwds)
    if program in SHELLS:
        at = next((k for k, w in enumerate(words) if re.fullmatch(r"-[a-z]*c[a-z]*", w)), None)
        return _command(words[at + 1], cwds) if at is not None and at + 1 < len(words) else None
    if program == "xargs":
        reader = _runs_reader(words[1:])
        return f"xargs would hand {reader} paths the hook can't see" if reader else None
    if program == "find":
        return _find(segment, words, cwds)
    if program == "git":
        return _git(segment, words, cwds)
    if program not in READERS:
        return None  # python, pytest, make, uv, gh, a script: never blocked
    files = _files(program, words[1:])
    named = next((s for w in files for c in cwds if (s := _names_sealed(w, c))), None)
    if named:
        return f"{program} would print a sealed file ({named})"
    return _search(program, segment, cwds, piped) if program in SEARCHES else None


def _git(segment: str, words: list[str], cwds: list[str | None]) -> str | None:
    args = words[1:]
    i = 0
    while i < len(args) and args[i].startswith("-"):  # git -C dir, git -c key=value
        i += 2 if args[i] in GIT_VALUE_OPTIONS else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1 :]
    if sub in GIT_READERS or (sub == "log" and any(w in ("-p", "-u", "--patch") for w in rest)):
        named = next((s for w in rest for c in cwds if (s := _names_sealed(w, c))), None)
        return f"git {sub} would print a sealed file ({named})" if named else None
    return _git_grep(segment, cwds) if sub == "grep" else None


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


def decide(tool_name: str, tool_input: dict, cwd: str | None = None) -> str | None:
    """The reason to block this tool call, or None to allow it."""
    cwd = cwd or os.getcwd()
    _budget.update(left=MAX_ENTRIES, deadline=time.monotonic() + MAX_SECONDS)
    reason: str | None = None
    if tool_name == "Read":
        path = str(tool_input.get("file_path", ""))
        real = os.path.realpath(os.path.join(cwd, os.path.expanduser(path))) if path else ""
        if _sealed(path) or _sealed(real):
            reason = f"Read of a sealed file ({path})"
    elif tool_name == "Grep":
        path = str(tool_input.get("path") or "")
        if _sealed(path):
            reason = f"Grep on a sealed file ({path})"
        elif "held_out_" in str(tool_input.get("glob") or ""):
            reason = "Grep with a glob that names sealed files"
    elif tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        try:
            reason = _command(command, [cwd])
        except Exception:  # a bug in the parser: block a search, allow anything else
            if SEARCH_WORD_RE.search(command) or "held_out_" in command:
                reason = "the hook could not read this command"
        if not reason and RISKY_ENV_RE.search(command) and SEARCH_WORD_RE.search(command):
            reason = "a search with an environment variable that changes what it reads"
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
