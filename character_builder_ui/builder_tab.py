"""Character Builder tab: Character → Outfit → Pose → Final Prompt → Send."""

from __future__ import annotations

from typing import Any, Callable, Optional

import gradio as gr
import numpy as np
from PIL import Image, ImageOps

from character_builder_core import anhao, session, sources
from character_builder_core.charadb_adapter import SOURCE_ID as CHARADB_SOURCE
from character_builder_core.models import (
    KIND_BANGS,
    KIND_EYE_COLOR,
    KIND_EYE_SHAPE,
    KIND_HAIR_ACCESSORY,
    KIND_HAIR_COLOR,
    BuilderSelection,
    ItemRef,
)

APPEND_JS = "(text) => appendCharacterBuilderPrompt(text, 'txt2img')"
REPLACE_JS = "(text) => replaceCharacterBuilderPrompt(text, 'txt2img')"
SEND_ALL_APPEND_JS = "(text) => 'Pose sent · ' + appendCharacterBuilderPrompt(text, 'txt2img')"

POSE_MODES = ["Normal", "Depth"]


def _parse_pick(value) -> Optional[int]:
    """Index from the hidden pick box (``"<index>:<nonce>"``, written by JS)."""
    try:
        return int(str(value or "").split(":", 1)[0])
    except ValueError:
        return None


def _hint(text: str) -> None:
    if text:
        gr.Markdown(text, elem_classes=["fcb-hint"])


def _pick_box(gallery_id: str):
    # Gradio only fires Gallery.select when the selected index changes, so a
    # thumbnail could not be picked again after paging or Clear. The JS click
    # handler writes "<index>:<nonce>" here instead, which always changes.
    return gr.Textbox(value="", elem_id=f"{gallery_id}_pick", elem_classes=["fcb-hidden"], show_label=False, container=False)


def _load_control_image(path) -> np.ndarray:
    try:
        image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception as error:  # unreadable / corrupt file
        raise gr.Error(f"{session.STATUS_MISSING_IMAGE}: {getattr(path, 'name', path)} ({error})")
    return np.asarray(image)


