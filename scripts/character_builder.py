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
from character_builder_core import anhao, charadb_adapter, session, sources
from character_builder_ui.builder_tab import build_builder_tab


EXTENSION_ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 48

# Anhao data access lives in character_builder_core.anhao (shared with the
# Character Builder). These thin wrappers keep the legacy call sites unchanged.
GALLERY_ROOT = anhao.find_gallery_root(EXTENSION_ROOT)
LIBRARIES = anhao.LIBRARIES
_english_value = anhao.english_value
_is_nsfw = anhao.is_nsfw
_item_title = anhao.item_title
_prompt_for = anhao.prompt_for
_caption = anhao.caption


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
        gallery.append((str(path), _caption(label, item)))
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
        gr.update(choices=primary_options, value=primary_value, label=config["primary_label"]),
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


def _select_item(label: str, use_depth: bool, item_ids: list[str], evt: gr.SelectData):
    if not item_ids or evt.index is None or evt.index >= len(item_ids):
        return None, "", "", "No item selected"

    item = _find_item(label, item_ids[evt.index])
    if item is None:
        return None, "", "", "The selected item could not be found"

    path = _asset_path(label, item, thumbnail=False, use_depth=bool(use_depth))
    if not path.is_file():
        return None, "", "", f"Image not found: {path.name}"

    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    title = _item_title(label, item)
    details = f"{title}\nID: {item.get('id', '')}"
    if label == "Poses":
        details += f"\nMode: {'Depth control image' if use_depth else 'Pose reference image'}"
    return image, str(path), _prompt_for(label, item), details


def _random_item(label, query, primary, secondary, safe_mode, use_depth):
    items = _filtered_items(label, query, primary, secondary, safe_mode)
    if not items:
        return None, "", "", "No items match the current filters"
    item = random.choice(items)
    path = _asset_path(label, item, thumbnail=False, use_depth=bool(use_depth))
    if not path.is_file():
        return None, "", "", "The randomly selected image is missing"
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    details = f"{_item_title(label, item)}\nID: {item.get('id', '')}"
    return image, str(path), _prompt_for(label, item), details


def _image_for_controlnet(path: str, label: str, use_depth: bool):
    if not path:
        raise gr.Error("Select an item first")
    if not Path(path).is_file():
        raise gr.Error(f"Missing reference image: {Path(path).name}")
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    array = np.asarray(image)
    if label == "Poses" and use_depth:
        return array, True, "None"
    if label == "Poses":
        return array, True, "canny"
    return array, True, "None"


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
    return session.BuilderContext(charadb=charadb_adapter.load_catalog(items_file, image_root), anhao_root=GALLERY_ROOT)


def _reference_count(ctx: session.BuilderContext) -> int:
    charadb_count = sum(len(ctx.charadb.list_kind(kind)) for kind in ctx.charadb.by_kind if kind != "pose")
    return charadb_count + sum(anhao.library_count(GALLERY_ROOT, label) for label in _visible_libraries())


