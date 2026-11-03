"""Adapter for CharaDB ``data/items_public.json`` (https://github.com/taki-ttt/charadb).

The metadata (tags / Japanese names) is CC0. Images are NOT redistributable
and are never bundled; users install them from the official release.

Loading never raises: a missing or broken file yields an unavailable
catalog with an English status message, so the rest of the extension keeps
working.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from .models import (
    KIND_BANGS,
    KIND_EYE_COLOR,
    KIND_EYE_SHAPE,
    KIND_HAIR_ACCESSORY,
    KIND_HAIR_COLOR,
    KIND_HAIRSTYLE,
    KIND_OUTFIT,
    KIND_POSE,
    CatalogItem,
    HairstyleVariant,
)
from .prompts import remove_tags, split_tags, tag_key

SOURCE_ID = "charadb"
SOURCE_URL = "https://github.com/taki-ttt/charadb"
ITEMS_FILENAME = "items_public.json"

# English labels for CharaDB categories. The UI is English-only; unknown
# categories fall back to "Other" instead of showing the original text.
CATEGORY_EN = {
    # hair set
    "髪型": "Hairstyle",
    "前髪": "Bangs",
    "髪色": "Hair color",
    "髪飾り": "Hair accessory",
    "目の色": "Eye color",
    "瞳の形": "Eye shape",
    # outfit set
    "制服・学生": "School uniform",
    "カジュアル(春夏)": "Casual (spring/summer)",
    "カジュアル(秋冬)": "Casual (autumn/winter)",
    "きれいめ・オフィス": "Smart / office",
    "ドレス・パーティ": "Dress / party",
    "和服": "Japanese traditional",
    "水着": "Swimwear",
    "スポーツ・部活": "Sports / club",
    "職業・コスプレ": "Occupation / cosplay",
    "ファンタジー": "Fantasy",
    "部屋着・パジャマ": "Loungewear / pajamas",
    "イベント・季節": "Events / seasonal",
    "アイドル・ステージ": "Idol / stage",
    "ゴスロリ・地雷系": "Gothic lolita / jirai-kei",
    "ミリタリー・SF": "Military / sci-fi",
    # pose set (not used by the MVP builder)
    "立ち": "Standing",
    "座り": "Sitting",
    "寝そべり": "Lying down",
    "しゃがみ・膝立ち": "Squatting / kneeling",
    "上半身・手の仕草": "Upper body / hand gestures",
    "動き": "Motion",
    "2人": "Two people",
    "日常の動作": "Everyday actions",
}
EYE_SHAPE_CATEGORY = "瞳の形"

# CharaDB has no adult category. These tags mark the few outfits that Safe
# mode hides, in line with the Anhao "sensual outfits" filter.
NSFW_TAGS = frozenset({"micro bikini", "slingshot swimsuit", "see-through"})

_HAIR_SUB_KINDS = {
    "style": KIND_HAIRSTYLE,
    "bangs": KIND_BANGS,
    "color": KIND_HAIR_COLOR,
    "acc": KIND_HAIR_ACCESSORY,
}


def category_en(category: str) -> str:
    return CATEGORY_EN.get(str(category or "").strip(), "Other")


def find_items_file(extension_root: Path, environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Locate a user-installed ``items_public.json``; ``None`` if absent."""
    environ = os.environ if environ is None else environ
    candidates: list[Path] = []
    configured = str(environ.get("CHARADB_ROOT", "")).strip()
    if configured:
        root = Path(configured).expanduser()
        candidates += [root / "data" / ITEMS_FILENAME, root / ITEMS_FILENAME]
    candidates += [
        extension_root / "vendor" / "charadb" / "data" / ITEMS_FILENAME,
        extension_root / "vendor" / "charadb" / ITEMS_FILENAME,
        extension_root.parent / "charadb" / "data" / ITEMS_FILENAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def parse_items(text: str) -> list[dict]:
    """Parse a JSON array, an ``{"items": [...]}`` object, or JSON Lines."""
    text = text.lstrip("﻿")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        data = data.get("items", [])
    if not isinstance(data, list):
        raise ValueError("Unexpected CharaDB index format")
    return [entry for entry in data if isinstance(entry, dict)]


@dataclass
class CharaDBCatalog:
    available: bool
    message: str
    path: Optional[Path] = None
    items: dict[str, CatalogItem] = field(default_factory=dict)
    by_kind: dict[str, list[CatalogItem]] = field(default_factory=dict)
    variants: dict[str, list[HairstyleVariant]] = field(default_factory=dict)
    variant_parent: dict[str, str] = field(default_factory=dict)
    hair_color_vocab: frozenset[str] = frozenset()
    eye_color_vocab: frozenset[str] = frozenset()
    skipped: int = 0

    source_id: str = SOURCE_ID

    def get(self, item_id: Optional[str]) -> Optional[CatalogItem]:
        if not item_id:
            return None
        return self.items.get(str(item_id))

    def list_kind(self, kind: str, *, safe_mode: bool = False) -> list[CatalogItem]:
        return [item for item in self.by_kind.get(kind, []) if not (safe_mode and item.nsfw)]

    def resolve_hairstyle(self, item_id: Optional[str]) -> tuple[Optional[CatalogItem], Optional[HairstyleVariant]]:
        """Accept a base hairstyle ID or a ``style_color`` variant ID."""
        if not item_id:
            return None, None
        item_id = str(item_id)
        parent_id = self.variant_parent.get(item_id)
        if parent_id is None:
            item = self.items.get(item_id)
            return (item if item and item.kind == KIND_HAIRSTYLE else None), None
        parent = self.items.get(parent_id)
        variant = next((v for v in self.variants.get(parent_id, []) if v.item_id == item_id), None)
        return parent, variant

    def counts(self) -> dict[str, int]:
        return {kind: len(items) for kind, items in self.by_kind.items()}


def _unavailable(message: str, path: Optional[Path] = None) -> CharaDBCatalog:
    return CharaDBCatalog(available=False, message=message, path=path)


def _display_tags(tags: list[str], limit: int = 0) -> str:
    if limit and len(tags) > limit:
        return ", ".join(tags[:limit]) + "…"
    return ", ".join(tags)


def build_catalog(entries: list[dict], path: Optional[Path] = None) -> CharaDBCatalog:
    """Normalize raw CharaDB entries into a catalog."""
    staged: list[tuple[str, dict, list[str]]] = []
    variants: dict[str, list[HairstyleVariant]] = {}
    variant_parent: dict[str, str] = {}
    hair_color_vocab: set[str] = set()
    eye_color_vocab: set[str] = set()
    skipped = 0
    seen_ids: set[str] = set()

    for entry in entries:
        item_id = str(entry.get("id") or "").strip()
        item_set = str(entry.get("set") or "").strip()
        sub = str(entry.get("sub") or "").strip()
        if not item_id or item_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(item_id)
        tags = split_tags(entry.get("tags"))

        if item_set == "hair" and sub == "style_color":
            parent = str(entry.get("parent") or "").strip()
            color = " ".join(str(entry.get("color") or "").split())
            if not parent or not color:
                skipped += 1
                continue
            variants.setdefault(parent, []).append(HairstyleVariant(item_id, color, dict(entry)))
            variant_parent[item_id] = parent
            hair_color_vocab.add(tag_key(color))
            continue

        if item_set == "hair":
            if sub == "eye":
                kind = KIND_EYE_SHAPE if str(entry.get("cat") or "").strip() == EYE_SHAPE_CATEGORY else KIND_EYE_COLOR
            else:
                kind = _HAIR_SUB_KINDS.get(sub)
        elif item_set == "outfit":
            kind = KIND_OUTFIT
        elif item_set == "pose":
            kind = KIND_POSE
        else:
            kind = None

        if kind is None:
            skipped += 1
            continue

        if kind == KIND_HAIR_COLOR:
            hair_color_vocab.update(tag_key(tag) for tag in tags)
        elif kind == KIND_HAIRSTYLE and entry.get("color"):
            hair_color_vocab.add(tag_key(str(entry["color"])))
        elif kind == KIND_EYE_COLOR:
            eye_color_vocab.update(tag_key(tag) for tag in tags)

        staged.append((kind, entry, tags))

    items: dict[str, CatalogItem] = {}
    by_kind: dict[str, list[CatalogItem]] = {}
    for kind, entry, tags in staged:
        item_id = str(entry["id"]).strip()
        normalized = tags
        if kind == KIND_EYE_SHAPE:
            # Eye-shape samples carry an illustrative eye colour ("tareme, brown
            # eyes"); colour comes only from the Eye color field.
            normalized = remove_tags(tags, eye_color_vocab) or tags
        elif kind == KIND_OUTFIT:
            # Some outfits carry hair/eye colours ("..., black hair"); those must
            # not override the character or its LoRA.
            normalized = remove_tags(tags, hair_color_vocab | eye_color_vocab)

        if kind == KIND_OUTFIT:
            display = f"{item_id} · {_display_tags(normalized or tags, 3)}"
        else:
            display = _display_tags(normalized) or item_id

        item = CatalogItem(
            source_id=SOURCE_ID,
            item_id=item_id,
            kind=kind,
            category=category_en(entry.get("cat", "")),
            display_name=display,
            raw_tags=", ".join(tags),
            normalized_tags=tuple(normalized),
            nsfw=any(tag_key(tag) in NSFW_TAGS for tag in tags),
            metadata=dict(entry),
        )
        items[item_id] = item
        by_kind.setdefault(kind, []).append(item)

    counts = {kind: len(values) for kind, values in by_kind.items()}
    message = (
        f"CharaDB: {counts.get(KIND_HAIRSTYLE, 0)} hairstyles, "
        f"{counts.get(KIND_OUTFIT, 0)} outfits, "
        f"{sum(counts.get(k, 0) for k in (KIND_HAIR_COLOR, KIND_BANGS, KIND_HAIR_ACCESSORY, KIND_EYE_COLOR, KIND_EYE_SHAPE))} hair/eye options"
    )
    return CharaDBCatalog(
        available=bool(items),
        message=message if items else "CharaDB index contains no usable items.",
        path=path,
        items=items,
        by_kind=by_kind,
        variants=variants,
        variant_parent=variant_parent,
        hair_color_vocab=frozenset(hair_color_vocab),
        eye_color_vocab=frozenset(eye_color_vocab),
        skipped=skipped,
    )


def load_catalog(path: Optional[Path]) -> CharaDBCatalog:
    """Load CharaDB from ``path``; never raises."""
    if path is None:
        return _unavailable(
            "CharaDB is not installed. Place items_public.json in vendor/charadb/data/ or set CHARADB_ROOT."
        )
    try:
        entries = parse_items(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # json.JSONDecodeError is a ValueError
        return _unavailable(f"CharaDB index could not be read: {error}", Path(path))
    return build_catalog(entries, Path(path))
