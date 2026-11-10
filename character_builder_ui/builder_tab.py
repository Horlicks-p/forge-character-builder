"""Character Builder tab: Character → Outfit → Pose → Final Prompt → Send."""

from __future__ import annotations

from typing import Any, Callable, Optional

import gradio as gr
import numpy as np
from PIL import Image, ImageOps

from character_builder_core import anhao, custom_poses, session, sources
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


def _pose_search_placeholder(source: str) -> str:
    return "Name or ID" if source == custom_poses.SOURCE_ID else "Category, angle, or ID"


def _pick_box(gallery_id: str):
    # Gradio only fires Gallery.select when the selected index changes, so a
    # thumbnail could not be picked again after paging or Clear. The JS click
    # handler writes "<index>:<nonce>" here instead, which always changes.
    return gr.Textbox(value="", elem_id=f"{gallery_id}_pick", elem_classes=["fcb-hidden"], show_label=False, container=False)


def _load_control_image(path) -> np.ndarray:
    if isinstance(path, np.ndarray):  # a Custom Pose, already prepared by its store
        return path
    try:
        image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception as error:  # unreadable / corrupt file
        raise gr.Error(f"{session.STATUS_MISSING_IMAGE}: {getattr(path, 'name', path)} ({error})")
    return np.asarray(image)


