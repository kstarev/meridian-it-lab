"""Claude Code PreToolUse hook: block reads and searches of sealed files, and nothing else.

Reads one JSON object on stdin. Exit 0 allows. Exit 2 blocks, with a reason on stderr.

A sealed file is a `held_out_*` file whose set is not in OPENED (tools/sealed_paths.py).
The hook blocks only these (docs/sealed-search-guard.md lists them with examples):
  1. Read of a sealed file, or of a symlink to one.
  2. Grep whose path is a sealed file or a symlink to one, or whose glob names `held_out_`;
     and, with a `glob` or `type` or with no path, Grep whose path (the session's folder when
     none is given) holds a sealed file the glob or type may let in. A glob overrides
     `.rgignore`, as `rg -g` does. With a path and neither, a folder that no `.rgignore` above
     it covers is walked too.
  3. Bash: a program that prints file content, with a sealed file among its arguments or in
     an option's value; and, for any program, a sealed file fed with `<`, run as a shell
     script, or given to a program the hook can't name.
  4. Bash: `git show`, `git diff`, `git blame`, `git cat-file`, `git log -p` and the like
     naming a sealed file; and, naming none, git output past `.gitattributes` (`--text`,
     an external diff), `git difftool`, `cat-file --batch`, a blob by its hash, and
     `git archive` to stdout of a tree that holds a sealed file.
  5. Bash: a search (`grep`, `rg`, `git grep`, `find -exec grep`, ...) unless it names
     explicit paths, the hook resolves every one (globs, `~`, `..`, symlinks, the folder after
     a `cd`), and none is or holds a sealed file. When the hook can't be sure, it blocks.

It never blocks a program that runs code (python, pytest, make, uv, gh, a shell script), so
the sealed runner, the tests and CI are not touched, unless the shell itself feeds it a sealed
file with `<`. Subshells, substitutions (in double quotes too), brace lists, zsh globs and
wrappers such as `uv run` are judged as the commands they run.

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
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from sealed_paths import OPENED, SEALED_GLOB, is_sealed

OFF_ENV = "MERIDIAN_SEALED_GUARD"
USE = "use `python tools/search.py PATTERN` (or `make search q=...`), which leaves sealed paths out"

# --- The rules, in one place -------------------------------------------------------------
# A sealed data file named inside a word (the set's name is group 1).
SEALED_FILE_RE = re.compile(r"(held_out_[A-Za-z0-9_]+?)\.(?:json\.gz|py\.gz|json)\b")
# Programs that search file content. Rule 5 checks every path they are given.
GREPS = frozenset(
    {
        *("grep", "egrep", "fgrep", "ggrep", "rgrep", "zgrep", "zegrep", "zfgrep", "bzgrep"),
        *("bzegrep", "bzfgrep", "xzgrep", "xzegrep", "xzfgrep", "lzgrep", "lzegrep", "lzfgrep"),
        *("zstdgrep", "zipgrep"),
    }
)
# options not modelled: any blocks. mdfind searches the content Spotlight has indexed.
OTHER_SEARCHES = frozenset({"ag", "ack", "ugrep", "ug", "pcregrep", "pcre2grep", "sift", "pt"})
OTHER_SEARCHES |= {"rga", "mdfind"}
SEARCHES = GREPS | {"rg"} | OTHER_SEARCHES
# Programs that print a file's content. The file must be one of the program's own arguments.
READERS = frozenset(
    {
        *("cat", "head", "tail", "less", "more", "bat", "nl", "tac", "cut", "sort", "uniq"),
        *("sed", "awk", "jq", "diff", "cmp", "strings", "xxd", "hexdump", "od"),
        *("zcat", "gzcat", "gunzip", "open", "code", "vim", "vi", "nano"),
        # encoders and line tools: each prints the file, changed but readable
        *("base64", "base32", "basenc", "uuencode", "uudecode", "rev", "shuf", "fold", "fmt"),
        *("pr", "paste", "column", "expand", "unexpand", "comm", "join", "look", "iconv", "dd"),
        *("vis", "unvis", "sdiff", "diff3", "colordiff", "textutil", "plutil", "xmllint"),
        *("curl", "ex", "view", "ed", "nvim", "vimdiff", "hexyl", "most", "pg"),
        # compressed files and archives
        *("zless", "zmore", "zdiff", "zcmp", "bzcat", "bzless", "bzmore", "bzdiff", "bzcmp"),
        *("xzcat", "xzless", "xzmore", "xzdiff", "xzcmp", "lzcat", "lzless", "lzmore"),
        *("zstdcat", "zstdless", "gzip", "bzip2", "bunzip2", "xz", "unxz", "zstd", "unzstd"),
        *("lz4", "lz4cat", "brotli", "funzip", "tar", "bsdtar", "pax", "shar", "zip", "unzip"),
        # GNU tools from Homebrew, and other awks
        *("gcat", "ghead", "gtail", "gsed", "gawk", "mawk", "nawk", "gsort", "gtac", "gcut"),
        *("gnl", "guniq", "gpaste", "gfold", "gfmt", "gpr", "gexpand", "gunexpand", "gcomm"),
        *("gjoin", "god", "gbase64", "gbase32", "gbasenc", "gshuf", "gdiff", "gcmp"),
        *SEARCHES,
    }
)
AWKS = frozenset({"awk", "gawk", "mawk", "nawk"})
# For these the first plain argument is a pattern or a script, not a file.
PATTERN_FIRST = frozenset({"sed", "gsed", *AWKS, "jq", "look", *SEARCHES})
# Options whose value is the pattern or script (not a file), and options whose value is a file
# read as the script or the patterns, by program. For any other program `-e` and `-f` are plain
# options (`cat -e`, `sort -f`, `tail -f`), so the next word is still a file.
_SED = (frozenset({"-e", "--expression"}), frozenset({"-f", "--file"}))
_AWK = (frozenset({"-e", "--source"}), frozenset({"-f", "--file"}))
_GREP = (frozenset({"-e", "--regexp"}), frozenset({"-f", "--file"}))
SCRIPT_OPTIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "sed": _SED,
    "gsed": _SED,
    **dict.fromkeys(AWKS, _AWK),
    "jq": (frozenset(), frozenset({"-f", "--from-file"})),
    **dict.fromkeys(SEARCHES, _GREP),
}
# Options that take a value which is not the pattern: the value is still checked as a file.
VALUE_OPTIONS: dict[str, frozenset[str]] = dict.fromkeys(AWKS, frozenset({"-v", "-F"}))
GIT_READERS = frozenset({"show", "diff", "blame", "cat-file"})
GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
)
# git commands that print file content when they name a file (rule 4).
GIT_NAMED = GIT_READERS | {"annotate", "merge-file", "archive", "format-patch", "range-diff"}
GIT_NAMED |= {"diff-tree", "diff-index", "diff-files"}
GIT_LOGS = frozenset({"log", "whatchanged", "reflog"})  # they print content with a patch flag
# git commands that print diffs: with these, `.gitattributes`' `-diff` keeps sealed lines out.
GIT_DIFFS = GIT_LOGS | {"diff", "diff-tree", "diff-index", "diff-files", "show", "format-patch"}
GIT_DIFFS |= {"range-diff", "stash"}
GIT_PATCH_RE = re.compile(
    r"-p|-u|--patch|-U\d*|--unified(=.*)?|-L.*|-W|--function-context|--word-diff(=.*)?"
    r"|--color-words(=.*)?|--patch-with-stat|--patch-with-raw|--cc|-c|--remerge-diff"
)
# Options that print a sealed file's lines past `-diff`, or run a program on each file.
GIT_PAST_ATTRIBUTES = frozenset({"-a", "--text", "--binary", "--ext-diff"})
GIT_PAST_ENV = frozenset({"GIT_EXTERNAL_DIFF", "GIT_ATTR_SOURCE"})
ARCHIVE_VALUES = frozenset(
    {"--format", "--prefix", "--exec", "--add-file", "--add-virtual-file", "--mtime"}
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
    # macOS: run a developer tool, an architecture, a recorded or limited session
    "xcrun": (frozenset({"-sdk", "--sdk", "-toolchain", "--toolchain"}), 0),
    "arch": (frozenset({"-e", "-d"}), 0),
    "script": (frozenset({"-t", "-T"}), 1),  # script [-q] FILE PROGRAM ...
    "taskpolicy": (frozenset({"-c", "-d", "-g", "-t", "-l", "-p", "-G", "-T"}), 0),
    "sandbox-exec": (frozenset({"-f", "-n", "-p", "-D"}), 0),
    **{
        w: (frozenset(), 0)
        for w in ("command", "builtin", "time", "nohup", "caffeinate", "noglob", "nocorrect")
    },
}
# `uv run` runs the program after its options. Options that take a value, before and after `run`.
UV_VALUES = frozenset(
    {
        *("--directory", "--project", "--config-file", "--cache-dir", "--color", "--with"),
        *("--with-editable", "--with-requirements", "--package", "--python", "-p", "--extra"),
        *("--group", "--only-group", "--no-group", "--env-file", "--index", "--default-index"),
        *("-i", "--index-url", "--extra-index-url", "--find-links", "-f", "--refresh-package"),
        *("--reinstall-package", "--upgrade-package", "-P", "--resolution", "--prerelease"),
        *("--exclude-newer", "--link-mode", "--python-platform", "--keyring-provider"),
        *("--index-strategy", "--config-setting", "-C", "--no-binary-package"),
        *("--no-build-package", "--no-extra", "--fork-strategy", "--allow-insecure-host"),
        *("--python-preference", "--no-sources-package"),
    }
)
CHDIR_FLAGS = frozenset({"-C", "-D", "--chdir", "--directory"})  # an option that changes folder
KEYWORDS = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!", "{"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
CD = frozenset({"cd", "pushd", "popd"})
FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[|;&\n]")  # only when the quotes don't balance
SEPARATORS = frozenset("|;&\n")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
FUNCTION_RE = re.compile(r"[\w.:-]+\(\)")  # `f()` before a function's body
# An environment variable that changes what grep or git grep reads.
RISKY_ENV_RE = re.compile(
    r"\b(?:GREP_OPTIONS|GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE|GIT_COMMON_DIR|GIT_NAMESPACE"
    r"|GIT_CONFIG\w*|GIT_\w*PATHSPECS|RIPGREP_CONFIG_PATH)\b"
)
SEARCH_WORD_RE = re.compile(rf"\b(?:{'|'.join(sorted(SEARCHES))})\b")
GLOB_CHARS = frozenset("*?[")
# zsh also globs with groups `(a|b)`, qualifiers `*(.)`, numeric ranges `<1-3>`, and, with
# EXTENDED_GLOB, `^x`, `x~y` and `x#`. A reader's file argument with any of these is expanded.
ZSH_GLOB_CHARS = GLOB_CHARS | frozenset("(<^#~")
NUMERIC_GLOB_RE = re.compile(r"<\d*-\d*>")
EXPANDS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789{(@*#?$!-")
MAX_ENTRIES = 300_000  # files and folders looked at per tool call, at most
MAX_SECONDS = 5.0
MAX_MATCHES = 20_000  # paths one glob may expand to
MAX_GLOB_ALTERNATIVES = 1_000  # globs one Grep `glob` may stand for, its braces expanded

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


def _zsh_part(part: str) -> str:
    """One part of a zsh glob, read loosely: a group or qualifier `(...)` and a range `<1-3>`
    become `*`; with `^`, `#`, or a `~` after the start (EXTENDED_GLOB), the part is `*`."""
    if "^" in part or "#" in part or "~" in part[1:-1]:
        return "*"
    loose = NUMERIC_GLOB_RE.sub("*", part)
    while (step := re.sub(r"\([^()/]*\)", "*", loose)) != loose:
        loose = step
    # a group the command split cut at its `|`: `(held|x)` arrives as `(held` and `x)`
    loose = re.sub(r"\(.*", "*", re.sub(r".*\)", "*", loose))
    return loose if loose == part else re.sub(r"\*+", "*", loose)


def _expand(pattern: str, base: str, zsh: bool = False) -> list[str]:
    """The paths a shell glob may expand to, from `base`, and a few more: no shell matches a
    path this leaves out. Classes match any character, case and dot files don't matter, and
    `**` stands for every folder below. With `zsh`, zsh's own glob syntax is read too."""
    pattern = _loosen(pattern)
    if pattern.startswith("/"):
        base = "/"
    current = [base]
    for part in (p for p in pattern.split("/") if p):
        if zsh:
            part = _zsh_part(part)
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


