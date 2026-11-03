"""Normalized data structures shared by all catalog providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

# Item kinds used by the Character Builder.
KIND_HAIRSTYLE = "hairstyle"
KIND_HAIR_COLOR = "hair_color"
KIND_BANGS = "bangs"
KIND_HAIR_ACCESSORY = "hair_accessory"
KIND_EYE_COLOR = "eye_color"
KIND_EYE_SHAPE = "eye_shape"
KIND_OUTFIT = "outfit"
KIND_POSE = "pose"


@dataclass(frozen=True)
class CatalogItem:
    """One selectable item from any source.

    The real primary key is ``(source_id, item_id)``. ``display_name`` and
    ``category`` are English, UI-ready strings; original-language names stay
    in ``metadata``.
    """

    source_id: str
    item_id: str
    kind: str
    category: str
    display_name: str
    raw_tags: str
    normalized_tags: Tuple[str, ...]
    thumbnail: Optional[str] = None
    image: Optional[str] = None
    depth_image: Optional[str] = None
    nsfw: bool = False
    metadata: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_id, self.item_id)


@dataclass(frozen=True)
class HairstyleVariant:
    """A colour variant of a hairstyle (CharaDB ``style_color``)."""

    item_id: str
    color_tag: str
    metadata: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class ItemRef:
    """Reference to a catalog item by source and original ID."""

    source_id: str
    item_id: str


@dataclass(frozen=True)
class BuilderSelection:
    """What the user picked in the Character Builder.

    Stores references (source + ID + variant/mode), never image paths, so the
    prompt and the ControlNet image can always be rebuilt from current data.
    Every field is optional; an empty selection produces an empty prompt.
    """

    hairstyle: Optional[ItemRef] = None
    # Item ID of a CharaDB style_color variant of ``hairstyle`` (e.g. "H001-black").
    hairstyle_variant: Optional[str] = None
    hair_color: Optional[ItemRef] = None
    bangs: Optional[ItemRef] = None
    hair_accessory: Optional[ItemRef] = None
    eye_color: Optional[ItemRef] = None
    eye_shape: Optional[ItemRef] = None
    outfit: Optional[ItemRef] = None
    pose: Optional[ItemRef] = None
    pose_mode: str = "normal"  # "normal" | "depth"
    include_subject: bool = False  # adds "1girl, solo" when explicitly enabled

    def with_changes(self, **changes: Any) -> "BuilderSelection":
        from dataclasses import replace

        return replace(self, **changes)
