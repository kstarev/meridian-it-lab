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
| 2 | Grep | the path is a sealed file or a symlink to one, or the glob names `held_out_`. With a `glob` or `type`, or with no path: the path (the session's folder when none is given) holds a sealed file that the glob or type may let in. See below | Grep with `glob: held_out_*.json`; Grep with `path: dataset, glob: *.json`; Grep with no path in the repository |
| 3 | Bash | a program that prints file content, with a sealed file among its arguments or in an option's value (`--from-file=x`, `sed -f x`). A glob is expanded as zsh would: `**`, brace lists, and zsh's `(a\|b)`, `*(.)` and `<1-3>`. A symlink counts as the file it points to. The programs: `cat head tail less more bat nl tac cut sort uniq sed awk jq diff cmp strings xxd hexdump od zcat gzcat gunzip open code vim vi nano`; encoders and line tools such as `base64 uuencode rev shuf fold fmt pr paste column expand comm join look iconv dd vis curl ex view ed`; compressed files and archives (`zless xzcat gzip tar zip unzip`...); the GNU `g` tools (`gcat gsed`...); and the searches in row 5. For any program: a sealed file fed with `<` (`tr a b < x`, `$(<x)`), a sealed file run as a script by `source`, `.` or a shell, and a sealed file given to a program the hook can't name (`$c x`) | `cat x/held_out_k1.json`, `cat -e x/held_out_k1.json`, `jq . x/held_out_v5.json`, `cat pools/*.json` when the pattern expands to a sealed file, `while read l; do echo $l; done < x/held_out_k1.json` |
| 4 | Bash | `git show`, `git diff`, `git blame`, `git annotate`, `git cat-file`, `git merge-file`, `git archive`, `git format-patch`, `git diff-tree` and the like, or `git log -p` (or `-L`, `-U3`, `--word-diff`...), that names a sealed file. Naming no file: `-a`, `--text`, `--binary` or `--ext-diff` on a command that prints diffs, and `GIT_EXTERNAL_DIFF`, `GIT_ATTR_SOURCE`, `--attr-source` or `-c diff.external` (all print past `.gitattributes`); `git difftool`; `git cat-file --batch`; `git show` or `git cat-file` of a blob given by its hash; `git archive` to stdout of a tree that holds a sealed file; and an alias set with `-c alias.x=...` that does any of this | `git show HEAD:x/held_out_v6.json`, `git log -p --text`, `git cat-file -p 3b18e51` |
| 5 | Bash | a search, unless it is limited to explicit paths, the hook resolves every one, and none is or holds a sealed file. The searches: `grep egrep fgrep rgrep ggrep zgrep zegrep zfgrep bzgrep xzgrep rg ag ack ugrep ug`, `git grep`, and `find -exec` or `xargs` running any program in row 3. See below | `git grep foo`, `rg foo`, `grep -rn foo .`, `grep -rn foo dataset` |

**Quotes.** The hook cuts a Bash command into its parts at each `|`, `||`, `&`, `&&`, `;` and new line, and judges each part. A `|`, `;` or `&&` inside single or double quotes, or after a backslash, is not a cut: `git grep -E "foo|bar" -- tier0/src` is one search. Nor is the `&` or `|` of a redirection (`2>&1`, `&>`, `>|`). A quoted word that holds such a character is judged as a command too, so `bash -c 'ls; cat x/held_out_k1.json'` is still blocked. So are the string after `bash -c`, `sh -c`, `eval` or `env -S`, and the command inside `$(...)` or backticks, unquoted or inside double quotes (`echo "$(cat x)"`): the shell runs both. The lines of a here-document are text, not commands, unless a shell reads them (`bash <<EOF`). A `$(...)` in them runs when the delimiter is not quoted (`<<EOF`, not `<<'EOF'`), so it is judged. A here-string, or an `echo` or `printf` piped to a shell, is judged too. A subshell `( ... )`, a brace list in the program's name (`{cat,x}`), a function's body, and the program after `uv run`, `xcrun`, `arch`, `script`, `taskpolicy` or `sandbox-exec` are judged as the commands they run. When the quotes don't balance, the hook cuts at every such character, quoted or not, as it did before.

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

### The Grep tool (rule 2)

Claude Code's Grep tool runs ripgrep. `.rgignore` leaves sealed files out of a folder, but the tool's `glob` overrides it, as `rg -g` does. So the rule from ruling 3 applies to Grep too, whenever a `glob` or `type` is given, or no path:

- **The path** is resolved from the session's folder, and is that folder when none is given. A path that doesn't exist blocks.
- **The hook walks it,** names only, never content, as in rule 5: it follows every symlink, visits each folder once (so a symlink loop ends), and leaves out a set in `OPENED`.
- **It blocks if any file the glob or type may let in is sealed,** or is a symlink to one. A folder with a sealed name always counts.

How the hook reads a glob, so that it lets in at least what ripgrep does:

- **Only the last part counts.** ripgrep judges a file by its own name: `pools/*.json` lets in what `*.json` does, and a glob that names a folder (`pools`) lets in none of the files below it. A last part of `*` or `**`, or none (`pools/`), lets in any file.
- **The glob is read whole, and split at spaces and commas,** as Claude Code may split it. Braces are expanded: `*.{md,json}` is `*.md` and `*.json`. More than 1,000 globs blocks.
- **A `[...]` class or an escaped character** counts as any one character. **Case doesn't matter.**
- **A glob that starts with `!`** only leaves files out, so it is skipped. Only `!` globs let in any file.
- **A `type`** (`py`, `json`) counts as any file: the hook doesn't know ripgrep's type list. ripgrep's `-t` does not override `.rgignore`, so this is stricter than it needs to be; it is the safe default until the owner rules.

A Grep with a path and neither `glob` nor `type` keeps the rule from before when an `.rgignore` (or `.ignore`) with the line `held_out_*` is in the path's real folder or a folder above it: that file leaves the sealed files out, and only a sealed path, or a symlink to one, blocks. With no such file (a folder outside the repository), the path is walked as with a `glob`.

| Allowed | Blocked, and why |
|---|---|
| `path: tier0, glob: *.py` | `path: dataset, glob: *.json`: it holds sealed pools |
| `path: dataset, glob: *.py` | `glob: *.py` with no path: the repository holds sealed files, or is too big to check |
| `path: tier0/src, type: py` | `path: dataset, type: py`: a type counts as any file |
| `path: dataset` | no path at all, in the repository |
| `path: ., glob: pools` (a folder's name) | `path: ., glob: pools/*` or `held_ou[t]_*` |

Allowed before, blocked now: Grep with no path in a folder that holds a sealed file (the repository root, say), with or without a glob; Grep with a `glob` or `type` whose path holds a sealed file the glob may let in; Grep whose path is a symlink to a sealed file; and Grep whose path doesn't exist when a glob, a type or no path is given.

### What it never blocks

- **Any program that runs code:** `python`, `pytest`, `make`, `uv`, `gh`, a shell script. So the sealed runner, the tests and CI commands are never blocked, and a set's name given to them (`--set held_out_month2`) is fine. CI doesn't run the hook at all: only Claude Code does. Three exceptions, where the shell or a wrapper does the reading: a sealed file fed with `<` (`python x.py < held_out_k1.json`), a sealed file run as a shell script (`bash held_out_k1.json`, `source`), and a reader after `uv run` (`uv run cat ...`).
- **Names, sizes, digests and copies:** `ls`, `git ls-files`, `wc -c`, `shasum`, `cp`, `git add`, `git commit`, `git log` without `-p`.
- **A search limited to paths that don't hold a sealed file:** `git grep foo -- tier0 tools`, `grep -rn foo tier0/src`, `rg foo tier0`.
- **A pattern that is a sealed file's name:** `grep held_out_k1.json docs/sealed-search-guard.md`.
- **A set in `OPENED`,** and `names.py`.
- **`git archive -o FILE`** (a copy, like `cp`), and `git diff`, `git log -p` or `git show` with no option that prints past `.gitattributes`.
- **Glob, Edit, Write and every other tool.** Glob lists file names only, never content, so it may name sealed files and is not blocked; `.claude/settings.json` doesn't even send it to the hook.

### What it misses

- Inline code: `python -c "..."`, `perl -ne`, `ruby -pe`, `node -e`, `osascript`, or a here-document given to one of them, that opens a sealed file. A count-only check is written that way, and the hook can't tell a count from a print. A reader's own script that opens a file by name is code too: `awk '{getline l < "x"}'`, `sed 'r x'`.
- A sealed file that a program which only prints (row 3) reaches through a variable, a `$(...)` result or a script: `cat "$f"`, `for f in x; do cat $f; done`. A search in that shape is blocked, and so is a glob after a `cd` the hook can't follow.
- A search run by a program the hook can't name: `g=grep; $g -rn foo .`.
- A hard link, a symlink or a copy of a sealed file under another name, made by an earlier call or earlier in the same command (`ln -s x y && cat y`), and a sealed file inside an archive (`tar -xOf a.tar`, `unzip -p a.zip`).
- Shell aliases and functions defined in an earlier call, git aliases and settings in `.git/config` (`diff.external`), and attributes in `.git/info/attributes`: the hook judges the words typed, not what an alias or a setting runs.
- A Grep with a folder path and neither `glob` nor `type`, under an `.rgignore`. It relies on `.rgignore`, which goes by names. If ripgrep follows symlinks it doesn't leave out a symlink to a sealed file that has another name, and an `.ignore` file deeper down can let sealed files back in (`!held_out_*`).
- Any shell outside Claude Code.

### The review of 2026-11-20

An adversarial review tried more than 150 crafted calls on throwaway repositories with dummy sealed files. 105 were allowed and printed the dummy text. All of those are blocked now, except the ones listed in "What it misses". Allowed before, blocked now:

- Options that swallowed the file: `cat -e`, `sort -f`, `tail -f`, `xxd -e`, `diff -e`. A file read through an option: `jq -f x`, `sed -f x`, `diff --from-file=x`, `dd if=x`.
- `( cat x )`, `echo "$(cat x)"`, `x="$(grep -rn foo pools)"`, `$(<x)`, `tr a b < x`, `while read l; do ...; done < x`, `cat<x`, `exec 3<x`.
- The programs added to row 3; the wrappers `uv run`, `xcrun`, `arch`, `script`, `env -S`; brace lists and zsh globs; `$'\x63at' x`; `$c x`; `find -exec sh -c 'cat "$1"'`; `source x`, `bash x`, `bash <<EOF`, `sh <<< 'cat x'`, `echo 'cat x' | sh`.
- The git cases in row 4, among them `git log -p --text`, `git diff --text` (even with clean paths), `git archive HEAD`, `git cat-file --batch-all-objects --batch`, `git show <blob hash>` and `git difftool`.
- Grep on a folder with no `.rgignore` above it, and `RIPGREP_CONFIG_PATH` with a search.

### How to turn it off

- **For one session:** start Claude Code with `MERIDIAN_SEALED_GUARD=off` in the environment. The hook then allows everything.
- **For good:** remove the `PreToolUse` entry from `.claude/settings.json`, or revert the commits that added and changed the hook.
- A malformed payload never blocks: the hook exits 0 and prints nothing. A command the hook fails to read blocks only if it holds a search or names `held_out_`.
