"""Human-readable before/after text for an edited video (title, description, tags).

Pure functions, no I/O. ``None`` for an "old" or "new" value means *unknown* (for
example no earlier snapshot exists), and an unknown field is never reported as changed.
"""

from __future__ import annotations

import difflib
from typing import Sequence

MAX_LINE = 200            # characters kept per diff line
MAX_DESC_BLOCK = 1500     # characters of description diff shown
MAX_TAG_LIST = 400        # characters per Added/Removed tag list
MAX_TAG_ITEMS = 60


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _normalise(text: str) -> str:
    return "\n".join(_lines(text)).strip()


def title_change(old: str | None, new: str | None) -> str:
    if not old or new is None or old == new:
        return ""
    return f"Title changed:\n~~{old}~~\n\u2192 **{new}**"


def description_change(old: str | None, new: str | None) -> str:
    """Only the changed lines, as a ```diff block (`-` before, `+` after)."""
    if old is None or new is None or _normalise(old) == _normalise(new):
        return ""
    rows: list[str] = []
    old_lines, new_lines = _lines(old), _lines(new)
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        # Never let a description close our code fence early.
        rows.append(_clip(line.replace("```", "`\u200b``"), MAX_LINE))

    shown: list[str] = []
    used = 0
    for i, row in enumerate(rows):
        if used + len(row) + 1 > MAX_DESC_BLOCK:
            shown.append(f"\u2026 ({len(rows) - i} more changed lines)")
            break
        shown.append(row)
        used += len(row) + 1
    return "Description changed:\n```diff\n" + "\n".join(shown) + "\n```"


def tags_change(old: Sequence[str] | None, new: Sequence[str] | None) -> str:
    if old is None or new is None:
        return ""
    added = [t for t in new if t not in old]
    removed = [t for t in old if t not in new]
    if not added and not removed:
        return ""

    def fmt(items: list[str]) -> str:
        shown = ", ".join(items[:MAX_TAG_ITEMS])
        if len(items) > MAX_TAG_ITEMS:
            shown += f", \u2026 (+{len(items) - MAX_TAG_ITEMS} more)"
        return _clip(shown, MAX_TAG_LIST)

    parts = ["Tags changed:"]
    if removed:
        parts.append(f"Before (removed): ~~{fmt(removed)}~~")
    if added:
        parts.append(f"After (added): **{fmt(added)}**")
    return "\n".join(parts)


def describe_changes(
    *,
    old_title: str | None, new_title: str | None,
    old_description: str | None = None, new_description: str | None = None,
    old_tags: Sequence[str] | None = None, new_tags: Sequence[str] | None = None,
) -> str:
    """All detected differences, separated by blank lines; "" if none were detected."""
    parts = [
        title_change(old_title, new_title),
        description_change(old_description, new_description),
        tags_change(old_tags, new_tags),
    ]
    return "\n\n".join(p for p in parts if p)
