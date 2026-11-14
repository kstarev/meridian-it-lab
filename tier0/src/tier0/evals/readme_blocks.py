"""Generated README blocks: each sits between its own start and end markers."""


def replace_between(text: str, block: str, start_marker: str, end_marker: str, what: str) -> str:
    """Replace the markers and everything between them with block (which carries its own)."""
    start, end = text.find(start_marker), text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"README has no {what} markers")
    return text[:start] + block + text[end + len(end_marker) :]


def block_between(text: str, start_marker: str, end_marker: str) -> str:
    """The block from its start marker through its end marker."""
    return text[text.index(start_marker) : text.index(end_marker) + len(end_marker)]