def _holds_sealed(path: str, admits: Callable[[str], bool] | None = None) -> str | None:
    """`path` if it is a sealed file or a symlink to one; else a sealed file, or a symlink to
    one, anywhere below it, following every symlink; else None. Names only, never content.
    With `admits`, a sealed file below counts only if `admits` its name, or the name of the
    symlink that leads to it; a folder with a sealed name always counts."""

    def counts(*names: str) -> bool:
        return admits is None or any(admits(n) for n in names)

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
            if _sealed(entry.name) and (entry.is_dir() or counts(entry.name)):
                return entry.path
            if entry.is_symlink():
                target = os.path.realpath(entry.path)
                if _sealed(target) and (
                    os.path.isdir(target) or counts(entry.name, os.path.basename(target))
                ):
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
    """The sealed file this word names, or None. Globs are expanded as zsh would, and a
    symlink counts as the file it points to. The value of `KEY=VALUE` (`dd if=...`,
    `--from-file=...`), a `%`-escaped URL (`file:///...`) and `@file` count too."""
    forms = [word, urllib.parse.unquote(word)] if "%" in word else [word]
    forms += [f.split("=", 1)[1] for f in forms if "=" in f]
    for form in forms:
        for m in SEALED_FILE_RE.finditer(form):
            if m[1] not in OPENED:
                return m[0]
        # REV:path, file://path, a redirect written as <path, and curl's @path
        if hit := _path_names_sealed(form.split(":", 1)[-1].lstrip("<>@"), cwd):
            return hit
    return None


