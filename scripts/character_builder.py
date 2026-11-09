from __future__ import annotations

import random
from pathlib import Path

import gradio as gr
import numpy as np
from PIL import Image, ImageOps

from modules import infotext_utils, script_callbacks

try:
    from modules import shared
except ImportError:  # pragma: no cover - only outside Forge
    shared = None

# Forge Neo adds the extension root to sys.path, so the shared core package
# (kept outside scripts/ so Forge does not load it as a script) imports directly.
from character_builder_core import anhao, charadb_adapter, custom_poses, session, sources
# The Builder's thumbnail-click bridge is reused so the legacy gallery can
# re-pick the same slot after a library switch, paging or a cleared selection.
from character_builder_ui.builder_tab import _parse_pick, _pick_box, build_builder_tab
from character_builder_ui import custom_pose_panel
from character_builder_ui.custom_pose_panel import CUSTOM_LABEL


EXTENSION_ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 48

# Anhao data access lives in character_builder_core.anhao (shared with the
# Character Builder). These thin wrappers keep the legacy call sites unchanged.
GALLERY_ROOT = anhao.find_gallery_root(EXTENSION_ROOT)
GALLERY_CANDIDATE = None if GALLERY_ROOT else anhao.find_gallery_candidate(EXTENSION_ROOT)
LIBRARIES = anhao.LIBRARIES
_english_value = anhao.english_value
_is_nsfw = anhao.is_nsfw
_item_title = anhao.item_title
_prompt_for = anhao.prompt_for
_caption = anhao.caption


def _forge_data_path() -> str:
    from modules import paths_internal

    return paths_internal.data_path


CUSTOM_STORE = custom_poses.open_store(data_path=_forge_data_path)

OPTIONS_SECTION = ("forge_character_builder", "Forge Character Builder")
OPT_SHOW_PORTRAITS = "fcb_show_portraits"
OPT_SHOW_ANHAO_OUTFITS = "fcb_show_anhao_outfits"
OPTION_DEFAULTS = {OPT_SHOW_PORTRAITS: False, OPT_SHOW_ANHAO_OUTFITS: True}


def _option(key: str) -> bool:
    """Read a setting; unregistered or missing options fall back to the default."""
    default = OPTION_DEFAULTS[key]
    opts = getattr(shared, "opts", None)
    try:
        return bool(getattr(opts, key, default)) if opts is not None else default
    except Exception:
        return default


def _on_ui_settings():
    shared.opts.add_option(
        OPT_SHOW_PORTRAITS,
        shared.OptionInfo(False, "Show the Portraits library in the Pose Gallery tab", section=OPTIONS_SECTION).needs_reload_ui(),
    )
    shared.opts.add_option(
        OPT_SHOW_ANHAO_OUTFITS,
        shared.OptionInfo(
            True, "Offer Anhao (Original) outfits as a Character Builder outfit source", section=OPTIONS_SECTION
        ).needs_reload_ui(),
    )


def _visible_libraries() -> list[str]:
    return [label for label in LIBRARIES if label != "Portraits" or _option(OPT_SHOW_PORTRAITS)]


def _load_library(label: str) -> tuple[dict, ...]:
    return anhao.load_library(GALLERY_ROOT, label)


def _filtered_items(label: str, query: str, primary: str, secondary: str, safe_mode: bool) -> list[dict]:
    return anhao.filtered_items(GALLERY_ROOT, label, query, primary, secondary, safe_mode)


def _option_values(label: str, field: str | None, safe_mode: bool) -> list[str]:
    return anhao.option_values(GALLERY_ROOT, label, field, safe_mode)


def _asset_path(label: str, item: dict, *, thumbnail: bool, use_depth: bool) -> Path:
    return anhao.asset_path(GALLERY_ROOT, label, item, thumbnail=thumbnail, use_depth=use_depth)


def _render_gallery(
    label: str,
    query: str,
    primary: str,
    secondary: str,
    safe_mode: bool,
    use_depth: bool,
    page: int,
):
    if GALLERY_ROOT is None:
        return [], [], 1, "Gallery data was not found. Install the original ai-pose-gallery-zh dataset."

    items = _filtered_items(label, query, primary, secondary, safe_mode)
    if label == "Poses":
        items = anhao.pose_group_representatives(items)
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(int(page or 1), total_pages))
    start = (page - 1) * PAGE_SIZE
    page_items = items[start : start + PAGE_SIZE]

    gallery = []
    ids = []
    for item in page_items:
        path = _asset_path(label, item, thumbnail=True, use_depth=bool(use_depth))
        if not path.is_file():
            continue
        caption = anhao.pose_group_caption(GALLERY_ROOT, item) if label == "Poses" else _caption(label, item)
        gallery.append((str(path), caption))
        ids.append(str(item.get("id", "")))

    info = f"Page {page} of {total_pages} · {len(items):,} items"
    return gallery, ids, page, info


