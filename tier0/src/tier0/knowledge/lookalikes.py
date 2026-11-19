"""More ways to read the same words, for the two code screens (guide_screen.py and screen.py).

Each screen reads a text as it always has, then again in the forms made here, and fires when any
reading fires. Its patterns are unchanged. So these forms can only make a screen fire more, never
less. A form here is lower case, with no accents and punctuation kept, like the screens' own fold,
and also:

- NFKD comes before lower case, so letters that NFKD turns into capitals (mathematical bold
  capitals, U+1D400 and on) are lower-cased too;
- a letter of another script that looks like a Latin one (Cyrillic small o, Greek capital
  omicron) is read as that letter, and a few Latin letters with no decomposition (o with a
  stroke, l with a stroke, sharp s) as plain ones;
- a character that is not shown (zero-width space or joiner, soft hyphen, word joiner, direction
  marks) is removed, so one inside a word does not split it. A second form reads it as a space,
  for one put between two words.

Written from the gaps the property tests found (docs/evals/2026-11-18-safety-mutation-testing.md),
not from any sealed set. The letters are written as code points, so the source shows none.
"""

import unicodedata


def _pairs(code_points: tuple[int, ...], latin: str) -> dict[int, str]:
    return dict(zip(code_points, latin, strict=True))


# Look-alikes of Latin letters, by case where the two cases look different (Greek capital eta is
# "H", small eta is "n"). From Unicode's confusables list, for letters only.
# fmt: off
_LOOKALIKES = {
    # Cyrillic capitals.
    **_pairs(
        (0x410, 0x412, 0x415, 0x41A, 0x41C, 0x41D, 0x41E, 0x420, 0x421, 0x422,
         0x423, 0x425, 0x405, 0x406, 0x408, 0x51A, 0x51C, 0x4BA, 0x4C0, 0x4AE),
        "ABEKMHOPCTYXSIJQWHIY",
    ),
    # Cyrillic small letters.
    **_pairs(
        (0x430, 0x435, 0x43E, 0x440, 0x441, 0x443, 0x445, 0x455, 0x456,
         0x458, 0x501, 0x51B, 0x51D, 0x4BB, 0x4CF, 0x4AF, 0x43A),
        "aeopcyxsijdqwhlyk",
    ),
    # Greek capitals.
    **_pairs(
        (0x391, 0x392, 0x395, 0x396, 0x397, 0x399, 0x39A, 0x39C,
         0x39D, 0x39F, 0x3A1, 0x3A4, 0x3A5, 0x3A7, 0x3F9, 0x37F),
        "ABEZHIKMNOPTYXCJ",
    ),
    # Greek small letters.
    **_pairs(
        (0x3B1, 0x3BF, 0x3C1, 0x3B9, 0x3B7, 0x3BA, 0x3BD, 0x3C5, 0x3C7, 0x3B3, 0x3F2, 0x3F3),
        "aopinkvuxycj",
    ),
    # Armenian small letters.
    **_pairs((0x585, 0x57D, 0x570, 0x578), "ouhn"),
    # Latin letters that NFKD leaves as they are: dotless i and j, script a and g, iota, and
    # o, d, l, h, t, b with a stroke, small and capital.
    **_pairs(
        (0x131, 0x237, 0x251, 0x261, 0x269, 0xF8, 0x111, 0x142,
         0x127, 0x167, 0x180, 0xD8, 0x110, 0x141, 0x126, 0x166),
        "ijagiodlhtbODLHT",
    ),
    0xE6: "ae", 0xC6: "AE", 0x153: "oe", 0x152: "OE",
}
# fmt: on
_TABLE = str.maketrans(_LOOKALIKES)


def _hidden(c: str) -> bool:
    """A character that is not shown on its own: a format character or a mark."""
    return unicodedata.category(c) in ("Cf", "Mn", "Me") or unicodedata.combining(c) != 0


def forms(text: str) -> tuple[str, ...]:
    """The text folded two more ways: hidden characters removed, and read as spaces. Casefold,
    unlike lower case, also turns sharp s into "ss"."""
    text = unicodedata.normalize("NFKD", text).translate(_TABLE)
    text = unicodedata.normalize("NFKD", text.casefold()).translate(_TABLE)
    joined = "".join("" if _hidden(c) else c for c in text)
    spaced = "".join(
        " " if unicodedata.category(c) == "Cf" else "" if _hidden(c) else c for c in text
    )
    return tuple(dict.fromkeys((joined, spaced)))