def _names_file(word: str, cwd: str | None) -> str | None:
    """As `_names_sealed`, for the argument of a program that runs code: only a sealed file
    (with its extension, a glob, or a path that exists) counts, not a set's name such as
    `--set held_out_month2`, which a runner takes."""
    path = os.path.expanduser(word)
    if (
        SEALED_FILE_RE.search(word)
        or ZSH_GLOB_CHARS & set(path.lstrip("~"))
        or os.path.lexists(os.path.join(cwd or "/", path))
    ):
        return _names_sealed(word, cwd)
    return None


def _path_names_sealed(path: str, cwd: str | None) -> str | None:
    if not path:
        return None
    if _sealed(path):
        return path
    full = os.path.expanduser(path)
    globbed = bool(ZSH_GLOB_CHARS & set(full.lstrip("~")))
    if cwd is None:
        if globbed and not os.path.isabs(full):
            return f"{path} (a glob in a folder the hook can't follow)"
        return None
    if globbed:
        try:
            hits = _expand(full, cwd, zsh=True)
            return next((h for h in hits if _sealed(h) or _sealed(os.path.realpath(h))), None)
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
def _strip_heredocs(command: str) -> tuple[str, list[tuple[str, bool]]]:
    """The command without the lines of its here-documents, and those documents, each with
    whether the shell expands `$(...)` in it (its delimiter is not quoted). The lines are text,
    not commands. A here-document inside `"$(...)"` counts too: a commit message lives there."""
    out: list[str] = []
    bodies: list[tuple[str, bool]] = []
    pending: list[tuple[str, bool, bool]] = []  # (delimiter, strip leading tabs, expands)
    stack: list[str] = []  # where the scan is: '"', "(" or "`"
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        top = stack[-1] if stack else ""
        if ch == "\\":
            out.append(command[i : i + 2])
            i += 2
            continue
        if ch == "\n" and pending:
            out.append(ch)
            i += 1
            for delimiter, strip_tabs, expands in pending:  # skip each document, to its last line
                lines: list[str] = []
                while i < n:
                    end = command.find("\n", i)
                    end = n if end < 0 else end
                    line = command[i:end]
                    i = end + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                        break
                    lines.append(line)
                bodies.append(("\n".join(lines), expands))
            pending = []
            continue
        if top == '"':
            if ch == '"':
                stack.pop()
            elif ch == "`":
                stack.append("`")
            elif command.startswith("$(", i):
                stack.append("(")
                out.append("$(")
                i += 2
                continue
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            end = command.find("'", i + 1)
            end = n - 1 if end < 0 else end
            out.append(command[i : end + 1])
            i = end + 1
            continue
        if ch == '"':
            stack.append('"')
        elif ch == "`":
            if top == "`":
                stack.pop()
            else:
                stack.append("`")
        elif ch == "(":
            stack.append("(")
        elif ch == ")" and top == "(":
            stack.pop()
        elif command.startswith("<<", i) and not command.startswith("<<<", i):
            strip = command.startswith("-", i + 2)
            j = i + 2 + strip
            while j < n and command[j] in " \t":
                j += 1
            k = j
            while k < n and command[k] not in " \t\n;|&<>()":
                if command[k] in "'\"":
                    close = command.find(command[k], k + 1)
                    k = n if close < 0 else close + 1
                else:
                    k += 2 if command[k] == "\\" else 1
            raw = command[j:k]
            quoted = bool(re.search(r"[\"'\\]", raw))
            pending.append((re.sub(r"[\"'\\]", "", raw), strip, not quoted))
            out.append(command[i:k])
            i = k
            continue
        out.append(ch)
        i += 1
    return "".join(out), bodies