def _refresh(
    label: str,
    query: str,
    primary: str,
    secondary: str,
    safe_mode: bool,
    use_depth: bool,
    page: int,
):
    if label == CUSTOM_LABEL:
        return custom_pose_panel.refresh(CUSTOM_STORE, query, safe_mode, page)
    config = LIBRARIES[label]
    primary_options = _option_values(label, config["primary"], safe_mode)
    secondary_options = _option_values(label, config["secondary"], safe_mode)
    primary_value = primary if primary in primary_options else "All"
    secondary_value = secondary if secondary in secondary_options else "All"
    gallery, ids, page, info = _render_gallery(
        label,
        query,
        primary_value,
        secondary_value,
        safe_mode,
        use_depth,
        page,
    )
    return (
        gallery,
        ids,
        page,
        info,
        gr.update(choices=primary_options, value=primary_value, label=config["primary_label"], visible=True),
        gr.update(
            choices=secondary_options,
            value=secondary_value,
            label=config["secondary_label"] or "Secondary filter",
            visible=bool(config["secondary"]),
        ),
        gr.update(visible=label == "Poses"),
    )


def _change_library(label, query, safe_mode, use_depth):
    return _refresh(label, query, "All", "All", safe_mode, use_depth, 1)


def _reset_page(*values):
    return _refresh(*values[:-1], 1)


def _move_page(page: int, delta: int) -> int:
    return max(1, int(page or 1) + delta)


def _find_item(label: str, item_id: str) -> dict | None:
    for item in _load_library(label):
        if str(item.get("id", "")) == str(item_id):
            return item
    return None


# The legacy selection is stored as "<library>:<id>", never as an image path:
# the image and its preprocessor are resolved together from the *current*
# Normal/Depth setting when sending, so they cannot disagree.


def _selection_ref(label: str, item: dict) -> str:
    return f"{label}:{item.get('id', '')}"


def _parse_selection_ref(ref: str) -> tuple[str, dict] | None:
    label, _, item_id = str(ref or "").partition(":")
    if label not in LIBRARIES or not item_id:
        return None
    item = _find_item(label, item_id)
    return (label, item) if item is not None else None


def _preview(label: str, item: dict, use_depth: bool):
    """(image, details) for the selected item, or (None, error text)."""
    path = _asset_path(label, item, thumbnail=False, use_depth=bool(use_depth))
    if not path.is_file():
        return None, f"Image not found: {path.name}"
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    details = f"{_item_title(label, item)}\nID: {item.get('id', '')}"
    if label == "Poses":
        details += f"\nMode: {'Depth control image' if use_depth else 'Pose reference image'}"
    return image, details


def _legacy_angle_update(ref: str):
    parsed = _parse_selection_ref(ref)
    if parsed is None or parsed[0] != "Poses":
        return gr.update(choices=[], value=None, visible=False, interactive=False)
    _, item = parsed
    choices = [
        (_english_value(view.get("角度", "")) or str(view.get("id", "")), str(view.get("id", "")))
        for view in anhao.pose_views(GALLERY_ROOT, item)
    ]
    return gr.update(
        choices=choices, value=str(item.get("id", "")),
        visible=bool(choices), interactive=len(choices) > 1,
    )


def _clear_legacy_selection():
    return None, "", "", "No item selected", _legacy_angle_update("")


def _switch_legacy_angle(ref: str, item_id: str, use_depth: bool, safe_mode: bool):
    parsed = _parse_selection_ref(ref)
    if parsed is None or parsed[0] != "Poses" or not item_id:
        return gr.update(), gr.update(), gr.update(), gr.update()
    _, current = parsed
    variants = {str(item.get("id", "")): item for item in anhao.pose_views(GALLERY_ROOT, current)}
    chosen = variants.get(str(item_id))
    if chosen is None or (safe_mode and _is_nsfw("Poses", chosen)):
        return gr.update(), gr.update(), gr.update(), gr.update()
    image, details = _preview("Poses", chosen, use_depth)
    if image is None:
        return None, "", "", details
    return image, _selection_ref("Poses", chosen), _prompt_for("Poses", chosen), details