def build_builder_tab(
    ctx: session.BuilderContext,
    controlnet_target: Optional[Any],
    *,
    entry_tab: Optional[Any] = None,
) -> tuple[Callable[[], dict], list]:
    """Build the tab inside the current Blocks context.

    Returns ``(load_fn, load_outputs)`` for the caller to attach to
    ``Blocks.load`` so galleries are filled when the page opens. ``entry_tab``
    is the tab holding the builder: returning to it re-checks Custom Poses,
    since Step 3 can already be the open sub-tab.
    """
    controlnet_ok = controlnet_target is not None
    pose_sources = ctx.pose_source_choices()
    default_pose_source = ctx.default_pose_source()
    starts_custom = default_pose_source == custom_poses.SOURCE_ID

    selection = gr.State(BuilderSelection())
    last_generated = gr.State("")
    hair_ids, hair_page = gr.State([]), gr.State(1)
    hair_color_ids = gr.State([])
    eye_color_ids = gr.State([])
    outfit_ids, outfit_page_no = gr.State([]), gr.State(1)
    pose_state, pose_page_no = gr.State(session.PosePageState(default_pose_source)), gr.State(1)

    with gr.Row(elem_classes=["fcb-builder-options"]):
        safe_mode = gr.Checkbox(value=True, label="Safe mode (hide 18+)")
        include_subject = gr.Checkbox(value=False, label="Add 1girl, solo")
    health = ctx.source_health()
    with gr.Accordion("Data sources", open=not sources.all_ok(health), elem_classes=["fcb-source-status"]):
        health_md = gr.Markdown(sources.render_markdown(health))

    with gr.Row(elem_classes=["fcb-builder"]):
        with gr.Column(scale=3, elem_classes=["fcb-builder-steps"]):
            with gr.Tabs():
                with gr.Tab("1 · Character"):
                    _hint(ctx.hairstyle_hint())
                    with gr.Tabs(elem_classes=["fcb-character-parts"]):
                        with gr.Tab("Hairstyle"):
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

                        with gr.Tab("Hair color"):
                            hair_color_clear = gr.Button("Clear hair color", scale=0, min_width=130)
                            hair_color_gallery = gr.Gallery(
                                label="Hair colors",
                                elem_id="fcb_hair_color_gallery",
                                columns=6,
                                height=420,
                                object_fit="contain",
                                allow_preview=False,
                                show_download_button=False,
                                show_share_button=False,
                                elem_classes=["fcb-gallery", "fcb-option-gallery"],
                            )
                            hair_color_pick = _pick_box("fcb_hair_color_gallery")

                        with gr.Tab("Eye color"):
                            eye_color_clear = gr.Button("Clear eye color", scale=0, min_width=130)
                            eye_color_gallery = gr.Gallery(
                                label="Eye colors",
                                elem_id="fcb_eye_color_gallery",
                                columns=6,
                                height=420,
                                object_fit="contain",
                                allow_preview=False,
                                show_download_button=False,
                                show_share_button=False,
                                elem_classes=["fcb-gallery", "fcb-option-gallery"],
                            )
                            eye_color_pick = _pick_box("fcb_eye_color_gallery")

                        with gr.Tab("Details"):
                            with gr.Row():
                                bangs = gr.Dropdown(session.dropdown_choices(ctx, KIND_BANGS), value="", label="Bangs", filterable=True)
                                accessory = gr.Dropdown(session.dropdown_choices(ctx, KIND_HAIR_ACCESSORY), value="", label="Hair accessory", filterable=True)
                            eye_shape = gr.Dropdown(session.dropdown_choices(ctx, KIND_EYE_SHAPE), value="", label="Eye shape", filterable=True)

                with gr.Tab("2 · Outfit"):
                    _hint(ctx.outfit_hint())
                    with gr.Row():
                        outfit_category = gr.Dropdown([session.ALL], value=session.ALL, label="Category", scale=1)
                        outfit_search = gr.Textbox(label="Search outfits", placeholder="Tag, category, or ID", scale=2)
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

                with gr.Tab("3 · Pose") as pose_tab:
                    _hint(ctx.pose_hint())
                    pose_source = gr.Radio(
                        pose_sources, value=default_pose_source, label="Pose source", visible=len(pose_sources) > 1, elem_id="fcb_pose_source"
                    )
                    with gr.Row():
                        pose_search = gr.Textbox(label="Search poses", placeholder=_pose_search_placeholder(default_pose_source), scale=2)
                        pose_system = gr.Dropdown([session.ALL], value=session.ALL, label="System", visible=not starts_custom)
                        pose_category = gr.Dropdown([session.ALL], value=session.ALL, label="Category", visible=not starts_custom)
                    with gr.Row():
                        pose_mode = gr.Radio(POSE_MODES, value="Normal", label="Control image", visible=not starts_custom)
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
            pose_angle = gr.Radio([], value=None, label="View angle", interactive=False, visible=False)
            pose_preview = gr.Image(label="Selected pose", type="filepath", height=240, interactive=False)
            pose_details_box = gr.Textbox(label="Pose details", interactive=False, visible=False, lines=3, elem_id="fcb_pose_details")
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
                "Normal uses the `canny` preprocessor, Depth uses `None`. Custom Poses follow their saved input type. "
                "Choose the ControlNet model yourself.",
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
        outfit_raw,
        pose_preview,
        pose_angle,
        pose_details_box,
        send_pose_button,
        send_all_button,
    ]

    def view(sel: BuilderSelection, final: str, last: str, safe: bool, *, force: bool = False, notices=()) -> dict:
        sel, stale = session.reconcile_pose(ctx, sel, bool(safe))
        notices = [*stale, *notices]
        update = session.regenerate(ctx, sel, final, last, bool(safe), force=force)
        _, raw = session.outfit_summary(ctx, sel)
        _, preview = session.pose_summary(ctx, sel)
        angles = session.pose_angle_choices(ctx, sel)
        selected_angle = sel.pose.item_id if sel.pose is not None else None
        can_send = controlnet_ok and sel.pose is not None
        details = session.pose_details(ctx, sel)
        return {
            selection: sel,
            appearance_box: update.appearance,
            outfit_box: update.outfit,
            final_box: update.final,
            last_generated: update.last_generated,
            edited_md: update.notice,
            status_md: session.join_notices([*notices, *update.warnings]),
            outfit_raw: raw,
            pose_preview: preview,
            pose_angle: gr.update(
                choices=angles,
                value=selected_angle if selected_angle in {item_id for _, item_id in angles} else None,
                visible=bool(angles),
                interactive=len(angles) > 1,
            ),
            pose_details_box: gr.update(value=details, visible=bool(details)),
            send_pose_button: gr.update(interactive=can_send),
            send_all_button: gr.update(interactive=can_send),
        }

    prompt_inputs = [final_box, last_generated, safe_mode]

    def hair_gallery_update(query, page, sel: BuilderSelection):
        color_id = sel.hair_color.item_id if sel.hair_color else None
        result = session.hairstyle_page(ctx, query, page, color_id)
        return result.images, result.ids, result.page, result.info

    hair_gallery_outputs = [hair_gallery, hair_ids, hair_page, hair_info]

    def outfit_gallery_update(category, query, safe, page):
        result = session.outfit_page(ctx, category, query, bool(safe), page)
        return result.images, result.ids, result.page, result.info

    outfit_gallery_inputs = [outfit_category, outfit_search, safe_mode, outfit_page_no]
    outfit_gallery_outputs = [outfit_gallery, outfit_ids, outfit_page_no, outfit_info]

    def pose_gallery_update(source, query, system, category, safe, mode, page):
        result, state = session.pose_gallery(ctx, source, query, system, category, bool(safe), mode == "Depth", page)
        return result.images, state, result.page, result.info

    pose_gallery_inputs = [pose_source, pose_search, pose_system, pose_category, safe_mode, pose_mode, pose_page_no]
    pose_gallery_outputs = [pose_gallery, pose_state, pose_page_no, pose_info]

    def outfit_filters(safe, category):
        options = session.outfit_categories(ctx, bool(safe))
        return gr.update(choices=options, value=category if category in options else session.ALL)

    def pose_filters(source, safe, system, category):
        if source == custom_poses.SOURCE_ID:
            return gr.update(visible=False), gr.update(visible=False)
        systems, categories = session.pose_filter_options(ctx, bool(safe))
        return (
            gr.update(choices=systems, value=system if system in systems else session.ALL, visible=True),
            gr.update(choices=categories, value=category if category in categories else session.ALL, visible=True),
        )

    # ------------------------------------------------------------ Step 1 events

    def set_slot(field: str):
        def handler(value, sel, final, last, safe):
            return view(sel.with_changes(**{field: session.ref_or_none(CHARADB_SOURCE, value)}), final, last, safe)

        return handler

    for component, field in (
        (bangs, "bangs"),
        (accessory, "hair_accessory"),
        (eye_shape, "eye_shape"),
    ):
        component.change(set_slot(field), [component, selection, *prompt_inputs], view_outputs, show_progress=False)

    def pick_character_option(field: str):
        def handler(pick, ids, sel, final, last, safe):
            index = _parse_pick(pick)
            if index is None or not 0 <= index < len(ids):
                return view(sel, final, last, safe)
            return view(sel.with_changes(**{field: ItemRef(CHARADB_SOURCE, ids[index])}), final, last, safe)

        return handler

    hair_color_pick.change(
        pick_character_option("hair_color"),
        [hair_color_pick, hair_color_ids, selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    ).then(hair_gallery_update, [hair_search, hair_page, selection], hair_gallery_outputs, show_progress=False)
    eye_color_pick.change(
        pick_character_option("eye_color"),
        [eye_color_pick, eye_color_ids, selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )

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
    hair_color_clear.click(
        lambda sel, final, last, safe: view(sel.with_changes(hair_color=None), final, last, safe),
        [selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    ).then(hair_gallery_update, [hair_search, hair_page, selection], hair_gallery_outputs, show_progress=False)
    eye_color_clear.click(
        lambda sel, final, last, safe: view(sel.with_changes(eye_color=None), final, last, safe),
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

    def reset_outfit_page(category, query, safe):
        return outfit_gallery_update(category, query, safe, 1)

    outfit_category.change(reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False)
    outfit_search.submit(reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False)
    outfit_prev.click(
        lambda c, q, safe, p: outfit_gallery_update(c, q, safe, int(p or 1) - 1),
        outfit_gallery_inputs,
        outfit_gallery_outputs,
        show_progress=False,
    )
    outfit_next.click(
        lambda c, q, safe, p: outfit_gallery_update(c, q, safe, int(p or 1) + 1),
        outfit_gallery_inputs,
        outfit_gallery_outputs,
        show_progress=False,
    )

    def pick_outfit(pick, ids, sel, final, last, safe):
        index = _parse_pick(pick)
        if index is None or not 0 <= index < len(ids):
            return view(sel, final, last, safe)
        return view(sel.with_changes(outfit=ItemRef(CHARADB_SOURCE, ids[index])), final, last, safe)

    outfit_pick.change(pick_outfit, [outfit_pick, outfit_ids, selection, *prompt_inputs], view_outputs, show_progress=False)
    outfit_clear.click(
        lambda sel, final, last, safe: view(sel.with_changes(outfit=None), final, last, safe),
        [selection, *prompt_inputs],
        view_outputs,
        show_progress=False,
    )

    # ------------------------------------------------------------ Step 3 events

    def reset_pose_page(source, query, system, category, safe, mode):
        return pose_gallery_update(source, query, system, category, safe, mode, 1)

    for trigger in (pose_system.change, pose_category.change, pose_search.submit):
        trigger(reset_pose_page, pose_gallery_inputs[:-1], pose_gallery_outputs, show_progress=False)
    pose_prev.click(
        lambda src, q, s, c, safe, m, p: pose_gallery_update(src, q, s, c, safe, m, int(p or 1) - 1),
        pose_gallery_inputs,
        pose_gallery_outputs,
        show_progress=False,
    )
    pose_next.click(
        lambda src, q, s, c, safe, m, p: pose_gallery_update(src, q, s, c, safe, m, int(p or 1) + 1),
        pose_gallery_inputs,
        pose_gallery_outputs,
        show_progress=False,
    )

    def begin_pose_source_change(source, sel, final, last, safe):
        # Everything from the old source goes at once, so no click can land on it.
        sel, notices = session.change_pose_source(sel, source)
        return {
            **view(sel, final, last, safe, notices=notices),
            pose_gallery: [],
            pose_state: session.PosePageState(source),
            pose_page_no: 1,
            pose_info: "",
            pose_search: gr.update(value="", placeholder=_pose_search_placeholder(source)),
            pose_mode: gr.update(visible=source != custom_poses.SOURCE_ID),
        }

    pose_source.change(
        begin_pose_source_change,
        [pose_source, selection, *prompt_inputs],
        [*view_outputs, *pose_gallery_outputs, pose_search, pose_mode],
        show_progress=False,
    ).then(
        pose_filters, [pose_source, safe_mode, pose_system, pose_category], [pose_system, pose_category], show_progress=False
    ).then(reset_pose_page, pose_gallery_inputs[:-1], pose_gallery_outputs, show_progress=False)

    def pick_pose(pick, state, mode, sel, final, last, safe):
        index = _parse_pick(pick)
        if index is None or not 0 <= index < len(state.refs):
            return view(sel, final, last, safe)
        chosen = state.refs[index]
        problem = session.custom_pose_problem(ctx, chosen, bool(safe))
        if problem:
            return view(sel, final, last, safe, notices=[problem])
        depth = mode == "Depth" and chosen.source_id == anhao.POSE_SOURCE
        return view(sel.with_changes(pose=chosen, pose_mode="depth" if depth else "normal"), final, last, safe)

    pose_pick.change(pick_pose, [pose_pick, pose_state, pose_mode, selection, *prompt_inputs], view_outputs, show_progress=False)

    def change_pose_angle(item_id, sel, final, last, safe):
        updated = session.select_pose_angle(ctx, sel, item_id)
        return view(updated, final, last, safe)

    pose_angle.input(
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

    def refresh_stale_pose(sel, final, last, safe):
        """Leaves the view, including its status line, alone unless the pose is gone."""
        cleaned, _ = session.reconcile_pose(ctx, sel, bool(safe))
        return {selection: sel} if cleaned == sel else view(sel, final, last, safe)

    def enter_builder(state, query, page, sel, final, last, safe):
        if state.source_id != custom_poses.SOURCE_ID or state.revision == custom_poses.revision():
            return refresh_stale_pose(sel, final, last, safe)
        gallery = pose_gallery_update(state.source_id, query, session.ALL, session.ALL, safe, "Normal", page)
        return {**view(sel, final, last, safe), **dict(zip(pose_gallery_outputs, gallery))}

    def current_health():
        return sources.render_markdown(ctx.source_health())

    for tab in filter(None, (pose_tab, entry_tab)):
        tab.select(
            enter_builder,
            [pose_state, pose_search, pose_page_no, selection, *prompt_inputs],
            [*view_outputs, *pose_gallery_outputs],
            show_progress=False,
        )
        tab.select(current_health, outputs=[health_md], show_progress=False)

    safe_mode.change(change_safe_mode, [safe_mode, selection, final_box, last_generated], view_outputs, show_progress=False).then(
        outfit_filters, [safe_mode, outfit_category], [outfit_category], show_progress=False
    ).then(reset_outfit_page, outfit_gallery_inputs[:-1], outfit_gallery_outputs, show_progress=False).then(
        pose_filters, [pose_source, safe_mode, pose_system, pose_category], [pose_system, pose_category], show_progress=False
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

        pose_sent = send_pose_button.click(send_pose, [selection, safe_mode], cn_outputs, show_progress=False)
        # The prompt is appended only after the ControlNet step succeeded.
        all_sent = send_all_button.click(send_all, [selection, final_box, safe_mode], cn_outputs, show_progress=False)
        all_sent.success(fn=None, inputs=[final_box], outputs=[status_md], js=SEND_ALL_APPEND_JS, show_progress=False)
        for sent in (pose_sent, all_sent):  # a failed send may mean the pose was just deleted
            sent.then(refresh_stale_pose, [selection, *prompt_inputs], view_outputs, show_progress=False)

    # ------------------------------------------------------------ initial load

    load_outputs = [
        *hair_gallery_outputs,
        hair_color_gallery,
        hair_color_ids,
        eye_color_gallery,
        eye_color_ids,
        outfit_category,
        *outfit_gallery_outputs,
        pose_system,
        pose_category,
        *pose_gallery_outputs,
        health_md,
    ]

    def initial_load() -> dict:
        hair = session.hairstyle_page(ctx, "", 1, None)
        hair_colors = session.character_option_page(ctx, KIND_HAIR_COLOR)
        eye_colors = session.character_option_page(ctx, KIND_EYE_COLOR)
        outfit = session.outfit_page(ctx, session.ALL, "", True, 1)
        pose, state = session.pose_gallery(ctx, default_pose_source, "", session.ALL, session.ALL, True, False, 1)
        system_filter, category_filter = pose_filters(default_pose_source, True, session.ALL, session.ALL)
        return {
            hair_gallery: hair.images,
            hair_ids: hair.ids,
            hair_page: hair.page,
            hair_info: hair.info,
            hair_color_gallery: hair_colors.images,
            hair_color_ids: hair_colors.ids,
            eye_color_gallery: eye_colors.images,
            eye_color_ids: eye_colors.ids,
            outfit_category: gr.update(choices=session.outfit_categories(ctx, True), value=session.ALL),
            outfit_gallery: outfit.images,
            outfit_ids: outfit.ids,
            outfit_page_no: outfit.page,
            outfit_info: outfit.info,
            pose_system: system_filter,
            pose_category: category_filter,
            pose_gallery: pose.images,
            pose_state: state,
            pose_page_no: pose.page,
            pose_info: pose.info,
            health_md: current_health(),
        }

    return initial_load, load_outputs