def _close(text: str, i: int) -> tuple[int, bool]:
    """The index after the `)` that closes the `(` at `i`, and whether there is one."""
    depth, j, inner, n = 1, i + 1, "", len(text)
    while j < n and depth:
        c = text[j]
        if c == "\\" and inner != "'":
            j += 2
            continue
        if inner:
            inner = "" if c == inner else inner
        elif c in "'\"":
            inner = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        j += 1
    return (j, True) if not depth else (n, False)


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


def _substitutions(command: str, literal_quotes: bool = False) -> list[str]:
    """The commands inside $(...) and `...`, unquoted or inside double quotes (the shell runs
    those too), and inside unquoted <(...), >(...) and zsh's =(...). Not inside single quotes. With
    `literal_quotes` (the text of a here-document) quotes are plain characters."""
    bodies: list[str] = []
    i, n = 0, len(command)
    quote = ""
    while i < n:
        ch = command[i]
        if ch == "\\" and quote != "'":
            i += 2
        elif quote == "'":
            quote = "" if ch == "'" else quote
            i += 1
        elif ch == "`":
            end = i + 1
            while end < n and command[end] != "`":
                end += 2 if command[end] == "\\" else 1
            bodies.append(command[i + 1 : end])
            i = end + 1
        elif command.startswith("$(", i) or (
            not quote and ch in "<>=" and command.startswith("(", i + 1)
        ):
            end, closed = _close(command, i + 1)
            bodies.append(command[i + 2 : end - 1] if closed else command[i + 2 :])
            i = end
        elif not literal_quotes and ch == '"':
            quote = "" if quote == '"' else '"'
            i += 1
        elif not literal_quotes and ch == "'" and not quote:
            quote = "'"
            i += 1
        else:
            i += 1
    return bodies


def _uv_run(words: list[str], j: int) -> int | None:
    """The index of the program that `uv [options] run [options]` runs, or None if the words
    from `j` are no `uv run`."""
    while j < len(words) and words[j].startswith("-"):
        j += 2 if words[j] in UV_VALUES else 1
    if j >= len(words) or words[j] != "run":
        return None
    j += 1
    while j < len(words) and words[j].startswith("-"):
        if words[j] == "--":
            return j + 1
        j += 2 if words[j] in UV_VALUES else 1
    return j


def _prefix_len(words: list[str]) -> int:
    """How many words come before the real program: assignments, keywords, and wrappers with
    their own options (`uv run` among them)."""
    i = 0
    while i < len(words):
        word = words[i]
        if ASSIGNMENT_RE.match(word) or word in KEYWORDS or FUNCTION_RE.fullmatch(word):
            i += 1
            continue
        if word == "function":  # `function f { cat x; }`: the body is judged
            i += 2
            continue
        if _program(word) == "uv":
            j = _uv_run(words, i + 1)
            if j is None:
                break
            i = j
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
    return [t.text for t in _parse_segment(segment).words]


@dataclass
class Token:
    """One shell word of a segment, quotes removed, for the readers' rules."""

    text: str
    expands: bool = False  # holds a $ or ` the shell expands: the hook can't know the text
    braces: bool = False  # holds an unquoted brace list


@dataclass
class Parsed:
    """One simple command: the words before the program (assignments, wrappers), the program
    and its arguments, the files it reads with `<`, and its here-strings."""

    prefix: list[Token]
    words: list[Token]
    inputs: list[Token]
    herestrings: list[Token]
    cut: int | None = None  # where a command starts after a `)` that closes nothing (a case arm)