def _select_item(label: str, use_depth: bool, item_ids: list[str], index: int | None):
    if not item_ids or index is None or not 0 <= index < len(item_ids):
        return _clear_legacy_selection()

    item = _find_item(label, item_ids[index])
    if item is None:
        return None, "", "", "The selected item could not be found", _legacy_angle_update("")

    image, details = _preview(label, item, use_depth)
    if image is None:
        return None, "", "", details, _legacy_angle_update("")
    ref = _selection_ref(label, item)
    return image, ref, _prompt_for(label, item), details, _legacy_angle_update(ref)


def _pick_item(pick: str, label: str, use_depth: bool, item_ids: list[str], safe_mode: bool = True):
    """Selection from the hidden pick box written by the JS click bridge."""
    index = _parse_pick(pick)
    value = item_ids[index] if item_ids and index is not None and 0 <= index < len(item_ids) else None
    if value is not None:
        custom = custom_pose_panel.custom_id(value) is not None
        if custom != (label == CUSTOM_LABEL):  # the gallery is still redrawing for another library
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        if custom:
            return custom_pose_panel.select(CUSTOM_STORE, value, safe_mode)
    return _select_item(label, use_depth, item_ids, index)


def _random_item(label, query, primary, secondary, safe_mode, use_depth):
    if label == CUSTOM_LABEL:
        return _clear_legacy_selection()
    items = _filtered_items(label, query, primary, secondary, safe_mode)
    if label == "Poses":
        items = anhao.pose_group_representatives(items)
    if not items:
        return None, "", "", "No items match the current filters", _legacy_angle_update("")
    item = random.choice(items)
    image, details = _preview(label, item, use_depth)
    if image is None:
        return None, "", "", "The randomly selected image is missing", _legacy_angle_update("")
    ref = _selection_ref(label, item)
    return image, ref, _prompt_for(label, item), details, _legacy_angle_update(ref)


def _refresh_preview(ref: str, use_depth: bool):
    """Show the image that would be sent after Normal/Depth changes."""
    parsed = _parse_selection_ref(ref)
    if parsed is None:
        return gr.update(), gr.update()
    image, details = _preview(*parsed, use_depth)
    return image, details


def _controlnet_source(ref: str, use_depth: bool, safe_mode: bool) -> tuple[Path, str]:
    """Image path and preprocessor for the current selection and mode."""
    parsed = _parse_selection_ref(ref)
    if parsed is None:
        raise gr.Error("Select an item first")
    label, item = parsed
    if safe_mode and _is_nsfw(label, item):
        raise gr.Error("The selected reference is hidden by Safe mode")
    depth = label == "Poses" and bool(use_depth)
    path = _asset_path(label, item, thumbnail=False, use_depth=depth)
    if not path.is_file():
        raise gr.Error(f"Missing reference image: {path.name}")
    return path, ("canny" if label == "Poses" and not depth else "None")


def _image_for_controlnet(ref: str, use_depth: bool, safe_mode: bool = True):
    custom_id = custom_pose_panel.custom_id(ref)
    if custom_id is not None:
        try:
            control = CUSTOM_STORE.load_control_image(custom_id, safe_mode=bool(safe_mode))
        except custom_poses.CustomPoseError as error:
            raise gr.Error(str(error))
        return control.array, True, control.preprocessor
    path, module = _controlnet_source(ref, use_depth, safe_mode)
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(image), True, module


def _find_controlnet_target():
    """txt2img ControlNet Unit 1 (the first non-img2img group in Forge Neo)."""
    try:
        from lib_controlnet.controlnet_ui.controlnet_ui_group import ControlNetUiGroup

        return next((group for group in ControlNetUiGroup.all_ui_groups if not group.is_img2img), None)
    except Exception:
        return None


def _builder_context() -> session.BuilderContext:
    items_file = charadb_adapter.find_items_file(EXTENSION_ROOT)
    image_root = charadb_adapter.find_image_root(EXTENSION_ROOT)
    return session.BuilderContext(charadb=charadb_adapter.load_catalog(items_file, image_root), anhao_root=GALLERY_ROOT, custom_store=CUSTOM_STORE, anhao_candidate=GALLERY_CANDIDATE)


def _reference_count(ctx: session.BuilderContext) -> int:
    charadb_count = sum(len(ctx.charadb.list_kind(kind)) for kind in ctx.charadb.by_kind if kind != "pose")
    # Poses count as logical poses (what the galleries show), not view images.
    return charadb_count + sum(
        anhao.pose_group_count(GALLERY_ROOT) if label == "Poses" else anhao.library_count(GALLERY_ROOT, label)
        for label in _visible_libraries()
    )


