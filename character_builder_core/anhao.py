"""Anhao ai-pose-gallery-zh data access (poses, outfits, legacy portraits).

Moved out of the entry script so the Legacy Pose Gallery and the Character
Builder share one implementation. Behaviour of the legacy helpers is kept;
the dataset root is passed explicitly instead of read from a module global.
Images are never bundled: the user installs the dataset locally.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional

from .assets import safe_join
from .models import KIND_OUTFIT, CatalogItem
from .prompts import format_outfit_keywords, split_tags

POSE_SOURCE = "anhao_pose"
OUTFIT_SOURCE = "anhao_outfit"

LIBRARIES = {
    "Poses": {
        "key": "pose",
        "file": "pose-zh.json",
        "primary": "体系",
        "primary_label": "System",
        "secondary": "分类",
        "secondary_label": "Category",
        "thumb": "缩略图",
        "image": "原图",
    },
    "Outfits": {
        "key": "costume",
        "file": "costume-zh.json",
        "primary": "分类",
        "primary_label": "Category",
        "secondary": None,
        "secondary_label": None,
        "thumb": "缩略图",
        "image": "原图",
    },
    "Portraits": {
        "key": "portrait",
        "file": "portrait-zh.json",
        "primary": "分区",
        "primary_label": "Section",
        "secondary": "发长",
        "secondary_label": "Hair length",
        "thumb": "缩略图",
        "image": "原图",
    },
}

NSFW_POSE_SYSTEMS = {"全身單人・性感系", "全身单人・性感系", "雙人纏綿", "双人缠绵"}
NSFW_COSTUME_CATEGORIES = {"內衣・睡衣", "内衣・睡衣", "性感穿搭"}

VALUE_TRANSLATIONS = {
    "上半身单人": "Single-person upper body",
    "双人缠绵": "Intimate pair",
    "全身单人・性感系": "Single-person full body — sensual",
    "全身单人・标准系": "Single-person full body — standard",
    "可爱系": "Cute",
    "正常位（传教士）": "Missionary position",
    "立位": "Standing position",
    "立位贴身": "Close standing",
    "后入位": "Rear-entry position",
    "后仰・挺背": "Leaning back / arched back",
    "坐位": "Seated position",
    "坐位贴身": "Close seated",
    "坐姿": "Sitting",
    "扭身": "Twisting",
    "侧坐・女孩坐": "Side sitting",
    "侧卧位": "Side-lying position",
    "性感・大胆系": "Sensual / bold",
    "前倾": "Leaning forward",
    "挤胸・强调事业线系": "Emphasized cleavage",
    "背向坐位": "Back-facing seated",
    "贴身": "Close contact",
    "贴脸系": "Face close-up",
    "趴卧": "Lying prone",
    "面对面坐位": "Face-to-face seated",
    "特殊": "Special",
    "站姿": "Standing",
    "骑乘位": "Riding position",
    "跪立": "Kneeling",
    "跪立・四肢着地": "Kneeling / all fours",
    "蹲姿": "Squatting",
    "正面": "Front",
    "仰视": "Low angle",
    "侧面": "Side",
    "俯视": "High angle",
    "斜侧": "Three-quarter",
    "内衣・睡衣": "Lingerie / sleepwear",
    "冬季穿搭": "Winter outfits",
    "性感穿搭": "Sensual outfits",
    "泳装・度假": "Swimwear / resort",
    "春季穿搭": "Spring outfits",
    "派对・礼服": "Party / formalwear",
    "秋季穿搭": "Autumn outfits",
    "夏季穿搭": "Summer outfits",
    "正脸参考": "Face reference",
    "造型氛围": "Styled portrait",
    "中发": "Medium hair",
    "中长发": "Medium-long hair",
    "长发": "Long hair",
    "短发": "Short hair",
}

_MISSING = Path("__missing_asset__")


def english_value(value: object) -> str:
    text = str(value or "").strip()
    return VALUE_TRANSLATIONS.get(text, text)


def find_gallery_root(extension_root: Path, environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    environ = os.environ if environ is None else environ
    candidates: list[Path] = []
    # ANHAO_GALLERY_ROOT is preferred; AI_POSE_GALLERY_ROOT is kept for compatibility.
    configured = (environ.get("ANHAO_GALLERY_ROOT") or environ.get("AI_POSE_GALLERY_ROOT") or "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend([extension_root / "vendor", extension_root.parent / "ai-pose-gallery-zh"])
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / "data" / "pose-zh.json").is_file():
            return candidate
    return None


def find_gallery_candidate(extension_root: Path, environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """A folder that looks like an Anhao install attempt, usable or not.

    Only for diagnostics when ``find_gallery_root`` returns ``None``: the
    configured folder if it exists, ``vendor/`` if it has a ``data/`` folder,
    or a sibling ``ai-pose-gallery-zh/`` folder.
    """
    environ = os.environ if environ is None else environ
    configured = (environ.get("ANHAO_GALLERY_ROOT") or environ.get("AI_POSE_GALLERY_ROOT") or "").strip()
    candidates = [Path(configured)] if configured else []
    vendor = extension_root / "vendor"
    if (vendor / "data").is_dir():
        candidates.append(vendor)
    candidates.append(extension_root.parent / "ai-pose-gallery-zh")
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    return None


@lru_cache(maxsize=12)
def load_library(root: Optional[Path], label: str) -> tuple[dict, ...]:
    if root is None:
        return tuple()
    path = root / "data" / LIBRARIES[label]["file"]
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return tuple()
    return tuple(item for item in data if isinstance(item, dict))


@lru_cache(maxsize=12)
def _index(root: Optional[Path], label: str) -> dict[str, dict]:
    return {str(item.get("id", "")): item for item in load_library(root, label)}


def find_item(root: Optional[Path], label: str, item_id: object) -> Optional[dict]:
    return _index(root, label).get(str(item_id))


def is_nsfw(label: str, item: dict) -> bool:
    if label == "Poses":
        return item.get("体系", "") in NSFW_POSE_SYSTEMS
    if label == "Outfits":
        return item.get("分类", "") in NSFW_COSTUME_CATEGORIES
    return False


def prompt_for(label: str, item: dict) -> str:
    """Legacy Gallery prompt text (not used by the Character Builder)."""
    if label == "Poses":
        angle = english_value(item.get("角度", ""))
        category = english_value(item.get("分类", ""))
        system = english_value(item.get("体系", ""))
        return ", ".join(part for part in [f"{angle} view" if angle else "", category, system, "character pose reference"] if part)

    if label == "Outfits":
        english = format_outfit_keywords(item.get("关键词", ""))
        return ", ".join(part for part in [english, "detailed outfit"] if part)

    section = english_value(item.get("分区", ""))
    hair_length = english_value(item.get("发长", ""))
    return ", ".join(part for part in ["character portrait reference", section, hair_length] if part)


def filtered_items(
    root: Optional[Path],
    label: str,
    query: str,
    primary: str,
    secondary: str,
    safe_mode: bool,
) -> list[dict]:
    config = LIBRARIES[label]
    normalized_query = (query or "").strip().casefold()
    primary = "" if primary in (None, "", "All") else primary
    secondary = "" if secondary in (None, "", "All") else secondary

    results: list[dict] = []
    for item in load_library(root, label):
        if safe_mode and is_nsfw(label, item):
            continue
        if primary and english_value(item.get(config["primary"], "")) != primary:
            continue
        if secondary and config["secondary"] and english_value(item.get(config["secondary"], "")) != secondary:
            continue
        if normalized_query:
            english_fields = " ".join(
                english_value(item.get(field, ""))
                for field in (config["primary"], config["secondary"])
                if field
            )
            haystack = f"{item.get('id', '')} {english_fields} {prompt_for(label, item)}".casefold()
            if normalized_query not in haystack:
                continue
        results.append(item)
    return results


def option_values(root: Optional[Path], label: str, field: Optional[str], safe_mode: bool) -> list[str]:
    if not field:
        return ["All"]
    values = {
        english_value(item.get(field, ""))
        for item in load_library(root, label)
        if item.get(field) and (not safe_mode or not is_nsfw(label, item))
    }
    return ["All", *sorted(values)]


def _under(root: Optional[Path], relative: str) -> Path:
    # Paths come from the dataset JSON; never let them escape the dataset root.
    return safe_join(root, relative) or _MISSING


def depth_path(root: Optional[Path], item: dict) -> Path:
    raw = str(item.get("原图", "")).replace("\\", "/").lstrip("/")
    if raw.startswith("img/"):
        stem = raw[len("img/") :].rsplit(".", 1)[0]
        return _under(root, f"img/depth/{stem}.webp")
    return _under(root, raw)


def asset_path(root: Optional[Path], label: str, item: dict, *, thumbnail: bool, use_depth: bool) -> Path:
    if label == "Poses" and use_depth:
        if thumbnail:
            raw = str(item.get("原图", "")).replace("\\", "/").lstrip("/")
            stem = raw[len("img/") :].rsplit(".", 1)[0] if raw.startswith("img/") else Path(raw).stem
            depth_thumb = _under(root, f"thumbs/depth/{stem}.webp")
            if depth_thumb.is_file():
                return depth_thumb
        return depth_path(root, item)

    config = LIBRARIES[label]
    field = config["thumb"] if thumbnail else config["image"]
    raw = str(item.get(field, "")).replace("\\", "/").lstrip("/")
    return _under(root, raw)


def item_title(label: str, item: dict) -> str:
    item_id = str(item.get("id", "Untitled"))
    if label == "Poses":
        category = english_value(item.get("分类", "Pose"))
        return f"{category} pose"
    if label == "Outfits":
        return f"Outfit {item_id}"
    hair_length = english_value(item.get("发长", ""))
    return f"{hair_length or 'Portrait'} reference"


def caption(label: str, item: dict) -> str:
    return f"{item_title(label, item)}\n#{item.get('id', '')}"


def library_count(root: Optional[Path], label: str) -> int:
    return len(load_library(root, label))


# --- Character Builder providers -------------------------------------------------


def outfit_catalog_item(item: dict) -> CatalogItem:
    """Anhao outfit as a builder item: conservative keyword formatting only,
    without the legacy ", detailed outfit" suffix."""
    item_id = str(item.get("id", ""))
    raw = " ".join(str(item.get("关键词", "")).split())
    tags = split_tags(format_outfit_keywords(raw))
    shown = ", ".join(tags[:3]) + ("…" if len(tags) > 3 else "")
    return CatalogItem(
        source_id=OUTFIT_SOURCE,
        item_id=item_id,
        kind=KIND_OUTFIT,
        category=english_value(item.get("分类", "")) or "Other",
        display_name=f"{item_id} · {shown}" if shown else f"Outfit {item_id}",
        raw_tags=raw,
        normalized_tags=tuple(tags),
        nsfw=is_nsfw("Outfits", item),
        metadata=dict(item),
    )


class AnhaoOutfits:
    """``ItemLookup`` for Anhao outfits (see builder.build_character_prompt)."""

    source_id = OUTFIT_SOURCE

    def __init__(self, root: Optional[Path]):
        self.root = root

    def get(self, item_id: Optional[str]) -> Optional[CatalogItem]:
        if not item_id:
            return None
        item = find_item(self.root, "Outfits", item_id)
        return outfit_catalog_item(item) if item is not None else None