def _lex(segment: str) -> tuple[list[Token], list[Token], list[Token], int | None]:
    """The words of one simple command, the files fed to it with `<`, its here-strings, and
    where a command starts after an unmatched `)`. Unlike `_scan` it never gives up: what it
    can't follow is kept as text and marked `expands`. A `(` that starts a command and its `)`
    are grouping, not words; redirections and their files are not words."""
    tokens: list[Token] = []
    inputs: list[Token] = []
    herestrings: list[Token] = []
    buf: list[str] = []
    cur = {"started": False, "expands": False, "braces": False, "open": 0}
    target: list[str] = []
    depth = 0
    cut: int | None = None

    def add(chars: str) -> None:
        buf.append(chars)
        cur["started"] = True

    def finish() -> None:
        if cur["started"]:
            token = Token("".join(buf), bool(cur["expands"]), bool(cur["braces"]))
            dest = target.pop() if target else "word"
            {"word": tokens, "in": inputs, "here": herestrings}.get(dest, []).append(token)
        buf.clear()
        cur.update(started=False, expands=False, braces=False, open=0)

    def command_start() -> bool:
        return not cur["started"] and all(
            ASSIGNMENT_RE.match(t.text) or t.text in KEYWORDS for t in tokens
        )

    i, n = 0, len(segment)
    while i < n:
        ch = segment[i]
        nxt = segment[i + 1 : i + 2]
        if ch in " \t\r\n":
            finish()
            i += 1
        elif ch == "#" and not cur["started"]:
            break  # a comment
        elif ch == "\\":
            if nxt and nxt != "\n":
                add(nxt)
            i += 2
        elif ch == "'":
            end = segment.find("'", i + 1)
            end = n if end < 0 else end
            add(segment[i + 1 : end])
            i = end + 1
        elif ch == '"':
            j, inner = i + 1, []
            while j < n and segment[j] != '"':
                c = segment[j]
                if c == "\\" and segment[j + 1 : j + 2] in ("\\", '"', "$", "`", "\n"):
                    inner.append(segment[j + 1] if segment[j + 1] != "\n" else "")
                    j += 2
                    continue
                if segment.startswith("$(", j):
                    end, _ = _close(segment, j + 1)
                    inner.append(segment[j:end])
                    cur["expands"] = True
                    j = end
                    continue
                if c == "`" or (c == "$" and segment[j + 1 : j + 2] in EXPANDS):
                    cur["expands"] = True
                inner.append(c)
                j += 1
            add("".join(inner))
            i = j + 1
        elif ch == "$" and nxt == "'":  # ANSI-C quoting: $'\x63at' is cat
            j, raw = i + 2, []
            while j < n and segment[j] != "'":
                raw.append(segment[j : j + 2] if segment[j] == "\\" else segment[j])
                j += 2 if segment[j] == "\\" else 1
            try:
                add("".join(raw).encode("latin-1", "backslashreplace").decode("unicode_escape"))
            except UnicodeError:
                add("".join(raw))
                cur["expands"] = True
            i = j + 1
        elif ch == "$" and nxt == "(":
            end, _ = _close(segment, i + 1)
            add(segment[i:end])
            cur["expands"] = True
            i = end
        elif ch == "$" and nxt == '"':
            i += 1  # $"..." is a double-quoted string
        elif ch == "$" and nxt and nxt in EXPANDS:
            add(ch)
            cur["expands"] = True
            i += 1
        elif ch == "`":
            end = i + 1
            while end < n and segment[end] != "`":
                end += 2 if segment[end] == "\\" else 1
            add(segment[i : end + 1])
            cur["expands"] = True
            i = end + 1
        elif ch == "(":
            if command_start():
                depth += 1  # a subshell: `(cat x)`
            elif cur["started"] and "".join(buf).endswith("="):  # an array: a=(x y)
                end, _ = _close(segment, i)
                add(segment[i:end])
                i = end
                continue
            else:  # a zsh glob group or qualifier: pools/(h)eld*, *(.)
                cur["open"] = int(cur["open"]) + 1
                add(ch)
            i += 1
        elif ch == ")":
            if cur["open"]:
                cur["open"] = int(cur["open"]) - 1
                add(ch)
            elif depth:
                finish()
                depth -= 1
            else:
                finish()
                cut = i + 1
            i += 1
        elif ch in "<>" or (ch == "&" and nxt == ">"):
            if ch == "<" and (m := NUMERIC_GLOB_RE.match(segment, i)):
                add(m[0])  # zsh's numeric glob: x<1-3>.json
                i = m.end()
                continue
            if cur["started"] and "".join(buf).isdigit():  # the file descriptor of `2>`
                buf.clear()
                cur["started"] = False
            finish()
            if segment.startswith(("<(", ">("), i):  # judged by _substitutions
                i, _ = _close(segment, i + 1)
                continue
            op = next(r for r in SHELL_REDIRECTS if segment.startswith(r, i))
            i += len(op)
            if op in ("<&", ">&"):
                dup = re.match(r"[ \t]*(?:\d+|-)(?=$|[ \t;|&<>()])", segment[i:])
                if dup:
                    i += dup.end()
                    continue
                target.append("in" if op == "<&" else "out")
            elif op == "<<<":
                target.append("here")
            elif op.startswith("<<"):
                target.append("delimiter")
            else:
                target.append("in" if op in ("<", "<>") else "out")
        else:
            if ch == "{" and _brace_list(segment, i):
                cur["braces"] = True
            add(ch)
            i += 1
    finish()
    return tokens, inputs, herestrings, cut


def _brace_range(body: str) -> list[str] | None:
    """The words of a shell brace range (`1..3`, `a..c`, `1..9..2`), or None."""
    m = re.fullmatch(r"(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])(?:\.\.(-?\d+))?", body)
    if not m:
        return None
    a, b, step = m[1], m[2], abs(int(m[3] or 1)) or 1
    if a.lstrip("-").isdigit() != b.lstrip("-").isdigit():
        return None
    lo, hi = (int(a), int(b)) if a.lstrip("-").isdigit() else (ord(a), ord(b))
    if abs(hi - lo) // step > MAX_GLOB_ALTERNATIVES:
        raise Unsure("a brace range with too many words")
    seq = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
    if not a.lstrip("-").isdigit():
        return [chr(c) for c in seq]
    padded = any(len(x.lstrip("-")) > 1 and x.lstrip("-")[0] == "0" for x in (a, b))
    width = max(len(a), len(b)) if padded else 0
    return [str(c).zfill(width) for c in seq]


def _env_split(tokens: list[Token]) -> list[Token]:
    """`env -S 'cat x'` runs `cat x`: the string's words take its place."""
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        out.append(tokens[i])
        i += 1
        if _program(out[-1].text) != "env":
            continue
        while i < len(tokens) and tokens[i].text.startswith("-"):
            text = tokens[i].text
            if text in ("-S", "--split-string") and i + 1 < len(tokens):
                out += _lex(tokens[i + 1].text)[0]
                i += 2
                break
            if text.startswith(("--split-string=", "-S")) and text not in ("-S",):
                out += _lex(text.split("=", 1)[1] if text.startswith("--") else text[2:])[0]
                i += 1
                break
            out.append(tokens[i])
            i += 1
    return out


def _parse_segment(segment: str) -> Parsed:
    """One simple command, its brace lists expanded and `env -S` strings split."""
    tokens, inputs, herestrings, cut = _lex(segment)
    words: list[Token] = []
    for token in tokens:
        if not token.braces:
            words.append(token)
            continue
        try:
            words += [Token(a, token.expands) for a in _braces(token.text, ranges=True)]
        except Unsure:
            words.append(token)
    words = _env_split(words)
    k = _prefix_len([t.text for t in words])
    return Parsed(words[:k], words[k:], inputs, herestrings, cut)


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
SHELL_REDIRECTS = ("&>>", "&>", "<<<", "<<-", "<<", "<>", "<&", ">>", ">|", ">&", "<", ">")


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
    """The reader or search among these words (xargs's or find -exec's program), or None. A
    word with spaces is read as a command too: `sh -c 'cat "$1"'`."""
    for k, word in enumerate(words):
        program = _program(word)
        if program in READERS:
            return program
        if program == "git" and set(words[k + 1 :]) & (GIT_NAMED | GIT_LOGS | {"grep", "stash"}):
            return "git"
        inner = _words(word) if any(c.isspace() for c in word) else [word]
        if inner != [word] and (reader := _runs_reader(inner)):
            return reader
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


