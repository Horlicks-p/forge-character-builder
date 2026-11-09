"""Character Builder session logic: galleries, choices, prompt refresh and
send preflight. Pure Python so every rule is testable without Gradio.

Selections are stored as ``BuilderSelection`` (source + ID + mode); image
paths are always resolved at use time, never kept in UI state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import anhao, custom_poses, sources
from .assets import PLACEHOLDER_IMAGE
from .builder import build_character_prompt
from .charadb_adapter import SOURCE_ID as CHARADB_SOURCE
from .charadb_adapter import CharaDBCatalog
from .models import (
    KIND_HAIR_COLOR,
    KIND_HAIRSTYLE,
    KIND_OUTFIT,
    BuilderSelection,
    CatalogItem,
    ItemRef,
)

PAGE_SIZE = 48
ALL = "All"
NONE_LABEL = "None"

STATUS_POSE_SENT = "Pose sent to ControlNet Unit 1"
STATUS_CONTROLNET_UNAVAILABLE = "ControlNet unavailable"
STATUS_MISSING_IMAGE = "Missing reference image"
MSG_EMPTY = "No custom poses yet. Add a local image to get started."
MSG_EMPTY_BUILDER = "No custom poses yet. Add one in Pose Gallery, under Custom Poses."
MSG_NO_MATCH = "No custom poses match the current search."
MSG_ALL_HIDDEN = "No custom poses to show. Safe Mode may be hiding them."
CAPTION_LENGTH = 32
EDITED_NOTICE = "Final Prompt was edited manually. Press **Reset from selection** to regenerate it."
STALE_TAGS_NOTICE = (
    "Final Prompt was edited manually and kept as is, so it may still contain tags from the cleared outfit. "
    "Press **Reset from selection** to rebuild it, or remove them by hand."
)

OUTFIT_SOURCE_LABELS = {
    CHARADB_SOURCE: "CharaDB (Anime)",
    anhao.OUTFIT_SOURCE: "Anhao (Original)",
}


class SendError(Exception):
    """A send preflight failed; the message is shown to the user as-is."""


@dataclass
class BuilderContext:
    charadb: CharaDBCatalog
    anhao_root: Optional[Path]
    placeholder: Path = PLACEHOLDER_IMAGE
    # Install attempt rejected by find_gallery_root (diagnostics only).
    anhao_candidate: Optional[Path] = None
    # Shared with the Pose Gallery tab; absent only in tests of other sources.
    custom_store: Optional[custom_poses.CustomPoseStore] = None

    @property
    def poses_available(self) -> bool:
        return anhao.library_count(self.anhao_root, "Poses") > 0

    def outfit_sources(self) -> dict[str, anhao.AnhaoOutfits]:
        """Extra outfit providers for ``build_character_prompt``."""
        return {anhao.OUTFIT_SOURCE: anhao.AnhaoOutfits(self.anhao_root)} if self.anhao_root else {}

    def outfit_source_choices(self, show_anhao: bool = True) -> list[tuple[str, str]]:
        choices = []
        if self.charadb.available:
            choices.append((OUTFIT_SOURCE_LABELS[CHARADB_SOURCE], CHARADB_SOURCE))
        if show_anhao and anhao.library_count(self.anhao_root, "Outfits"):
            choices.append((OUTFIT_SOURCE_LABELS[anhao.OUTFIT_SOURCE], anhao.OUTFIT_SOURCE))
        return choices

    def pose_source_choices(self) -> list[tuple[str, str]]:
        choices = [("Anhao", anhao.POSE_SOURCE)]
        if self.custom_store is not None:
            choices.append(("Custom Poses", custom_poses.SOURCE_ID))
        return choices

    def default_pose_source(self) -> str:
        return anhao.POSE_SOURCE if self.poses_available or self.custom_store is None else custom_poses.SOURCE_ID

    def source_health(self) -> list[sources.SourceHealth]:
        rows = [sources.charadb_health(self.charadb), sources.anhao_health(self.anhao_root, candidate=self.anhao_candidate)]
        if self.custom_store is not None:
            rows.append(sources.custom_pose_health(self.custom_store))
        return rows

    def hairstyle_hint(self) -> str:
        """Empty-state text for Step 1; ``""`` when CharaDB is usable."""
        if self.charadb.available:
            return ""
        return "**No hair or eye options.** " + sources.CHARADB_HINT

    def outfit_hint(self, show_anhao: bool = True) -> str:
        """Empty-state text for Step 2; ``""`` when an outfit source exists."""
        if self.outfit_source_choices(show_anhao):
            return ""
        text = "**No outfit source is available.** " + sources.CHARADB_HINT
        if not show_anhao and anhao.library_count(self.anhao_root, "Outfits"):
            text += " Anhao outfits are installed but hidden: enable them in *Settings → Forge Character Builder*."
        return text

    def pose_hint(self) -> str:
        """Empty-state text for Step 3; ``""`` when poses are installed."""
        if self.poses_available:
            return ""
        if self.custom_store is not None and self.custom_store.status().state != custom_poses.STATUS_UNAVAILABLE:
            return "**Anhao poses are not installed.** You can use **Custom Poses** instead. " + sources.ANHAO_HINT
        return "**No poses.** Send Pose and Send All need the Anhao dataset. " + sources.ANHAO_HINT


@dataclass(frozen=True)
class GalleryPage:
    images: list[tuple[str, str]]
    ids: list[str]
    page: int
    total_pages: int
    total: int
    message: str = ""  # replaces the page count, e.g. why the page is empty

    @property
    def info(self) -> str:
        return self.message or f"Page {self.page} of {self.total_pages} · {self.total:,} items"


@dataclass(frozen=True)
class PosePageState:
    """The Step 3 page on screen. ``refs`` line up with its cards, so a pick
    never depends on which source the radio shows by then."""

    source_id: str = ""
    revision: int = -1
    refs: tuple[ItemRef, ...] = ()


def _paginate(items: list, page: object) -> tuple[list, int, int]:
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    return items[start : start + PAGE_SIZE], page, total_pages


def _matches(query: str, *fields: object) -> bool:
    query = (query or "").strip().casefold()
    return not query or query in " ".join(str(f or "") for f in fields).casefold()


def _caption(item: CatalogItem) -> str:
    return f"{item.display_name}\n#{item.item_id}"


# --- choices -----------------------------------------------------------------


def dropdown_choices(ctx: BuilderContext, kind: str) -> list[tuple[str, str]]:
    """``[("None", ""), (label, item_id), ...]`` for the small CharaDB sets."""
    return [(NONE_LABEL, "")] + [(item.display_name, item.item_id) for item in ctx.charadb.list_kind(kind)]


def ref_or_none(source_id: str, item_id: object) -> Optional[ItemRef]:
    item_id = str(item_id or "").strip()
    return ItemRef(source_id, item_id) if item_id else None


# --- galleries ---------------------------------------------------------------


def hairstyle_page(ctx: BuilderContext, query: str, page: object, hair_color_id: Optional[str] = None) -> GalleryPage:
    styles = [
        item
        for item in ctx.charadb.list_kind(KIND_HAIRSTYLE)
        if _matches(query, item.item_id, item.display_name, item.category)
    ]
    color = ctx.charadb.get(hair_color_id) if hair_color_id else None
    color_tags = color.normalized_tags if color is not None and color.kind == KIND_HAIR_COLOR else ()
    page_items, page, total_pages = _paginate(styles, page)
    images = []
    for item in page_items:
        thumb = None
        variant = ctx.charadb.variant_for_color(item.item_id, color_tags) if color_tags else None
        if variant is not None:
            thumb = ctx.charadb.thumbnail(variant.item_id)
        thumb = thumb or ctx.charadb.thumbnail(item.item_id) or ctx.placeholder
        images.append((str(thumb), _caption(item)))
    return GalleryPage(images, [i.item_id for i in page_items], page, total_pages, len(styles))


def character_option_page(ctx: BuilderContext, kind: str) -> GalleryPage:
    """Small CharaDB sets rendered as image choices instead of dropdowns."""
    items = ctx.charadb.list_kind(kind)
    images = [
        (str(ctx.charadb.thumbnail(item.item_id) or ctx.placeholder), _caption(item))
        for item in items
    ]
    return GalleryPage(images, [item.item_id for item in items], 1, 1, len(items))


def outfit_items(ctx: BuilderContext, source: str, category: str, query: str, safe_mode: bool) -> list[CatalogItem]:
    if source == CHARADB_SOURCE:
        items = ctx.charadb.list_kind(KIND_OUTFIT, safe_mode=safe_mode)
    elif source == anhao.OUTFIT_SOURCE:
        items = [anhao.outfit_catalog_item(raw) for raw in anhao.load_library(ctx.anhao_root, "Outfits")]
        items = [item for item in items if not (safe_mode and item.nsfw)]
    else:
        return []
    category = "" if category in (None, "", ALL) else category
    return [
        item
        for item in items
        if (not category or item.category == category)
        and _matches(query, item.item_id, item.display_name, item.raw_tags, item.category)
    ]


def outfit_categories(ctx: BuilderContext, source: str, safe_mode: bool) -> list[str]:
    return [ALL, *sorted({item.category for item in outfit_items(ctx, source, ALL, "", safe_mode)})]


def _outfit_thumb(ctx: BuilderContext, item: CatalogItem) -> Path:
    if item.source_id == CHARADB_SOURCE:
        return ctx.charadb.thumbnail(item.item_id) or ctx.placeholder
    path = anhao.asset_path(ctx.anhao_root, "Outfits", item.metadata, thumbnail=True, use_depth=False)
    return path if path.is_file() else ctx.placeholder


def outfit_page(ctx: BuilderContext, source: str, category: str, query: str, safe_mode: bool, page: object) -> GalleryPage:
    items = outfit_items(ctx, source, category, query, safe_mode)
    page_items, page, total_pages = _paginate(items, page)
    images = [(str(_outfit_thumb(ctx, item)), _caption(item)) for item in page_items]
    return GalleryPage(images, [i.item_id for i in page_items], page, total_pages, len(items))


def pose_filter_options(ctx: BuilderContext, safe_mode: bool) -> tuple[list[str], list[str]]:
    config = anhao.LIBRARIES["Poses"]
    return (
        anhao.option_values(ctx.anhao_root, "Poses", config["primary"], safe_mode),
        anhao.option_values(ctx.anhao_root, "Poses", config["secondary"], safe_mode),
    )


def pose_page(
    ctx: BuilderContext,
    query: str,
    primary: str,
    secondary: str,
    safe_mode: bool,
    depth: bool,
    page: object,
) -> GalleryPage:
    items = anhao.pose_group_representatives(
        anhao.filtered_items(ctx.anhao_root, "Poses", query, primary, secondary, safe_mode)
    )
    page_items, page, total_pages = _paginate(items, page)
    images = []
    for item in page_items:
        thumb = anhao.asset_path(ctx.anhao_root, "Poses", item, thumbnail=True, use_depth=depth)
        images.append((str(thumb if thumb.is_file() else ctx.placeholder), anhao.pose_group_caption(ctx.anhao_root, item)))
    return GalleryPage(images, [str(i.get("id", "")) for i in page_items], page, total_pages, len(items))


def _custom_caption(name: str) -> str:
    """Captions overlay the thumbnail, so long names stop short of covering it."""
    return name if len(name) <= CAPTION_LENGTH else f"{name[:CAPTION_LENGTH - 1]}…"


def _custom_empty_message(store: custom_poses.CustomPoseStore, query: str, empty_text: str) -> str:
    status = store.status()
    if status.state != custom_poses.STATUS_OK:
        return status.message
    if (query or "").strip():
        return MSG_NO_MATCH
    return MSG_ALL_HIDDEN if status.count else empty_text


def custom_pose_gallery(
    store: custom_poses.CustomPoseStore,
    query: str,
    safe_mode: bool,
    page: object,
    *,
    placeholder: Path = PLACEHOLDER_IMAGE,
    empty_text: str = MSG_EMPTY,
) -> GalleryPage:
    items = store.items(query, bool(safe_mode))
    page_items, page, total_pages = _paginate(items, page)
    images = []
    for item in page_items:
        thumb = store.thumbnail_path(item.id)
        images.append((str(thumb if thumb is not None and thumb.is_file() else placeholder), _custom_caption(item.name)))
    message = "" if items else _custom_empty_message(store, query, empty_text)
    return GalleryPage(images, [item.id for item in page_items], page, total_pages, len(items), message)


def pose_gallery(
    ctx: BuilderContext,
    source: str,
    query: str,
    primary: str,
    secondary: str,
    safe_mode: bool,
    depth: bool,
    page: object,
) -> tuple[GalleryPage, PosePageState]:
    """One page of either source, with the state describing exactly that page."""
    # Read first: a change during the listing must look newer, never older.
    revision = custom_poses.revision()
    if source != custom_poses.SOURCE_ID:
        result = pose_page(ctx, query, primary, secondary, safe_mode, depth, page)
    elif ctx.custom_store is None:
        result = GalleryPage([], [], 1, 1, 0)
    else:
        result = custom_pose_gallery(ctx.custom_store, query, safe_mode, page, placeholder=ctx.placeholder, empty_text=MSG_EMPTY_BUILDER)
    return result, PosePageState(source, revision, tuple(ItemRef(source, item_id) for item_id in result.ids))


# --- lookups / summaries ------------------------------------------------------


def outfit_item(ctx: BuilderContext, ref: Optional[ItemRef]) -> Optional[CatalogItem]:
    if ref is None:
        return None
    if ref.source_id == CHARADB_SOURCE:
        item = ctx.charadb.get(ref.item_id)
        return item if item is not None and item.kind == KIND_OUTFIT else None
    source = ctx.outfit_sources().get(ref.source_id)
    return source.get(ref.item_id) if source is not None else None


def pose_record(ctx: BuilderContext, ref: Optional[ItemRef]) -> Optional[dict]:
    if ref is None or ref.source_id != anhao.POSE_SOURCE:
        return None
    return anhao.find_item(ctx.anhao_root, "Poses", ref.item_id)


def custom_pose_record(ctx: BuilderContext, ref: Optional[ItemRef]) -> Optional[custom_poses.CustomPose]:
    if ref is None or ref.source_id != custom_poses.SOURCE_ID or ctx.custom_store is None:
        return None
    return ctx.custom_store.get(ref.item_id)


def custom_pose_problem(ctx: BuilderContext, ref: Optional[ItemRef], safe_mode: bool) -> str:
    """Why a Custom Pose reference cannot be used now; ``""`` when it can, or is not one."""
    if ref is None or ref.source_id != custom_poses.SOURCE_ID:
        return ""
    record = custom_pose_record(ctx, ref)
    if record is None:
        return custom_poses.ERROR_NO_ITEM
    return custom_poses.ERROR_HIDDEN if safe_mode and record.sensitive else ""


def pose_details(ctx: BuilderContext, selection: BuilderSelection) -> str:
    """Plain text for a selected Custom Pose, which may hold a user-written name."""
    record = custom_pose_record(ctx, selection.pose)
    if record is None:
        return ""
    return f"{record.name}\nType: {record.input_label}\nPreprocessor: {record.preprocessor}"


def pose_angle_choices(ctx: BuilderContext, selection: BuilderSelection) -> list[tuple[str, str]]:
    """Available angles and their exact source record IDs for the selected pose."""
    record = pose_record(ctx, selection.pose)
    if record is None:
        return []
    return [
        (anhao.english_value(item.get("角度", "")) or str(item.get("id", "")), str(item.get("id", "")))
        for item in anhao.pose_views(ctx.anhao_root, record)
    ]


def select_pose_angle(ctx: BuilderContext, selection: BuilderSelection, item_id: object) -> BuilderSelection:
    """Only accept an angle belonging to the currently selected logical pose."""
    selected_id = str(item_id or "")
    if selected_id and selected_id in {value for _, value in pose_angle_choices(ctx, selection)}:
        return selection.with_changes(pose=ItemRef(anhao.POSE_SOURCE, selected_id))
    return selection


def hairstyle_summary(ctx: BuilderContext, selection: BuilderSelection) -> str:
    if selection.hairstyle is None:
        return "Hairstyle: *none*"
    style, _ = ctx.charadb.resolve_hairstyle(selection.hairstyle.item_id)
    if style is None:
        return f"Hairstyle: *unavailable* (#{selection.hairstyle.item_id})"
    return f"Hairstyle: **{style.display_name}** (#{style.item_id})"


def outfit_summary(ctx: BuilderContext, selection: BuilderSelection) -> tuple[str, str]:
    """(summary markdown, raw tags) for the selected outfit."""
    if selection.outfit is None:
        return "Outfit: *none*", ""
    item = outfit_item(ctx, selection.outfit)
    if item is None:
        return f"Outfit: *unavailable* (#{selection.outfit.item_id})", ""
    label = OUTFIT_SOURCE_LABELS.get(item.source_id, item.source_id)
    return f"Outfit: **#{item.item_id}** · {item.category} · {label}", item.raw_tags


def pose_mode_label(depth: bool) -> str:
    return "Depth (preprocessor None)" if depth else "Normal (preprocessor canny)"


def _custom_pose_summary(ctx: BuilderContext, ref: ItemRef) -> tuple[str, Optional[str]]:
    # Markdown, so the user-written name stays out; see pose_details().
    record = custom_pose_record(ctx, ref)
    if record is None:
        return f"Pose: *unavailable* (#{ref.item_id})", None
    thumb = ctx.custom_store.thumbnail_path(record.id)
    preview = thumb if thumb is not None and thumb.is_file() else ctx.placeholder
    return f"Pose: #{record.id} · {record.input_label} · preprocessor {record.preprocessor}", str(preview)


def pose_summary(ctx: BuilderContext, selection: BuilderSelection) -> tuple[str, Optional[str]]:
    """(summary markdown, preview thumbnail path) for the selected pose."""
    record = pose_record(ctx, selection.pose)
    if selection.pose is None:
        return "Pose: *none* — select a pose to enable **Send Pose** and **Send All**.", None
    if selection.pose.source_id == custom_poses.SOURCE_ID:
        return _custom_pose_summary(ctx, selection.pose)
    if record is None:
        return f"Pose: *unavailable* (#{selection.pose.item_id})", None
    depth = selection.pose_mode == "depth"
    thumb = anhao.asset_path(ctx.anhao_root, "Poses", record, thumbnail=True, use_depth=depth)
    angle = anhao.english_value(record.get("角度", ""))
    number = str(record.get("姿势编号") or record.get("id", ""))
    summary = f"Pose: **{anhao.item_title('Poses', record)}** ({number} / #{selection.pose.item_id}) · {angle} · {pose_mode_label(depth)}"
    return summary, (str(thumb) if thumb.is_file() else str(ctx.placeholder))


# --- prompt refresh -----------------------------------------------------------


@dataclass(frozen=True)
class PromptUpdate:
    appearance: str
    outfit: str
    final: str
    last_generated: str
    notice: str
    warnings: tuple[str, ...] = field(default=())


def regenerate(
    ctx: BuilderContext,
    selection: BuilderSelection,
    final_text: Optional[str],
    last_generated: Optional[str],
    safe_mode: bool,
    *,
    force: bool = False,
) -> PromptUpdate:
    """Rebuild the prompt; keep a manually edited Final Prompt unless ``force``.

    The Final Prompt counts as edited when it differs from the last text this
    builder generated.
    """
    parts = build_character_prompt(selection, ctx.charadb, outfit_sources=ctx.outfit_sources(), safe_mode=safe_mode)
    edited = (final_text or "") != (last_generated or "")
    if force or not edited:
        final, notice = parts.final, ""
    else:
        final, notice = final_text or "", EDITED_NOTICE
    return PromptUpdate(parts.appearance, parts.outfit, final, parts.final, notice, parts.warnings)


def is_edited(final_text: Optional[str], last_generated: Optional[str]) -> bool:
    return (final_text or "") != (last_generated or "")


def edited_notice(final_text: Optional[str], last_generated: Optional[str]) -> str:
    return EDITED_NOTICE if is_edited(final_text, last_generated) else ""


def change_outfit_source(
    selection: BuilderSelection, source: Optional[str], *, prompt_edited: bool = False
) -> tuple[BuilderSelection, list[str]]:
    """Drop an outfit picked from another provider when the source changes.

    The Final Prompt itself is left to ``regenerate`` (dirty-state rules);
    an edited prompt only gets a notice that old tags may remain.
    """
    current = selection.outfit
    if current is None or current.source_id == (source or ""):
        return selection, []
    label = OUTFIT_SOURCE_LABELS.get(current.source_id, current.source_id)
    notices = [f"Outfit #{current.item_id} ({label}) was cleared because the outfit source changed."]
    if prompt_edited:
        notices.append(STALE_TAGS_NOTICE)
    return selection.with_changes(outfit=None), notices


def change_pose_source(selection: BuilderSelection, source: Optional[str]) -> tuple[BuilderSelection, list[str]]:
    """Drop a pose picked from another source. Poses emit no tags, so the prompt is untouched."""
    current = selection.pose
    if current is None or current.source_id == (source or ""):
        return selection, []
    return selection.with_changes(pose=None), [f"Pose #{current.item_id} was cleared because the pose source changed."]


def reconcile_pose(ctx: BuilderContext, selection: BuilderSelection, safe_mode: bool) -> tuple[BuilderSelection, list[str]]:
    """Clear a Custom Pose that was deleted or became hidden since it was picked."""
    problem = custom_pose_problem(ctx, selection.pose, safe_mode)
    if not problem:
        return selection, []
    return selection.with_changes(pose=None), [f"Pose #{selection.pose.item_id} was cleared: {problem}."]


def apply_safe_mode(
    ctx: BuilderContext, selection: BuilderSelection, safe_mode: bool, *, prompt_edited: bool = False
) -> tuple[BuilderSelection, list[str]]:
    """Clear selections that Safe mode hides, so they can never be sent.

    With ``prompt_edited`` the kept Final Prompt may still hold the cleared
    outfit's tags, so a notice says so instead of implying they are gone.
    Poses emit no tags (MVP), so clearing a pose needs no such notice.
    """
    if not safe_mode:
        return selection, []
    notices: list[str] = []
    outfit = outfit_item(ctx, selection.outfit)
    if outfit is not None and outfit.nsfw:
        selection = selection.with_changes(outfit=None)
        notices.append(f"Outfit #{outfit.item_id} was cleared because Safe mode hides it.")
        if prompt_edited:
            notices.append(STALE_TAGS_NOTICE)
    record = pose_record(ctx, selection.pose)
    if record is not None and anhao.is_nsfw("Poses", record):
        notices.append(f"Pose #{selection.pose.item_id} was cleared because Safe mode hides it.")
        selection = selection.with_changes(pose=None)
    custom = custom_pose_record(ctx, selection.pose)
    if custom is not None and custom.sensitive:
        notices.append(f"Pose #{custom.id} was cleared because Safe mode hides it.")
        selection = selection.with_changes(pose=None)
    return selection, notices


# --- sending ------------------------------------------------------------------


def _resolve_custom_pose(ctx: BuilderContext, ref: ItemRef, safe_mode: bool) -> tuple[object, str]:
    if ctx.custom_store is None:
        raise SendError(f"{STATUS_MISSING_IMAGE}: pose #{ref.item_id} is not available.")
    try:
        control = ctx.custom_store.load_control_image(ref.item_id, safe_mode=bool(safe_mode))
    except custom_poses.CustomPoseError as error:
        raise SendError(str(error)) from error
    return control.array, control.preprocessor


def resolve_pose_image(ctx: BuilderContext, selection: BuilderSelection, safe_mode: bool) -> tuple[object, str]:
    """Image and preprocessor for the *current* pose selection.

    The image is a ``Path`` for Anhao and an RGB array for Custom Poses: the
    store flattens by input type and enforces Safe Mode as it reads, so both
    tabs send the same pixels.
    """
    if selection.pose is None:
        raise SendError("Select a pose first.")
    if selection.pose.source_id == custom_poses.SOURCE_ID:
        return _resolve_custom_pose(ctx, selection.pose, safe_mode)
    record = pose_record(ctx, selection.pose)
    if record is None:
        raise SendError(f"{STATUS_MISSING_IMAGE}: pose #{selection.pose.item_id} is not available.")
    if safe_mode and anhao.is_nsfw("Poses", record):
        raise SendError(f"Pose #{selection.pose.item_id} is hidden by Safe mode.")
    depth = selection.pose_mode == "depth"
    path = anhao.asset_path(ctx.anhao_root, "Poses", record, thumbnail=False, use_depth=depth)
    if not path.is_file():
        raise SendError(f"{STATUS_MISSING_IMAGE}: {path.name}")
    return path, ("None" if depth else "canny")


def preflight_send_all(
    ctx: BuilderContext, selection: BuilderSelection, final_text: Optional[str], safe_mode: bool
) -> tuple[object, str]:
    if not (final_text or "").strip():
        raise SendError("Final Prompt is empty. Use **Send Pose** to send only the pose.")
    return resolve_pose_image(ctx, selection, safe_mode)


def join_notices(lines: Iterable[str]) -> str:
    return "\n".join(f"- {line}" for line in lines if line)