def _gallery_ui():
    libraries = _visible_libraries()
    legacy_health = sources.anhao_health(GALLERY_ROOT, libraries=libraries, candidate=GALLERY_CANDIDATE)

    def source_status() -> str:
        text = sources.render_markdown([legacy_health, sources.custom_pose_health(CUSTOM_STORE)])
        return f"{text}  \nData folder: `{GALLERY_ROOT}`" if GALLERY_ROOT is not None else text

    target = _find_controlnet_target()
    ctx = _builder_context()

    with gr.Blocks(elem_id="forge_character_builder") as interface:
        gr.HTML(
            f"""
            <header class="fcb-heading">
              <div>
                <h1>Forge Character Builder</h1>
                <p>Build Danbooru-style character prompts and send a pose to ControlNet.</p>
              </div>
              <div class="fcb-count"><strong>{_reference_count(ctx):,}</strong> references</div>
            </header>
            """
        )

        with gr.Tabs():
            with gr.Tab("Character Builder") as builder_entry:
                builder_load, builder_outputs = build_builder_tab(
                    ctx, target, show_anhao_outfits=_option(OPT_SHOW_ANHAO_OUTFITS), entry_tab=builder_entry
                )

            with gr.Tab("Pose Gallery"):
                with gr.Row(elem_classes=["fcb-toolbar"]):
                    library = gr.Radio([*libraries, CUSTOM_LABEL], value="Poses", label="Library", elem_classes=["fcb-library"])
                    search = gr.Textbox(label="Search", placeholder="Category, type, or ID", scale=3)
                    search_button = gr.Button("Search", variant="secondary", scale=0, min_width=90)
                    random_button = gr.Button("Random pick", variant="secondary", scale=0, min_width=110)
                    add_button = gr.Button("Add Pose", variant="primary", scale=0, min_width=100, visible=False, elem_id="fcb_custom_add")

                with gr.Row(elem_classes=["fcb-filters"]):
                    primary = gr.Dropdown(["All"], value="All", label="System")
                    secondary = gr.Dropdown(["All"], value="All", label="Category")
                    safe_mode = gr.Checkbox(value=True, label="Safe mode (hide 18+)")
                    use_depth = gr.Checkbox(value=False, label="Use depth image")

                custom_status = gr.Textbox(label="Custom Poses status", interactive=False, visible=False, max_lines=2, elem_id="fcb_custom_status")
                custom_form = custom_pose_panel.build_add_form()

                page = gr.State(1)
                item_ids = gr.State([])
                selected_ref = gr.State("")

                with gr.Row(elem_classes=["fcb-workbench"]):
                    with gr.Column(scale=4, elem_classes=["fcb-browser"]):
                        gallery = gr.Gallery(
                            label="References",
                            elem_id="fcb_legacy_gallery",
                            columns=6,
                            rows=2,
                            height=590,
                            object_fit="contain",
                            allow_preview=False,
                            show_download_button=False,
                            show_share_button=False,
                            elem_classes=["fcb-gallery"],
                        )
                        legacy_pick = _pick_box("fcb_legacy_gallery")
                        with gr.Row(elem_classes=["fcb-pagination"]):
                            previous = gr.Button("Previous", min_width=90)
                            page_info = gr.Markdown("Loading…", elem_classes=["fcb-page-info"])
                            following = gr.Button("Next", min_width=90)

                    with gr.Column(scale=2, min_width=320, elem_classes=["fcb-inspector"]):
                        pose_view = gr.Radio(
                            [], value=None, label="Pose view", visible=False, interactive=False
                        )
                        selected_image = gr.Image(
                            label="Selected reference",
                            type="pil",
                            height=360,
                            interactive=False,
                            show_download_button=True,
                            elem_classes=["fcb-selected-image"],
                        )
                        selection_details = gr.Textbox(label="Reference details", lines=3, interactive=False)
                        prompt = gr.Textbox(
                            label="Prompt",
                            lines=5,
                            placeholder="Select a reference to generate an editable prompt",
                            show_copy_button=True,
                        )
                        send_controlnet = gr.Button("Send to txt2img + ControlNet 1", variant="primary")
                        send_img2img = gr.Button("Send to img2img", variant="secondary")
                        send_prompt = gr.Button("Append prompt to txt2img", variant="secondary")
                        custom_manage = custom_pose_panel.build_manage_controls()
                        gr.Markdown(
                            "Depth images use the `None` preprocessor. Regular pose images preselect `canny`. Choose the ControlNet model separately.",
                            elem_classes=["fcb-help"],
                        )

                source_panel = gr.Markdown(source_status(), elem_classes=["fcb-source-status"])

                outputs = [gallery, item_ids, page, page_info, primary, secondary, use_depth]
                refresh_inputs = [library, search, primary, secondary, safe_mode, use_depth, page]
                legacy_selection_outputs = [selected_image, selected_ref, prompt, selection_details, pose_view]
                custom_events = custom_pose_panel.CustomPoseEvents(
                    CUSTOM_STORE,
                    custom_form,
                    custom_manage,
                    add_button=add_button,
                    status=custom_status,
                    library=library,
                    search=search,
                    page=page,
                    safe_mode=safe_mode,
                    selected_ref=selected_ref,
                    selection_outputs=legacy_selection_outputs,
                    refresh_fn=_refresh,
                    refresh_inputs=refresh_inputs,
                    refresh_outputs=outputs,
                    non_custom_controls=[random_button, send_img2img],
                    source_status=source_panel,
                    source_status_fn=source_status,
                )

                interface.load(
                    fn=lambda: _refresh("Poses", "", "All", "All", True, False, 1),
                    outputs=outputs,
                    show_progress=False,
                )
                interface.load(fn=source_status, outputs=[source_panel], show_progress=False)
                custom_events.after_selection(
                    library.change(
                        fn=_change_library,
                        inputs=[library, search, safe_mode, use_depth],
                        outputs=outputs,
                        show_progress=False,
                    ).then(fn=_clear_legacy_selection, outputs=legacy_selection_outputs, show_progress=False)
                )
                search.submit(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                search_button.click(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                primary.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                secondary.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                safe_mode.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                use_depth.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)

                use_depth.change(
                    fn=_refresh_preview,
                    inputs=[selected_ref, use_depth],
                    outputs=[selected_image, selection_details],
                    show_progress=False,
                )

                previous.click(fn=lambda value: _move_page(value, -1), inputs=[page], outputs=[page], show_progress=False).then(
                    fn=_refresh, inputs=refresh_inputs, outputs=outputs, show_progress=False
                )
                following.click(fn=lambda value: _move_page(value, 1), inputs=[page], outputs=[page], show_progress=False).then(
                    fn=_refresh, inputs=refresh_inputs, outputs=outputs, show_progress=False
                )

                custom_events.after_selection(
                    legacy_pick.change(
                        fn=_pick_item,
                        inputs=[legacy_pick, library, use_depth, item_ids, safe_mode],
                        outputs=legacy_selection_outputs,
                        show_progress=False,
                    )
                )
                random_button.click(
                    fn=_random_item,
                    inputs=[library, search, primary, secondary, safe_mode, use_depth],
                    outputs=legacy_selection_outputs,
                    show_progress=False,
                )
                pose_view.input(
                    fn=_switch_legacy_angle,
                    inputs=[selected_ref, pose_view, use_depth, safe_mode],
                    outputs=[selected_image, selected_ref, prompt, selection_details],
                    show_progress=False,
                )

                infotext_utils.register_paste_params_button(
                    infotext_utils.ParamBinding(
                        paste_button=send_img2img,
                        tabname="img2img",
                        source_image_component=selected_image,
                    )
                )

                send_img2img.click(
                    fn=None,
                    inputs=[prompt],
                    js="(text) => { appendCharacterBuilderPrompt(text, 'img2img'); }",
                    show_progress=False,
                )
                send_prompt.click(
                    fn=None,
                    inputs=[prompt],
                    js="(text) => { appendCharacterBuilderPrompt(text, 'txt2img'); }",
                    show_progress=False,
                )

                if target is not None:
                    controlnet_sent = send_controlnet.click(
                        fn=_image_for_controlnet,
                        inputs=[selected_ref, use_depth, safe_mode],
                        outputs=[target.image.background, target.enabled, target.module],
                        show_progress=False,
                    )
                    controlnet_sent.success(
                        # Only append the prompt after the ControlNet step succeeded. The
                        # append helper skips the tab switch when there is nothing to add.
                        fn=None,
                        inputs=[prompt],
                        js="(text) => { appendCharacterBuilderPrompt(text, 'txt2img'); characterBuilderSwitchTab('txt2img'); }",
                    )
                    custom_events.after_send(controlnet_sent)
                else:
                    send_controlnet.interactive = False

        interface.load(fn=builder_load, outputs=builder_outputs, show_progress=False)

    return [(interface, "Character Builder", "forge-character-builder")]


script_callbacks.on_ui_tabs(_gallery_ui)
if shared is not None and hasattr(script_callbacks, "on_ui_settings"):
    script_callbacks.on_ui_settings(_on_ui_settings)

