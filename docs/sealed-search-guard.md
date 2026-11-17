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

`tools/sealed_guard.py` runs before every Read, Grep and Bash call that Claude Code makes in this repository. It is wired in `.claude/settings.json`. It blocks reads and searches of sealed files, and nothing else. A blocked call gets one line that names the file and says to use `tools/search.py`.

### Exactly what it blocks

| # | Tool | Blocked | Examples |
|---|---|---|---|
| 1 | Read | the file is a sealed file | Read of `.../pools/held_out_v6.json` |
| 2 | Grep | the path is a sealed file, or the glob names `held_out_` | Grep with `glob: held_out_*.json` |
| 3 | Bash | a program that prints file content, with a sealed file among its arguments. The programs: `cat head tail less more bat nl tac cut sort uniq sed awk grep egrep fgrep rg ag jq diff cmp strings xxd hexdump od zcat gzcat gunzip open code vim vi nano` | `cat x/held_out_k1.json`, `jq . x/held_out_v5.json`, `cat pools/*.json` when the pattern expands to a sealed file |
| 4 | Bash | `git show`, `git diff`, `git blame`, `git cat-file` or `git log -p` that names a sealed file | `git show HEAD:x/held_out_v6.json` |
| 5 | Bash | `git grep`, `grep -r` (or `-R`) and `rg --no-ignore` (or `-u`) whose paths cover a sealed file. The hook asks `git ls-files` what the paths cover. No path means the whole folder | `git grep foo`, `git grep foo -- dataset`, `grep -rn foo .` |

### What it never blocks

- **Any program that runs code:** `python`, `pytest`, `make`, `uv`, `gh`, a shell script. So the sealed runner, the tests and CI commands are never blocked. CI doesn't run the hook at all: only Claude Code does.
- **Names, sizes, digests and copies:** `ls`, `git ls-files`, `wc -c`, `shasum`, `cp`, `git add`, `git commit`, `git log` without `-p`.
- **A search that doesn't cover a sealed file:** `git grep foo -- tier0 docs`, `git grep foo -- ':!*held_out_*'`, `grep -rn foo tier0/src`, `rg foo`.
- **A pattern that is a sealed file's name:** `grep held_out_k1.json docs/plans/x.md`.
- **A set in `OPENED`,** and `names.py`.
- **Glob, Edit, Write and every other tool.**

### What it misses

- Inline code: `python -c "..."` or a here-document that opens a sealed file. A count-only check is written that way, and the hook can't tell a count from a print.
- A sealed file reached through `xargs`, a variable or a script.
- Any shell outside Claude Code.

### How to turn it off

- **For one session:** start Claude Code with `MERIDIAN_SEALED_GUARD=off` in the environment. The hook then allows everything.
- **For good:** remove the `PreToolUse` entry from `.claude/settings.json`, or revert the commit that added the hook. The hook, its settings file, its tests and this section are one commit.
- A malformed payload never blocks: the hook exits 0 and prints nothing.
