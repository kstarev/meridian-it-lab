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