# --- Rule 2: the Grep tool ---------------------------------------------------------------
# Claude Code's Grep tool runs ripgrep. `.rgignore` leaves sealed files out of a folder, but a
# `glob` overrides it, as `rg -g` does. ripgrep judges a file by its own name against a glob:
# a glob that names a folder lets in none of the files below it. So only a glob's last part
# counts, and the hook reads it loosely, so that it lets in at least what ripgrep does.
def _brace_group(glob: str) -> tuple[int, int, list[str]] | None:
    """The first `{...}` group in a glob (start, end, alternatives), or None."""
    depth, start, cuts, i = 0, 0, [0], 0
    while i < len(glob):
        ch = glob[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            if depth == 0:
                start, cuts = i, [i]
            depth += 1
        elif ch == "," and depth == 1:
            cuts.append(i)
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                cuts.append(i)
                return start, i, [glob[cuts[k] + 1 : cuts[k + 1]] for k in range(len(cuts) - 1)]
        i += 1
    return None  # none, or braces that don't balance: ripgrep refuses such a glob


def _braces(glob: str, ranges: bool = False) -> list[str]:
    """The globs `a{b,c}d` stands for (`abd` and `acd`), nested braces too, in the shell's
    order. With `ranges`, a shell range `{1..3}` or `{a..c}` is expanded as well."""
    done: list[str] = []
    todo = [glob]
    steps = 0
    while todo:
        current = todo.pop(0)
        group = _brace_group(current)
        if group is None:
            done.append(current)
        else:
            start, end, alternatives = group
            if ranges and len(alternatives) == 1:
                alternatives = _brace_range(alternatives[0]) or alternatives
            todo[:0] = [current[:start] + a + current[end + 1 :] for a in alternatives]
        steps += 1
        if steps > MAX_GLOB_ALTERNATIVES or len(done) + len(todo) > MAX_GLOB_ALTERNATIVES:
            raise Unsure("a glob with too many alternatives")
    return done


def _grep_admits(glob: str, kind: str) -> Callable[[str], bool] | None:
    """A test of the file names the Grep tool's `glob` may let in, or None when it may let in
    any file: a `type` (the hook doesn't know ripgrep's type list), no glob, only `!` globs,
    or a glob whose last part is `*`, `**` or empty (`dir/`). The glob is read whole, and also
    split at spaces and commas, as Claude Code may split it; braces are expanded; a `[...]`
    class or an escaped character counts as any one character; case doesn't matter. A `!`
    glob only leaves files out, so it is skipped."""
    if kind or not glob.strip():
        return None
    parts = glob.split()
    pieces = {glob.strip(), *parts, *(p for part in parts for p in part.split(","))}
    tests: list[Callable[[str], object]] = []
    for piece in sorted(p for p in pieces if p and not p.startswith("!")):
        for alternative in _braces(piece):
            tail = alternative.rsplit("/", 1)[-1]
            if not tail.strip("*"):
                return None
            tail = re.sub(r"\*+", "*", re.sub(r"\\.", "?", tail))
            tests.append(re.compile(fnmatch.translate(_loosen(tail)), re.IGNORECASE).match)
    if not tests:
        return None
    return lambda name: any(test(name) for test in tests)


def _rgignore_covers(top: str) -> bool:
    """True if ripgrep leaves sealed files out below `top`: an `.rgignore` or `.ignore` with
    the line `held_out_*` in its real folder or a folder above it. Claude Code's Grep searches
    the real path, and ripgrep reads the ignore files of the folders above it."""
    real = os.path.realpath(top)
    folder = real if os.path.isdir(real) else os.path.dirname(real)
    while True:
        for name in (".rgignore", ".ignore"):
            try:
                with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                    if any(line.strip() == SEALED_GLOB for line in f):
                        return True
            except OSError:
                pass
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def _grep_tool(tool_input: dict, cwd: str) -> str | None:
    """Rule 2 for the Grep tool: the reason to block, or None. With a `glob` or `type`, or
    with no path, the path (the session's folder when none is given) is walked, names only,
    and the call is blocked if any file the glob or type may let in is sealed. A path given
    with neither keeps the rule from before, if an `.rgignore` above it leaves sealed files
    out: only a sealed path itself is blocked. With no such `.rgignore` it is walked too."""
    path = str(tool_input.get("path") or "")
    glob = str(tool_input.get("glob") or "")
    kind = str(tool_input.get("type") or "")
    tops = [os.path.join(cwd, path)] if path else [cwd]
    if path.startswith("~"):
        tops.append(os.path.expanduser(path))
    if _sealed(path) or any(_sealed(os.path.realpath(top)) for top in tops):
        return f"Grep on a sealed file ({path})"
    if "held_out_" in glob:
        return "Grep with a glob that names sealed files"
    found = [top for top in tops if os.path.lexists(top)]
    if path and not glob and not kind and all(_rgignore_covers(top) for top in found):
        return None  # `.rgignore` leaves sealed files out of a folder
    try:
        if not found:
            raise Unsure(f"{path} doesn't resolve to a file")
        admits = _grep_admits(glob, kind)
        hit = next((h for top in found if (h := _holds_sealed(top, admits))), None)
    except Unsure as exc:
        return f"Grep can't be checked ({exc}), so it counts as a search of sealed files"
    return f"Grep would search a sealed file ({hit})" if hit else None


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


def _command(
    command: str,
    cwds: list[str | None],
    substitutions: bool = True,
    bodies: list[tuple[str, bool]] | None = None,
) -> str | None:
    """The reason to block this shell command, or None. `cwds` holds every folder the shell
    may be in; a `cd` the hook can't follow adds None. `substitutions` judges the commands in
    $(...) and backticks too; a quoted word judged as a command (it may be a commit message)
    leaves them alone. `bodies` are the here-documents of the command around this one."""
    command, found = _strip_heredocs(command)
    bodies = [*(bodies or []), *found]
    try:
        parts = _split(command)
    except ValueError:  # can't parse it: split as before, ignoring quotes
        parts = [(s, "?") for s in SEGMENT_SPLIT_RE.split(command)]
    here = pool = list(cwds)
    fed: Parsed | None = None
    for segment, sep in parts:
        if sep != "&&":  # a new list or pipe: an earlier cd may or may not have run
            here = pool
        seg = _parse_segment(segment)
        words = [t.text for t in seg.words]
        if words and words[0] in CD:
            here = _cd(words, here)
            pool = _union(pool, here)
        elif reason := _segment(
            segment, seg, here, piped=sep == "|", bodies=bodies, fed=fed if sep == "|" else None
        ):
            return reason
        fed = seg
    if not substitutions:
        return None
    inner = list(_substitutions(command))
    for text, expands in found:  # `$(...)` in a here-document whose delimiter is not quoted
        if expands:
            inner += _substitutions(text, literal_quotes=True)
    return next((r for body in inner if (r := _command(body, pool, bodies=bodies))), None)


def _segment(
    segment: str,
    seg: Parsed,
    cwds: list[str | None],
    piped: bool,
    bodies: list[tuple[str, bool]] | None = None,
    fed: Parsed | None = None,
) -> str | None:
    if seg.cut is not None:  # `a) cat x`: a case arm, or a `)` the split left behind
        rest = segment[seg.cut :]
        if reason := _segment(rest, _parse_segment(rest), cwds, piped, bodies):
            return reason
    for token in seg.inputs:  # `< file` feeds a file to any program, or to none: `$(<file)`
        if named := next((s for c in cwds if (s := _names_sealed(token.text, c))), None):
            return f"the shell would read a sealed file with < ({named})"
    words = [t.text for t in seg.words]
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
    if seg.words[0].expands:  # `$c x`, `"$(which cat)" x`: a program the hook can't name
        named = next((s for w in words[1:] for c in cwds if (s := _names_file(w, c))), None)
        return f"a program the hook can't name would get a sealed file ({named})" if named else None
    program = _program(words[0])
    if program == "eval":
        return _command(" ".join(words[1:]), cwds, bodies=bodies)
    if program in SHELLS or program in ("source", "."):
        return _shell(program, seg, cwds, bodies or [], fed)
    if program == "xargs":
        reader = _runs_reader(words[1:])
        return f"xargs would hand {reader} paths the hook can't see" if reader else None
    if program == "find":
        return _find(segment, words, cwds)
    if program == "git":
        return _git(segment, words, cwds, env=[t.text for t in seg.prefix])
    if program not in READERS:
        return None  # python, pytest, make, uv, gh, a script: never blocked
    # a wrapper that changes folder (`env -C dir cat *.json`): a relative glob can't be checked
    moved = CHDIR_FLAGS & {t.text.split("=", 1)[0] for t in seg.prefix}
    places = [None] if moved else cwds
    files = _files(program, words[1:])
    named = next((s for w in files for c in places if (s := _names_sealed(w, c))), None)
    if named:
        return f"{program} would print a sealed file ({named})"
    return _search(program, segment, cwds, piped) if program in SEARCHES else None


def _shell(
    program: str,
    seg: Parsed,
    cwds: list[str | None],
    bodies: list[tuple[str, bool]],
    fed: Parsed | None,
) -> str | None:
    """A shell, `source` or `.`. Its `-c` string is judged as a command. A sealed file given as
    its script blocks. With no script it reads commands from stdin: its here-strings, the
    here-documents of the command, and what `echo` or `printf` pipes to it are judged."""
    args = [t.text for t in seg.words[1:]]
    script: str | None = args[0] if args else None
    if program in SHELLS:
        at = next((k for k, w in enumerate(args) if re.fullmatch(r"-[a-z]*c[a-z]*", w)), None)
        if at is not None:
            return _command(args[at + 1], cwds, bodies=bodies) if at + 1 < len(args) else None
        k, stdin = 0, False
        while k < len(args) and args[k][:1] in "-+" and args[k] not in ("-", "--"):
            stdin |= bool(re.fullmatch(r"-[a-z]*s[a-z]*", args[k]))
            k += 2 if args[k] in ("-o", "+o", "--rcfile", "--init-file") else 1
        if k < len(args) and args[k] == "--":
            k += 1
        script = None if stdin or k >= len(args) else args[k]
    if script is not None and script not in ("-", "/dev/stdin"):
        named = next((s for c in cwds if (s := _names_sealed(script, c))), None)
        return f"{program} would run a sealed file as a script ({named})" if named else None
    texts = [t.text for t in seg.herestrings] + [text for text, _ in bodies]
    if fed is not None and fed.words and _program(fed.words[0].text) in ("echo", "printf"):
        given = [t.text for t in fed.words[1:]]
        while given and re.fullmatch(r"-[neE]+", given[0]):
            given = given[1:]
        named = next((s for w in given for c in cwds if (s := _names_file(w, c))), None)
        if named:
            return f"{program} would run commands that name a sealed file ({named})"
        texts.append(" ".join(given).replace("\\n", "\n"))
    return next((r for text in texts if (r := _command(text, cwds))), None)


def _git(
    segment: str,
    words: list[str],
    cwds: list[str | None],
    env: list[str] | None = None,
    depth: int = 0,
) -> str | None:
    """Rule 4 and git grep. `env` holds the words before `git` (`GIT_EXTERNAL_DIFF=cat`)."""
    args = words[1:]
    configs: dict[str, str | None] = {}  # -c key=value; None: from --config-env, unseen
    git_cwds = list(cwds)
    past = [w.split("=", 1)[0] for w in env or [] if w.split("=", 1)[0] in GIT_PAST_ENV]
    i = 0
    while i < len(args) and args[i].startswith("-"):  # git -C dir, git -c key=value
        option, _, value = args[i].partition("=")
        if args[i] in GIT_VALUE_OPTIONS | {"--attr-source"} and i + 1 < len(args):
            option, value = args[i], args[i + 1]
            i += 1
        i += 1
        if option == "-c":
            key, _, setting = value.partition("=")
            configs[key.lower()] = setting
        elif option == "--config-env":
            configs[value.split("=", 1)[0].lower()] = None
        elif option == "-C":
            git_cwds = [_chdir_or_none(value, c) for c in git_cwds]
        elif option == "--attr-source":
            past.append(option)
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1 :]
    alias = f"alias.{sub.lower()}"
    if alias in configs:  # `git -c alias.x='!cat' x file`: judge what the alias runs
        body = configs[alias]
        if body is None or depth > 3:
            return f"git {sub} is an alias the hook can't see"
        if body.startswith("!"):
            return _command(f"{body[1:]} {shlex.join(rest)}", cwds)
        new = ["git", *args[:i], *_words(body), *rest]
        return _git(shlex.join(new), new, cwds, env, depth + 1)
    if sub == "difftool":
        return "git difftool hands each file to another program, which may print a sealed file"
    if sub in GIT_DIFFS and (sub != "stash" or rest[:1] == ["show"]):
        past += sorted(GIT_PAST_ATTRIBUTES & set(rest))
        past += [
            f"-c {k}"
            for k in configs
            if re.fullmatch(r"diff\.(.+\.)?(external|command|textconv)", k)
        ]
        if past:
            return f"git {sub} with {past[0]} prints sealed files past .gitattributes"
    if (
        sub in GIT_NAMED
        or (sub in GIT_LOGS and any(GIT_PATCH_RE.fullmatch(w) for w in rest))
        or (sub == "stash" and rest[:1] == ["show"])
    ):
        named = next((s for w in rest for c in git_cwds if (s := _names_sealed(w, c))), None)
        if named:
            return f"git {sub} would print a sealed file ({named})"
        if sub in ("show", "cat-file"):
            return _git_object(sub, rest, git_cwds)
        return _git_archive(rest, git_cwds) if sub == "archive" else None
    return _git_grep(segment, cwds) if sub == "grep" else None