def _gallery_ui():
    libraries = _visible_libraries()
    legacy_health = sources.anhao_health(GALLERY_ROOT, libraries=libraries)
    source_status = sources.render_markdown([legacy_health])
    if GALLERY_ROOT is not None:
        source_status += f"  \nData folder: `{GALLERY_ROOT}`"
    target = _find_controlnet_target()
    ctx = _builder_context()

    with gr.Blocks(elem_id="forge_character_builder") as interface:
        gr.HTML(
            f"""
            <header class="fcb-hero">
              <div>
                <h1>Forge Character Builder</h1>
                <p>Build Danbooru-style character prompts and send a pose to ControlNet.</p>
              </div>
              <div class="fcb-count">{_reference_count(ctx):,} <span>REFERENCES</span></div>
            </header>
            """
        )

        with gr.Tabs():
            with gr.Tab("Character Builder"):
                builder_load, builder_outputs = build_builder_tab(
                    ctx, target, show_anhao_outfits=_option(OPT_SHOW_ANHAO_OUTFITS)
                )

            with gr.Tab("Pose Gallery"):
                with gr.Row(elem_classes=["fcb-toolbar"]):
                    library = gr.Radio(libraries, value="Poses", label="Library", elem_classes=["fcb-library"])
                    search = gr.Textbox(label="Search", placeholder="Category, type, or ID", scale=3)
                    search_button = gr.Button("Search", variant="secondary", scale=0, min_width=90)
                    random_button = gr.Button("Random pick", variant="secondary", scale=0, min_width=110)

                with gr.Row(elem_classes=["fcb-filters"]):
                    primary = gr.Dropdown(["All"], value="All", label="System")
                    secondary = gr.Dropdown(["All"], value="All", label="Category")
                    safe_mode = gr.Checkbox(value=True, label="Safe mode (hide 18+)")
                    use_depth = gr.Checkbox(value=False, label="Use depth image")

                page = gr.State(1)
                item_ids = gr.State([])
                selected_path = gr.State("")

                with gr.Row(elem_classes=["fcb-workbench"]):
                    with gr.Column(scale=4, elem_classes=["fcb-browser"]):
                        gallery = gr.Gallery(
                            label="References",
                            columns=6,
                            rows=2,
                            height=590,
                            object_fit="contain",
                            allow_preview=False,
                            show_download_button=False,
                            show_share_button=False,
                            elem_classes=["fcb-gallery"],
                        )
                        with gr.Row(elem_classes=["fcb-pagination"]):
                            previous = gr.Button("Previous", min_width=90)
                            page_info = gr.Markdown("Loading…", elem_classes=["fcb-page-info"])
                            following = gr.Button("Next", min_width=90)

                    with gr.Column(scale=2, min_width=320, elem_classes=["fcb-inspector"]):
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
                        gr.Markdown(
                            "Depth images use the `None` preprocessor. Regular pose images preselect `canny`. Choose the ControlNet model separately.",
                            elem_classes=["fcb-help"],
                        )

                gr.Markdown(source_status, elem_classes=["fcb-source-status"])

                outputs = [gallery, item_ids, page, page_info, primary, secondary, use_depth]
                refresh_inputs = [library, search, primary, secondary, safe_mode, use_depth, page]

                interface.load(
                    fn=lambda: _refresh("Poses", "", "All", "All", True, False, 1),
                    outputs=outputs,
                    show_progress=False,
                )
                library.change(
                    fn=_change_library,
                    inputs=[library, search, safe_mode, use_depth],
                    outputs=outputs,
                    show_progress=False,
                )
                search.submit(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                search_button.click(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                primary.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                secondary.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                safe_mode.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)
                use_depth.change(fn=_reset_page, inputs=refresh_inputs, outputs=outputs, show_progress=False)

                previous.click(fn=lambda value: _move_page(value, -1), inputs=[page], outputs=[page], show_progress=False).then(
                    fn=_refresh, inputs=refresh_inputs, outputs=outputs, show_progress=False
                )
                following.click(fn=lambda value: _move_page(value, 1), inputs=[page], outputs=[page], show_progress=False).then(
                    fn=_refresh, inputs=refresh_inputs, outputs=outputs, show_progress=False
                )

                gallery.select(
                    fn=_select_item,
                    inputs=[library, use_depth, item_ids],
                    outputs=[selected_image, selected_path, prompt, selection_details],
                    show_progress=False,
                )
                random_button.click(
                    fn=_random_item,
                    inputs=[library, search, primary, secondary, safe_mode, use_depth],
                    outputs=[selected_image, selected_path, prompt, selection_details],
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
                    send_controlnet.click(
                        fn=_image_for_controlnet,
                        inputs=[selected_path, library, use_depth],
                        outputs=[target.image.background, target.enabled, target.module],
                        show_progress=False,
                    ).success(
                        # Only append the prompt after the ControlNet step succeeded.
                        fn=None,
                        inputs=[prompt],
                        js="(text) => { appendCharacterBuilderPrompt(text, 'txt2img'); }",
                    )
                else:
                    send_controlnet.interactive = False

        interface.load(fn=builder_load, outputs=builder_outputs, show_progress=False)

    return [(interface, "Character Builder", "forge-character-builder")]


script_callbacks.on_ui_tabs(_gallery_ui)
if shared is not None and hasattr(script_callbacks, "on_ui_settings"):
    script_callbacks.on_ui_settings(_on_ui_settings)

