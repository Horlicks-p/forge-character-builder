"""Danbooru-style tag helpers shared by the Forge UI and the Character Builder.

Everything here is pure Python: no Gradio, no Forge modules. These helpers
only ever touch prompt fragments that this extension generates; they are
never applied to the user's existing txt2img prompt.
"""

from __future__ import annotations

import re
from typing import Iterable

TAG_SEPARATOR = ", "

_EXPLICIT_SEPARATORS = re.compile(r"[,，、;；]+")


def _collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def tag_key(tag: str) -> str:
    """Comparison key for whole-tag equality (case and spacing insensitive)."""
    return _collapse_spaces(tag).casefold()


def split_tags(value: object) -> list[str]:
    """Split an already comma-separated tag string into individual tags.

    Multi-word tags such as ``blue sailor collar`` stay intact. ``None`` and
    empty strings yield an empty list. Lists/tuples are flattened.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        tags: list[str] = []
        for part in value:
            tags.extend(split_tags(part))
        return tags
    text = str(value)
    return [tag for tag in (_collapse_spaces(part) for part in _EXPLICIT_SEPARATORS.split(text)) if tag]


def dedupe_tags(tags: Iterable[str]) -> list[str]:
    """Remove exact duplicate tags, keeping the first occurrence.

    Matching is by whole tag (ignoring case and surrounding spaces), never by
    substring: ``skirt`` and ``pleated skirt`` are different tags.
    """
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        cleaned = _collapse_spaces(str(tag))
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def remove_tags(tags: Iterable[str], blocked: Iterable[str]) -> list[str]:
    """Drop tags whose whole-tag key appears in ``blocked``."""
    blocked_keys = {tag_key(tag) for tag in blocked}
    return [tag for tag in tags if tag_key(tag) not in blocked_keys]


def merge_tags(*groups: object) -> list[str]:
    """Concatenate tag groups in order, skipping empties and duplicates."""
    merged: list[str] = []
    for group in groups:
        merged.extend(split_tags(group))
    return dedupe_tags(merged)


def join_tags(tags: Iterable[str]) -> str:
    """Join tags with ``", "``; never produces empty, doubled or trailing commas."""
    return TAG_SEPARATOR.join(dedupe_tags(tags))
