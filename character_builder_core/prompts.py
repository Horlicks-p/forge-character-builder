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


# Conservative tag boundaries for unpunctuated outfit keywords from ai-pose-gallery-zh.
# Keep modifiers (e.g. "pleated mini") with the following garment instead of
# replacing every space with a comma.
OUTFIT_GARMENT_NOUNS = {
    "blouse", "bra", "bracelet", "camisole", "cap", "cardigan", "choker",
    "coat", "corset", "dress", "earrings", "gloves", "hat", "headband",
    "heels", "hoodie", "jacket", "jeans", "jumpsuit", "kimono", "leggings",
    "loafers", "necklace", "overalls", "pajamas", "panties", "pants",
    "pumps", "sandals", "scarf", "shirt", "shoes", "shorts", "skirt",
    "sneakers", "socks", "stockings", "sweater", "swimsuit", "tights",
    "top", "vest", "bag", "backpack", "belt", "boots", "bikini",
    "tie", "uniform", "hairpin",
}
OUTFIT_COLOR_WORDS = {
    "black", "blue", "brown", "cream", "gold", "gray", "green", "grey",
    "ivory", "navy", "orange", "pink", "purple", "red", "silver",
    "white", "yellow", "beige",
}


def format_outfit_keywords(value: object) -> str:
    """Insert tag separators without breaking multi-word clothing names.

    Intended for unpunctuated keyword strings (Anhao outfits). Input that
    already contains explicit separators is only normalized, not re-split.
    """
    raw = " ".join(str(value or "").split())
    if not raw:
        return ""

    # Respect existing tag boundaries. Do not reinterpret prompts already
    # supplied in comma-separated (or Chinese-punctuated) form.
    explicit = [part.strip() for part in _EXPLICIT_SEPARATORS.split(raw) if part.strip()]
    if len(explicit) > 1:
        return ", ".join(explicit)

    words = raw.split()
    tags: list[str] = []
    current: list[str] = []
    for index, token in enumerate(words):
        current.append(token)
        word = token.casefold()
        next_word = words[index + 1].casefold() if index + 1 < len(words) else ""

        # "knit sweater" and "dress shirt" are single garment names.
        if word == "knit":
            boundary = next_word not in {"sweater", "cardigan", "dress", "top", "vest"}
        elif word == "dress" and next_word == "shirt":
            boundary = False
        elif word in {"ribbon", "bow"}:
            # Can be either a separate accessory or a garment modifier.
            boundary = not next_word or next_word in OUTFIT_COLOR_WORDS
        else:
            boundary = word in OUTFIT_GARMENT_NOUNS

        if boundary:
            tags.append(" ".join(current))
            current = []

    if current:
        tags.append(" ".join(current))
    return ", ".join(tags)
