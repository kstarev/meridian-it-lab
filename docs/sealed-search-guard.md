# Searches leave sealed paths out by default

## Why

On 2026-11-17 a helper ran one `git grep` whose paths covered a sealed pool file. It printed no line from the file, and that was checked by count. The rule it broke is that no search touches a sealed path at all. The owner ruled that helper searches leave sealed paths out by default, with a test.

## What counts as sealed

- **Every file whose name starts with `held_out_`.** That is each sealed set, its labels and its referee, wherever it is stored.
- **A set the owner opened as a dev set is not sealed here.** It is listed in `OPENED` in `tools/sealed_paths.py` with its ADR. Today that is `held_out_month1` ([ADR-049](decisions.md)).
- **`dataset/src/meridian_dataset/names.py` is left out of searches too.** It is code, and it also holds blind wording inline. Nothing blocks reading or editing it.

## The defaults, without any hook

1. **`python tools/search.py PATTERN [PATH ...]`** is the search to use, or `make search q='pattern'`.
   - It runs `git grep` with the sealed paths excluded, and says so in one line.
   - It refuses a sealed path given as an argument.
   - `--open held_out_month1` puts an opened dev set back in. A name that is not in `OPENED` is refused.
2. **`.rgignore`** makes ripgrep, and tools built on it, skip the same paths.
3. **`.gitattributes` marks `held_out_*` as `-diff`.** Git then treats those files as binary:
   - `git grep` prints "Binary file ... matches" and no line;
   - `git diff`, `git show` and `git log -p` print "Binary files differ" and no line;
   - GitHub shows no diff for them in a pull request.
4. **`CLAUDE.md`** tells a helper these rules in a few lines.

## What they don't cover

- A program that loads a set through the code and prints it. Checks on a sealed set print counts, ids and enums only, and that stays a rule for people and helpers.
- `cat`, an editor or any other plain read of a sealed file.
- `git grep` still reports that a pattern matches somewhere in a sealed file. Use `tools/search.py`, which doesn't look.
- Blind wording inside other source files. `names.py` is the only one known. A new one is added to `EXTRA_PATHS`.

## The Claude Code hook

`tools/sealed_guard.py` runs before every Read, Grep and Bash call that Claude Code makes in this repository. It is wired in `.claude/settings.json`. It blocks reads and searches of sealed files, and nothing else. A blocked call gets one line that names the file, or says what the hook couldn't check, and says to use `tools/search.py`.

### Exactly what it blocks

| # | Tool | Blocked | Examples |
|---|---|---|---|
| 1 | Read | the file is a sealed file, or a symlink to one. The case of the name doesn't matter: macOS opens `HELD_OUT_V6.JSON` too | Read of `.../pools/held_out_v6.json` |
| 2 | Grep | the path is a sealed file, or the glob names `held_out_` | Grep with `glob: held_out_*.json` |
| 3 | Bash | a program that prints file content, with a sealed file among its arguments. A glob is expanded as zsh would, `**` included, and a symlink counts as the file it points to. The programs: `cat head tail less more bat nl tac cut sort uniq sed awk jq diff cmp strings xxd hexdump od zcat gzcat gunzip open code vim vi nano`, and the searches in row 5 | `cat x/held_out_k1.json`, `jq . x/held_out_v5.json`, `cat pools/*.json` when the pattern expands to a sealed file |
| 4 | Bash | `git show`, `git diff`, `git blame`, `git cat-file` or `git log -p` that names a sealed file | `git show HEAD:x/held_out_v6.json` |
| 5 | Bash | a search, unless it is limited to explicit paths, the hook resolves every one, and none is or holds a sealed file. The searches: `grep egrep fgrep rgrep ggrep zgrep zegrep zfgrep bzgrep xzgrep rg ag ack ugrep ug`, `git grep`, and `find -exec` or `xargs` running any program in row 3. See below | `git grep foo`, `rg foo`, `grep -rn foo .`, `grep -rn foo dataset` |

**Quotes.** The hook cuts a Bash command into its parts at each `|`, `||`, `&`, `&&`, `;` and new line, and judges each part. A `|`, `;` or `&&` inside single or double quotes, or after a backslash, is not a cut: `git grep -E "foo|bar" -- tier0/src` is one search. Nor is the `&` or `|` of a redirection (`2>&1`, `&>`, `>|`). A quoted word that holds such a character is judged as a command too, so `bash -c 'ls; cat x/held_out_k1.json'` is still blocked. So are the string after `bash -c`, `sh -c` or `eval`, and the command inside an unquoted `$(...)` or backticks. The lines of a here-document are text, not commands. When the quotes don't balance, the hook cuts at every such character, quoted or not, as it did before.

### Searches limited to explicit paths (rule 5)

The owner ruled on 2026-11-19 (ruling 3): a search limited to explicit paths is allowed only if the hook resolves every path and none is or holds a sealed file.

How the hook resolves each path:

- **Globs** are expanded as zsh or bash would, `**` included. A `[...]` class counts as any one character, and case and dot files don't matter, so the hook never sees fewer files than the shell. A glob that matches nothing blocks.
- **`~`, `..` and relative paths** are resolved from the command's folder. `cd dir && grep ...` moves that folder. After `cd dir; grep ...`, where the `cd` may have failed, the search is judged from both folders. After a `cd` the hook can't follow (`cd -`, `cd $DIR`), a relative path blocks. `git -C dir` moves the folder for that search.
- **Symlinks** are followed to the real path. A symlink to a sealed file counts as the sealed file.
- **A folder holds a sealed file** if any file below it is sealed, or is a symlink to one. The hook follows every symlink below the folder, even where the search would not. It reads names only, never content.
- **`git grep`** is also checked against what git would read: the tracked files under each path, by git's own pathspec rules (so `'*.json'` matches in every folder), and, for a revision such as `HEAD~1`, that revision's files under each path. A sealed file deleted since then still blocks.

It blocks, when:

- **no path is given:** the search would cover the whole folder. A search that reads a pipe or a redirected file needs no path: `git ls-files | grep held_out`.
- **a path doesn't resolve,** or the command can't be parsed: unbalanced quotes, `$VAR`, `$(...)`, backticks, `{a,b}`, a here-document.
- **an option reaches past the paths or runs a program:** `git grep --no-index`, `--untracked`, `--recurse-submodules` and `-O`; pathspec magic such as `:!` or `:(glob)`; `git --git-dir`; `rg --pre`; a wrapper that changes folder (`env -C`); `GIT_DIR`, `GREP_OPTIONS` and the like.
- **an option is one the hook doesn't know.** Long options must be spelt out in full. `grep` on macOS is ugrep, whose `-J -M -O -Q -z` mean something else in GNU or BSD grep, so those block. `zgrep` and the like read inside archives, so they block. `ag`, `ack` and `ugrep` block with any option.
- **`-f FILE`** would read patterns from a sealed file.
- **`xargs`** hands a reader or search paths the hook can't see, or **`find -exec`** runs one from a start path that holds a sealed file.
- **a folder is too big to check:** more than 300,000 entries, or 5 seconds.

| Allowed | Blocked, and why |
|---|---|
| `grep -rn foo tier0/src` | `grep -rn foo dataset`: it holds sealed pools |
| `grep -n foo tier0/src/tier0/*.py` | `cat dataset/src/meridian_dataset/month/pools/*.json`: the glob matches a sealed file |
| `rg foo tier0 tools` | `rg foo`: no path |
| `git grep -n foo -- tier0 tools` | `git grep foo -- ':!*held_out_*'`: pathspec magic |
| `cd tier0 && grep -rn foo src` | `grep -rn foo tier0/../dataset`: `..` into a folder that holds one |
| `git ls-files \| grep held_out` | `git ls-files \| xargs grep foo`: paths the hook can't see |

Before this ruling, rule 5 asked `git ls-files` what the paths covered. That missed untracked and ignored files, symlinks, folders outside the repository and old revisions, and it let `rg` through unless `--no-ignore` was given, although `rg -g '*.json'` overrides `.rgignore`. All of those now block. `rg foo`, `git grep foo -- ':!*held_out_*'` and a path that doesn't exist were allowed and now block. For a search of the whole repository, `tools/search.py` is still the one to use.

### What it never blocks

- **Any program that runs code:** `python`, `pytest`, `make`, `uv`, `gh`, a shell script. So the sealed runner, the tests and CI commands are never blocked. CI doesn't run the hook at all: only Claude Code does.
- **Names, sizes, digests and copies:** `ls`, `git ls-files`, `wc -c`, `shasum`, `cp`, `git add`, `git commit`, `git log` without `-p`.
- **A search limited to paths that don't hold a sealed file:** `git grep foo -- tier0 tools`, `grep -rn foo tier0/src`, `rg foo tier0`.
- **A pattern that is a sealed file's name:** `grep held_out_k1.json docs/sealed-search-guard.md`.
- **A set in `OPENED`,** and `names.py`.
- **Glob, Edit, Write and every other tool.**

### What it misses

- Inline code: `python -c "..."` or a here-document that opens a sealed file. A count-only check is written that way, and the hook can't tell a count from a print.
- A sealed file that a program which only prints (row 3) reaches through a variable, a `$(...)` result or a script: `cat "$f"`. A search in that shape is blocked.
- A hard link or a copy of a sealed file under another name, and a sealed file inside an archive with another name.
- Shell aliases and functions, and git aliases: the hook judges the words typed, not what an alias runs.
- The Grep tool's `glob`, which overrides `.rgignore` as `rg -g` does. Rule 2 checks only that it doesn't name `held_out_`.
- Any shell outside Claude Code.

### How to turn it off

- **For one session:** start Claude Code with `MERIDIAN_SEALED_GUARD=off` in the environment. The hook then allows everything.
- **For good:** remove the `PreToolUse` entry from `.claude/settings.json`, or revert the commits that added and changed the hook.
- A malformed payload never blocks: the hook exits 0 and prints nothing. A command the hook fails to read blocks only if it holds a search or names `held_out_`.
