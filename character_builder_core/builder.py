"""Assemble the Character Builder prompt fragment from a ``BuilderSelection``.

Order: [subject], hair color, hairstyle, bangs, hair accessory, eye color,
eye shape, outfit. The MVP never emits pose tags (the pose goes to
ControlNet only), never adds quality tags, LoRA or character names, and
never emits a hair colour the user did not explicitly choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol

from .charadb_adapter import CharaDBCatalog
from .models import (
    KIND_BANGS,
    KIND_EYE_COLOR,
    KIND_EYE_SHAPE,
    KIND_HAIR_ACCESSORY,
    KIND_HAIR_COLOR,
    KIND_OUTFIT,
    BuilderSelection,
    CatalogItem,
    ItemRef,
)
from .prompts import dedupe_tags, join_tags

SUBJECT_TAGS = ("1girl", "solo")


class ItemLookup(Protocol):
    def get(self, item_id: Optional[str]) -> Optional[CatalogItem]: ...


@dataclass(frozen=True)
class PromptParts:
    appearance: str
    outfit: str
    final: str
    warnings: tuple[str, ...] = field(default=())


def _lookup(
    ref: Optional[ItemRef],
    expected_kind: str,
    sources: Mapping[str, ItemLookup],
    warnings: list[str],
    label: str,
) -> Optional[CatalogItem]:
    if ref is None:
        return None
    source = sources.get(ref.source_id)
    item = source.get(ref.item_id) if source is not None else None
    if item is None or item.kind != expected_kind:
        warnings.append(f"{label} {ref.item_id} is not available and was skipped.")
        return None
    return item


def build_character_prompt(
    selection: BuilderSelection,
    charadb: CharaDBCatalog,
    *,
    outfit_sources: Optional[Mapping[str, ItemLookup]] = None,
    safe_mode: bool = True,
) -> PromptParts:
    """Build the appendable prompt fragment. Never raises for bad selections."""
    warnings: list[str] = []
    sources: dict[str, ItemLookup] = {charadb.source_id: charadb}
    if outfit_sources:
        sources.update(outfit_sources)

    # Hair colour: explicit Hair Color wins, then an explicit style variant.
    # The hairstyle's own sample colour (CharaDB "color") is never used.
    style_item, variant = (None, None)
    if selection.hairstyle is not None:
        if selection.hairstyle.source_id == charadb.source_id:
            style_item, variant = charadb.resolve_hairstyle(selection.hairstyle.item_id)
        if style_item is None:
            warnings.append(f"Hairstyle {selection.hairstyle.item_id} is not available and was skipped.")
    if style_item is not None and selection.hairstyle_variant:
        _, explicit_variant = charadb.resolve_hairstyle(selection.hairstyle_variant)
        if explicit_variant is not None and charadb.variant_parent.get(explicit_variant.item_id) == style_item.item_id:
            variant = explicit_variant
        else:
            warnings.append(f"Hair variant {selection.hairstyle_variant} does not match the hairstyle and was ignored.")

    hair_color = _lookup(selection.hair_color, KIND_HAIR_COLOR, sources, warnings, "Hair color")
    if hair_color is not None:
        hair_color_tags = list(hair_color.normalized_tags)
    elif variant is not None:
        hair_color_tags = [variant.color_tag]
    else:
        hair_color_tags = []

    bangs = _lookup(selection.bangs, KIND_BANGS, sources, warnings, "Bangs")
    accessory = _lookup(selection.hair_accessory, KIND_HAIR_ACCESSORY, sources, warnings, "Hair accessory")
    eye_color = _lookup(selection.eye_color, KIND_EYE_COLOR, sources, warnings, "Eye color")
    eye_shape = _lookup(selection.eye_shape, KIND_EYE_SHAPE, sources, warnings, "Eye shape")

    outfit = _lookup(selection.outfit, KIND_OUTFIT, sources, warnings, "Outfit")
    if outfit is not None and safe_mode and outfit.nsfw:
        warnings.append(f"Outfit {outfit.item_id} is hidden by Safe mode and was skipped.")
        outfit = None

    appearance_tags: list[str] = []
    if selection.include_subject:
        appearance_tags += SUBJECT_TAGS
    appearance_tags += hair_color_tags
    for item in (style_item, bangs, accessory, eye_color, eye_shape):
        if item is not None:
            appearance_tags += item.normalized_tags

    appearance_tags = dedupe_tags(appearance_tags)
    outfit_tags = dedupe_tags(outfit.normalized_tags) if outfit is not None else []

    return PromptParts(
        appearance=join_tags(appearance_tags),
        outfit=join_tags(outfit_tags),
        final=join_tags([*appearance_tags, *outfit_tags]),
        warnings=tuple(warnings),
    )