def _chdir_or_none(path: str, cwd: str | None) -> str | None:
    try:
        return _chdir(Word(path, tilde=path.startswith("~")), cwd)
    except Unsure:
        return None


def _git_object(sub: str, rest: list[str], cwds: list[str | None]) -> str | None:
    """git show or git cat-file of an object named by a hash or a ref, not by `REV:path`: the
    reason to block if it is a blob, since the hook can't tell which file the blob was. A
    `--batch` reads its objects from stdin, which the hook can't see."""
    words = rest[: rest.index("--")] if "--" in rest else rest
    plain = [w for w in words if not w.startswith("-")]
    if sub == "cat-file":
        if any(w.split("=", 1)[0] in ("--batch", "--batch-command") for w in words):
            return "git cat-file --batch prints objects the hook can't see"
        types = ("blob", "tree", "commit", "tag")
        if not (
            {"-p", "--textconv", "--filters"} & set(words) or plain[:1] in [[t] for t in types]
        ):
            return None  # -t, -s, -e: the type, size or existence only
        plain = plain[1:] if plain[:1] in [[t] for t in types] else plain
    for obj in (w for w in plain if ":" not in w and ".." not in w):
        for cwd in cwds:
            if cwd is None:
                return f"git {sub} {obj} can't be checked from a folder the hook can't follow"
            try:
                kind = subprocess.run(  # noqa: S603
                    ["git", "-C", cwd, "cat-file", "-t", f"{obj}^{{}}"],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return f"git {sub} {obj} can't be checked"
            if kind == "blob":
                return f"git {sub} would print a blob by its hash ({obj}), maybe a sealed file"
    return None


def _git_archive(rest: list[str], cwds: list[str | None]) -> str | None:
    """git archive to stdout prints every file of the tree: the reason to block if the tree,
    under the paths given, holds a sealed file. With `-o FILE` it writes a copy: allowed."""
    plain: list[str] = []
    i = 0
    while i < len(rest):
        word = rest[i]
        i += 1
        name = word.split("=", 1)[0]
        if name in ("-o", "--output") or (word.startswith("-o") and not word.startswith("--")):
            return None
        if name == "--remote":
            return "git archive --remote: the hook can't see that tree"
        if word == "--":
            plain += rest[i:]
            break
        if word.startswith("-"):
            i += word in ARCHIVE_VALUES
            continue
        plain.append(word)
    if not plain:
        return None
    for cwd in cwds:
        if cwd is None:
            return "git archive can't be checked from a folder the hook can't follow"
        try:
            names = _git_lines(
                cwd, "ls-tree", "-r", "-z", "--name-only", plain[0], "--", *plain[1:]
            )
        except Unsure:
            return None  # git archive fails as well
        if hit := next((name for name in names if _sealed(name)), None):
            return f"git archive would print a sealed file ({plain[0]}:{hit})"
    return None


def _files(program: str, args: list[str]) -> list[str]:
    """The arguments that may be files: not options and not the pattern or script, plus the
    values of options (`--from-file=x`, a script given with `-f`), which may be files too."""
    patterns, scripts = SCRIPT_OPTIONS.get(program, (frozenset(), frozenset()))
    values = VALUE_OPTIONS.get(program, frozenset())
    plain: list[str] = []
    extra: list[str] = []
    script_given = False
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        name, eq, value = word.partition("=")
        if word == "--":
            plain += args[i:]
            break
        if not word.startswith("-") or word == "-":
            plain.append(word)
            continue
        if name in patterns or name in scripts or name in values:
            script_given |= name not in values
            if not eq and i < len(args):
                value = args[i]
                i += 1
            if name not in patterns:
                extra.append(value)
            continue
        if not word.startswith("--"):  # short options run together: -nf FILE, -fFILE, -e'p'
            flags = patterns | scripts
            at = next((j for j, c in enumerate(word[1:], 1) if f"-{c}" in flags), None)
            if at is not None:
                script_given = True
                value = word[at + 1 :]
                if not value and i < len(args):
                    value = args[i]
                    i += 1
                if f"-{word[at]}" not in patterns:
                    extra.append(value)
                continue
        if eq:
            extra.append(value)  # an option's value may be a file
    files = plain[1:] if program in PATTERN_FIRST and not script_given else plain
    return files + extra


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
        try:
            reason = _grep_tool(tool_input, cwd)
        except Exception:  # a bug in the hook: a Grep reads content, so block it
            reason = "the hook could not read this Grep call"
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