def build_builder_tab(
    ctx: session.BuilderContext,
    controlnet_target: Optional[Any],
    *,
    show_anhao_outfits: bool = True,
) -> tuple[Callable[[], dict], list]:
    """Build the tab inside the current Blocks context.

    Returns ``(load_fn, load_outputs)`` for the caller to attach to
    ``Blocks.load`` so galleries are filled when the page opens.
    """
    controlnet_ok = controlnet_target is not None
    outfit_sources = ctx.outfit_source_choices(show_anhao_outfits)
    default_outfit_source = outfit_sources[0][1] if outfit_sources else ""

    selection = gr.State(BuilderSelection())
    last_generated = gr.State("")
    hair_ids, hair_page = gr.State([]), gr.State(1)
    outfit_ids, outfit_page_no = gr.State([]), gr.State(1)
    pose_ids, pose_page_no = gr.State([]), gr.State(1)

    with gr.Row(elem_classes=["fcb-builder-options"]):
        safe_mode = gr.Checkbox(value=True, label="Safe mode (hide 18+)")
        include_subject = gr.Checkbox(value=False, label="Add 1girl, solo")
    health = ctx.source_health()
    with gr.Accordion("Data sources", open=not sources.all_ok(health), elem_classes=["fcb-source-status"]):
        gr.Markdown(sources.render_markdown(health))

    with gr.Row(elem_classes=["fcb-builder"]):
        with gr.Column(scale=3, elem_classes=["fcb-builder-steps"]):
            with gr.Tabs():
                with gr.Tab("1 · Character"):
                    _hint(ctx.hairstyle_hint())
                    hair_summary = gr.Markdown("Hairstyle: *none*")
                    with gr.Row():
                        hair_search = gr.Textbox(label="Search hairstyles", placeholder="e.g. twintails, bob, H010", scale=3)
                        hair_clear = gr.Button("Clear hairstyle", scale=0, min_width=130)
                    hair_gallery = gr.Gallery(
                        label="Hairstyles",
                        elem_id="fcb_hair_gallery",
                        columns=6,
                        height=420,
                        object_fit="contain",
                        allow_preview=False,
                        show_download_button=False,
                        show_share_button=False,
                        elem_classes=["fcb-gallery"],
                    )
                    hair_pick = _pick_box("fcb_hair_gallery")
                    with gr.Row(elem_classes=["fcb-pagination"]):
                        hair_prev = gr.Button("Previous", min_width=90)
                        hair_info = gr.Markdown("", elem_classes=["fcb-page-info"])
                        hair_next = gr.Button("Next", min_width=90)
                    with gr.Row():
                        hair_color = gr.Dropdown(session.dropdown_choices(ctx, KIND_HAIR_COLOR), value="", label="Hair color", filterable=True)
                        bangs = gr.Dropdown(session.dropdown_choices(ctx, KIND_BANGS), value="", label="Bangs", filterable=True)
                        accessory = gr.Dropdown(session.dropdown_choices(ctx, KIND_HAIR_ACCESSORY), value="", label="Hair accessory", filterable=True)
                    with gr.Row():
                        eye_color = gr.Dropdown(session.dropdown_choices(ctx, KIND_EYE_COLOR), value="", label="Eye color", filterable=True)
                        eye_shape = gr.Dropdown(session.dropdown_choices(ctx, KIND_EYE_SHAPE), value="", label="Eye shape", filterable=True)

                with gr.Tab("2 · Outfit"):
                    _hint(ctx.outfit_hint(show_anhao_outfits))
                    outfit_summary = gr.Markdown("Outfit: *none*")
                    with gr.Row():
                        outfit_source = gr.Radio(outfit_sources, value=default_outfit_source or None, label="Source")
                        outfit_category = gr.Dropdown([session.ALL], value=session.ALL, label="Category")
                    with gr.Row():
                        outfit_search = gr.Textbox(label="Search outfits", placeholder="Tag, category, or ID", scale=3)
                        outfit_clear = gr.Button("Clear outfit", scale=0, min_width=120)
                    outfit_gallery = gr.Gallery(
                        label="Outfits",
                        elem_id="fcb_outfit_gallery",
                        columns=6,
                        height=420,
                        object_fit="contain",
                        allow_preview=False,
                        show_download_button=False,
                        show_share_button=False,
                        elem_classes=["fcb-gallery"],
                    )
                    outfit_pick = _pick_box("fcb_outfit_gallery")
                    with gr.Row(elem_classes=["fcb-pagination"]):
                        outfit_prev = gr.Button("Previous", min_width=90)
                        outfit_info = gr.Markdown("", elem_classes=["fcb-page-info"])
                        outfit_next = gr.Button("Next", min_width=90)
                    outfit_raw = gr.Textbox(label="Raw source tags (read-only)", interactive=False, lines=2)

                with gr.Tab("3 · Pose"):
                    _hint(ctx.pose_hint())
                    pose_summary = gr.Markdown("Pose: *none*")
                    with gr.Row():
                        pose_search = gr.Textbox(label="Search poses", placeholder="Category, angle, or ID", scale=2)
                        pose_system = gr.Dropdown([session.ALL], value=session.ALL, label="System")
                        pose_category = gr.Dropdown([session.ALL], value=session.ALL, label="Category")
                    with gr.Row():
                        pose_mode = gr.Radio(POSE_MODES, value="Normal", label="Control image")
                        pose_angle = gr.Radio([], value=None, label="View angle", interactive=False, visible=False)
                        pose_clear = gr.Button("Clear pose", scale=0, min_width=110)
                    pose_gallery = gr.Gallery(
                        label="Poses",
                        elem_id="fcb_pose_gallery",
                        columns=6,
                        height=420,
                        object_fit="contain",
                        allow_preview=False,
                        show_download_button=False,
                        show_share_button=False,
                        elem_classes=["fcb-gallery"],
                    )
                    pose_pick = _pick_box("fcb_pose_gallery")
                    with gr.Row(elem_classes=["fcb-pagination"]):
                        pose_prev = gr.Button("Previous", min_width=90)
                        pose_info = gr.Markdown("", elem_classes=["fcb-page-info"])
                        pose_next = gr.Button("Next", min_width=90)

        with gr.Column(scale=2, min_width=320, elem_classes=["fcb-inspector"]):
            pose_preview = gr.Image(label="Selected pose", type="filepath", height=240, interactive=False)
            appearance_box = gr.Textbox(label="Appearance", interactive=False, lines=2)
            outfit_box = gr.Textbox(label="Outfit", interactive=False, lines=2)
            final_box = gr.Textbox(
                label="Final Prompt (editable)",
                lines=5,
                show_copy_button=True,
                placeholder="Select a hairstyle, eyes, or an outfit to build an appendable prompt",
                elem_id="fcb_final_prompt",
            )
            edited_md = gr.Markdown("")
            reset_button = gr.Button("Reset from selection")
            append_button = gr.Button("Append Prompt to txt2img", variant="secondary")
            send_pose_button = gr.Button("Send Pose to ControlNet 1", interactive=False)
            send_all_button = gr.Button("Send All", variant="primary", interactive=False, elem_id="fcb_send_all")
            with gr.Accordion("Advanced", open=False):
                replace_button = gr.Button("Replace txt2img Prompt (overwrites it)", variant="stop")
            if not controlnet_ok:
                gr.Markdown(
                    f"**{session.STATUS_CONTROLNET_UNAVAILABLE}.** Send Pose and Send All are disabled; Copy and Append still work.",
                    elem_classes=["fcb-help"],
                )
            status_md = gr.Markdown("", elem_id="fcb_status")
            gr.Markdown(
                "Hair, eyes and outfit go to the prompt; only the pose goes to ControlNet. "
                "Normal uses the `canny` preprocessor, Depth uses `None`. Choose the ControlNet model yourself.",
                elem_classes=["fcb-help"],
            )

    # ------------------------------------------------------------------ helpers

    view_outputs = [
        selection,
        appearance_box,
        outfit_box,
        final_box,
        last_generated,
        edited_md,
        status_md,
        hair_summary,
        outfit_summary,
        outfit_raw,
        pose_summary,
        pose_preview,
        pose_angle,
        send_pose_button,
        send_all_button,
    ]

    def view(sel: BuilderSelection, final: str, last: str, safe: bool, *, force: bool = False, notices=()) -> dict:
        update = session.regenerate(ctx, sel, final, last, bool(safe), force=force)
        outfit_md, raw = session.outfit_summary(ctx, sel)
        pose_md, preview = session.pose_summary(ctx, sel)
        angles = session.pose_angle_choices(ctx, sel)
        selected_angle = sel.pose.item_id if sel.pose is not None else None
        can_send = controlnet_ok and sel.pose is not None
        return {
            selection: sel,
            appearance_box: update.appearance,
            outfit_box: update.outfit,
            final_box: update.final,
            last_generated: update.last_generated,
            edited_md: update.notice,
            status_md: session.join_notices([*notices, *update.warnings]),
            hair_summary: session.hairstyle_summary(ctx, sel),
            outfit_summary: outfit_md,
            outfit_raw: raw,
            pose_summary: pose_md,
            pose_preview: preview,
            pose_angle: gr.update(
                choices=angles,
                value=selected_angle if selected_angle in {item_id for _, item_id in angles} else None,
                visible=bool(angles),
                interactive=len(angles) > 1,
            ),
            send_pose_button: gr.update(interactive=can_send),
            send_all_button: gr.update(interactive=can_send),
        }

    prompt_inputs = [final_box, last_generated, safe_mode]

    def hair_gallery_update(query, page, sel: BuilderSelection):
        color_id = sel.hair_color.item_id if sel.hair_color else None
        result = session.hairstyle_page(ctx, query, page, color_id)
        return result.images, result.ids, result.page, result.info

    hair_gallery_outputs = [hair_gallery, hair_ids, hair_page, hair_info]

    def outfit_gallery_update(source, category, query, safe, page):
        result = session.outfit_page(ctx, source, category, query, bool(safe), page)
        return result.images, result.ids, result.page, result.info

    outfit_gallery_inputs = [outfit_source, outfit_category, outfit_search, safe_mode, outfit_page_no]
    outfit_gallery_outputs = [outfit_gallery, outfit_ids, outfit_page_no, outfit_info]

    def pose_gallery_update(query, system, category, safe, mode, page):
        result = session.pose_page(ctx, query, system, category, bool(safe), mode == "Depth", page)
        return result.images, result.ids, result.page, result.info

    pose_gallery_inputs = [pose_search, pose_system, pose_category, safe_mode, pose_mode, pose_page_no]
    pose_gallery_outputs = [pose_gallery, pose_ids, pose_page_no, pose_info]

    def outfit_filters(source, safe, category):
        options = session.outfit_categories(ctx, source, bool(safe))
        return gr.update(choices=options, value=category if category in options else session.ALL)

    def pose_filters(safe, system, category):
        systems, categories = session.pose_filter_options(ctx, bool(safe))
        return (
            gr.update(choices=systems, value=system if system in systems else session.ALL),
            gr.update(choices=categories, value=category if category in categories else session.ALL),
        )

    # ------------------------------------------------------------ Step 1 events

    def set_slot(field: str):
        def handler(value, sel, final, last, safe):
            return view(sel.with_changes(**{field: session.ref_or_none(CHARADB_SOURCE, value)}), final, last, safe)

        return handler

    for component, field in (
        (bangs, "bangs"),
        (accessory, "hair_accessory"),
        (eye_color, "eye_color"),
        (eye_shape, "eye_shape"),
    ):
        component.change(set_slot(field), [component, selection, *prompt_inputs], view_outputs, show_progress=False)

    hair_color.change(
        set_slot("hair_color"), [hair_color, selection, *prompt_inputs], view_outputs, show_progress=False
    ).then(hair_gallery_update, [hair_search, hair_page, selection], hair_gallery_outputs, show_progress=False)

    def pick_hairstyle(pick, ids, sel, final, last, safe):
        index = _parse_pick(pick)
        if index is None or not 0 <= index < len(ids):
            return view(sel, final, last, safe)
        return view(sel.with_changes(hairstyle=ItemRef(CHARADB_SOURCE, ids[index]), hairstyle_variant=None), final, last, safe)

    hair_pick.change(pick_hairstyle, [hair_pick, hair_ids, selection, *prompt_inputs], view_outputs, show_progress=False)
    hair_clear.click(
        lambda sel, final, last, safe: view(sel.with_changes(hairstyle=None, hairstyle_variant=None), final, last, safe),
        [selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )
    hair_search.submit(lambda q, sel: hair_gallery_update(q, 1, sel), [hair_search, selection], hair_gallery_outputs, show_progress=False)
    hair_prev.click(lambda q, p, sel: hair_gallery_update(q, int(p or 1) - 1, sel), [hair_search, hair_page, selection], hair_gallery_outputs, show_progress=False)
    hair_next.click(lambda q, p, sel: hair_gallery_update(q, int(p or 1) + 1, sel), [hair_search, hair_page, selection], hair_gallery_outputs, show_progress=False)

    include_subject.change(
        lambda value, sel, final, last, safe: view(sel.with_changes(include_subject=bool(value)), final, last, safe),
        [include_subject, selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )

    # ------------------------------------------------------------ Step 2 events

    def reset_outfit_page(source, category, query, safe):
        return outfit_gallery_update(source, category, query, safe, 1)

    def change_outfit_source(source, sel, final, last, safe):
        sel, notices = session.change_outfit_source(sel, source, prompt_edited=session.is_edited(final, last))
        return view(sel, final, last, safe, notices=notices)

    outfit_source.change(
        change_outfit_source, [outfit_source, selection, *prompt_inputs], view_outputs, show_progress=False
    ).then(outfit_filters, [outfit_source, safe_mode, outfit_category], [outfit_category], show_progress=False).then(
        reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False
    )
    outfit_category.change(reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False)
    outfit_search.submit(reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False)
    outfit_prev.click(
        lambda s, c, q, safe, p: outfit_gallery_update(s, c, q, safe, int(p or 1) - 1),
        outfit_gallery_inputs,
        outfit_gallery_outputs,
        show_progress=False,
    )
    outfit_next.click(
        lambda s, c, q, safe, p: outfit_gallery_update(s, c, q, safe, int(p or 1) + 1),
        outfit_gallery_inputs,
        outfit_gallery_outputs,
        show_progress=False,
    )

    def pick_outfit(pick, source, ids, sel, final, last, safe):
        index = _parse_pick(pick)
        if not source or index is None or not 0 <= index < len(ids):
            return view(sel, final, last, safe)
        return view(sel.with_changes(outfit=ItemRef(source, ids[index])), final, last, safe)

    outfit_pick.change(pick_outfit, [outfit_pick, outfit_source, outfit_ids, selection, *prompt_inputs], view_outputs, show_progress=False)
    outfit_clear.click(
        lambda sel, final, last, safe: view(sel.with_changes(outfit=None), final, last, safe),
        [selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )

    # ------------------------------------------------------------ Step 3 events

    def reset_pose_page(query, system, category, safe, mode):
        return pose_gallery_update(query, system, category, safe, mode, 1)

    for trigger in (pose_system.change, pose_category.change, pose_search.submit):
        trigger(reset_pose_page, pose_gallery_inputs[:-1], pose_gallery_outputs, show_progress=False)
    pose_prev.click(
        lambda q, s, c, safe, m, p: pose_gallery_update(q, s, c, safe, m, int(p or 1) - 1),
        pose_gallery_inputs,
        pose_gallery_outputs,
        show_progress=False,
    )
    pose_next.click(
        lambda q, s, c, safe, m, p: pose_gallery_update(q, s, c, safe, m, int(p or 1) + 1),
        pose_gallery_inputs,
        pose_gallery_outputs,
        show_progress=False,
    )

    def pick_pose(pick, ids, mode, sel, final, last, safe):
        index = _parse_pick(pick)
        if index is None or not 0 <= index < len(ids):
            return view(sel, final, last, safe)
        chosen = ItemRef(anhao.POSE_SOURCE, ids[index])
        return view(sel.with_changes(pose=chosen, pose_mode="depth" if mode == "Depth" else "normal"), final, last, safe)

    pose_pick.change(pick_pose, [pose_pick, pose_ids, pose_mode, selection, *prompt_inputs], view_outputs, show_progress=False)

    def change_pose_angle(item_id, sel, final, last, safe):
        updated = session.select_pose_angle(ctx, sel, item_id)
        return view(updated, final, last, safe)

    pose_angle.change(
        change_pose_angle, [pose_angle, selection, *prompt_inputs], view_outputs, show_progress=False
    )

    def change_pose_mode(mode, sel, final, last, safe):
        return view(sel.with_changes(pose_mode="depth" if mode == "Depth" else "normal"), final, last, safe)

    pose_mode.change(change_pose_mode, [pose_mode, selection, *prompt_inputs], view_outputs, show_progress=False).then(
        pose_gallery_update, pose_gallery_inputs, pose_gallery_outputs, show_progress=False
    )
    pose_clear.click(
        lambda sel, final, last, safe: view(sel.with_changes(pose=None), final, last, safe),
        [selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )

    # ------------------------------------------------------------ safe mode

    def change_safe_mode(safe, sel, final, last):
        sel, notices = session.apply_safe_mode(ctx, sel, bool(safe), prompt_edited=session.is_edited(final, last))
        return view(sel, final, last, safe, notices=notices)

    safe_mode.change(change_safe_mode, [safe_mode, selection, final_box, last_generated], view_outputs, show_progress=False).then(
        outfit_filters, [outfit_source, safe_mode, outfit_category], [outfit_category], show_progress=False
    ).then(reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False).then(
        pose_filters, [safe_mode, pose_system, pose_category], [pose_system, pose_category], show_progress=False
    ).then(reset_pose_page, pose_gallery_inputs[:-1], pose_gallery_outputs, show_progress=False)

    # ------------------------------------------------------------ final prompt

    final_box.input(session.edited_notice, [final_box, last_generated], [edited_md], show_progress=False)
    reset_button.click(
        lambda sel, final, last, safe: view(sel, final, last, safe, force=True),
        [selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )
    append_button.click(fn=None, inputs=[final_box], outputs=[status_md], js=APPEND_JS, show_progress=False)
    replace_button.click(fn=None, inputs=[final_box], outputs=[status_md], js=REPLACE_JS, show_progress=False)

    if controlnet_ok:
        cn_outputs = [
            controlnet_target.image.background,
            controlnet_target.enabled,
            controlnet_target.module,
            status_md,
        ]

        def send_pose(sel, safe):
            try:
                path, module = session.resolve_pose_image(ctx, sel, bool(safe))
            except session.SendError as error:
                raise gr.Error(str(error))
            return _load_control_image(path), True, module, session.STATUS_POSE_SENT

        def send_all(sel, final, safe):
            try:
                path, module = session.preflight_send_all(ctx, sel, final, bool(safe))
            except session.SendError as error:
                raise gr.Error(str(error))
            return _load_control_image(path), True, module, session.STATUS_POSE_SENT

        send_pose_button.click(send_pose, [selection, safe_mode], cn_outputs, show_progress=False)
        # The prompt is appended only after the ControlNet step succeeded.
        send_all_button.click(send_all, [selection, final_box, safe_mode], cn_outputs, show_progress=False).success(
            fn=None, inputs=[final_box], outputs=[status_md], js=SEND_ALL_APPEND_JS, show_progress=False
        )

    # ------------------------------------------------------------ initial load

    load_outputs = [
        *hair_gallery_outputs,
        outfit_category,
        *outfit_gallery_outputs,
        pose_system,
        pose_category,
        *pose_gallery_outputs,
    ]

    def initial_load() -> dict:
        hair = session.hairstyle_page(ctx, "", 1, None)
        outfit = session.outfit_page(ctx, default_outfit_source, session.ALL, "", True, 1)
        pose = session.pose_page(ctx, "", session.ALL, session.ALL, True, False, 1)
        systems, categories = session.pose_filter_options(ctx, True)
        return {
            hair_gallery: hair.images,
            hair_ids: hair.ids,
            hair_page: hair.page,
            hair_info: hair.info,
            outfit_category: gr.update(choices=session.outfit_categories(ctx, default_outfit_source, True), value=session.ALL),
            outfit_gallery: outfit.images,
            outfit_ids: outfit.ids,
            outfit_page_no: outfit.page,
            outfit_info: outfit.info,
            pose_system: gr.update(choices=systems, value=session.ALL),
            pose_category: gr.update(choices=categories, value=session.ALL),
            pose_gallery: pose.images,
            pose_ids: pose.ids,
            pose_page_no: pose.page,
            pose_info: pose.info,
        }

    return initial_load, load_outputs
