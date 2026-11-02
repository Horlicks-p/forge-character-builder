from __future__ import annotations

import json
import os
import random
from functools import lru_cache
from pathlib import Path

import gradio as gr
import numpy as np
from PIL import Image, ImageOps

from modules import infotext_utils, script_callbacks


EXTENSION_ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 48

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


def _english_value(value: object) -> str:
    text = str(value or "").strip()
    return VALUE_TRANSLATIONS.get(text, text)


def _find_gallery_root() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("AI_POSE_GALLERY_ROOT", "").strip()
    if configured:
        candidates.append(Path(configured))

    candidates.extend(
        [
            EXTENSION_ROOT / "vendor",
            EXTENSION_ROOT.parent / "ai-pose-gallery-zh",
        ]
    )

    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / "data" / "pose-zh.json").is_file():
            return candidate
    return None


GALLERY_ROOT = _find_gallery_root()


@lru_cache(maxsize=3)
def _load_library(label: str) -> tuple[dict, ...]:
    if GALLERY_ROOT is None:
        return tuple()

    config = LIBRARIES[label]
    path = GALLERY_ROOT / "data" / config["file"]
    with path.open("r", encoding="utf-8") as handle:
        return tuple(json.load(handle))


def _is_nsfw(label: str, item: dict) -> bool:
    if label == "Poses":
        return item.get("体系", "") in NSFW_POSE_SYSTEMS
    if label == "Outfits":
        return item.get("分类", "") in NSFW_COSTUME_CATEGORIES
    return False


def _filtered_items(
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
    for item in _load_library(label):
        if safe_mode and _is_nsfw(label, item):
            continue
        if primary and _english_value(item.get(config["primary"], "")) != primary:
            continue
        if secondary and config["secondary"] and _english_value(item.get(config["secondary"], "")) != secondary:
            continue
        if normalized_query:
            english_fields = " ".join(
                _english_value(item.get(field, ""))
                for field in (config["primary"], config["secondary"])
                if field
            )
            haystack = f"{item.get('id', '')} {english_fields} {_prompt_for(label, item)}".casefold()
            if normalized_query not in haystack:
                continue
        results.append(item)
    return results


def _option_values(label: str, field: str | None, safe_mode: bool) -> list[str]:
    if not field:
        return ["All"]
    values = {
        _english_value(item.get(field, ""))
        for item in _load_library(label)
        if item.get(field) and (not safe_mode or not _is_nsfw(label, item))
    }
    return ["All", *sorted(values)]


def _depth_path(item: dict) -> Path:
    raw = str(item.get("原图", "")).replace("\\", "/").lstrip("/")
    if raw.startswith("img/"):
        stem = raw[len("img/") :].rsplit(".", 1)[0]
        return GALLERY_ROOT / "img" / "depth" / f"{stem}.webp"
    return GALLERY_ROOT / raw


def _asset_path(label: str, item: dict, *, thumbnail: bool, use_depth: bool) -> Path:
    if label == "Poses" and use_depth:
        if thumbnail:
            raw = str(item.get("原图", "")).replace("\\", "/").lstrip("/")
            stem = raw[len("img/") :].rsplit(".", 1)[0] if raw.startswith("img/") else Path(raw).stem
            depth_thumb = GALLERY_ROOT / "thumbs" / "depth" / f"{stem}.webp"
            if depth_thumb.is_file():
                return depth_thumb
        return _depth_path(item)

    config = LIBRARIES[label]
    field = config["thumb"] if thumbnail else config["image"]
    raw = str(item.get(field, "")).replace("\\", "/").lstrip("/")
    return GALLERY_ROOT / raw


def _item_title(label: str, item: dict) -> str:
    item_id = str(item.get("id", "Untitled"))
    if label == "Poses":
        category = _english_value(item.get("分类", "Pose"))
        return f"{category} pose"
    if label == "Outfits":
        return f"Outfit {item_id}"
    hair_length = _english_value(item.get("发长", ""))
    return f"{hair_length or 'Portrait'} reference"


def _prompt_for(label: str, item: dict) -> str:
    if label == "Poses":
        angle = _english_value(item.get("角度", ""))
        category = _english_value(item.get("分类", ""))
        system = _english_value(item.get("体系", ""))
        return ", ".join(part for part in [f"{angle} view" if angle else "", category, system, "character pose reference"] if part)

    if label == "Outfits":
        english = str(item.get("关键词", "")).strip()
        return ", ".join(part for part in [english, "detailed outfit"] if part)

    section = _english_value(item.get("分区", ""))
    hair_length = _english_value(item.get("发长", ""))
    return ", ".join(part for part in ["character portrait reference", section, hair_length] if part)


def _caption(label: str, item: dict) -> str:
    title = _item_title(label, item)
    return f"{title}\n#{item.get('id', '')}"


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
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    array = np.asarray(image)
    if label == "Poses" and use_depth:
        return array, True, "None"
    if label == "Poses":
        return array, True, "dw_openpose_full"
    return array, True, "None"


def _gallery_ui():
    source_status = (
        f"Data source: {GALLERY_ROOT}"
        if GALLERY_ROOT is not None
        else "Gallery data is not installed. Follow the README to add the ai-pose-gallery-zh dataset."
    )

    with gr.Blocks(elem_id="ai_pose_gallery") as interface:
        gr.HTML(
            """
            <header class="apg-hero">
              <div>
                <h1>AI Pose Gallery</h1>
                <p>Browse pose, outfit, and portrait references for your Forge workflow.</p>
              </div>
              <div class="apg-count">4,345 <span>REFERENCE IMAGES</span></div>
            </header>
            """
        )

        with gr.Row(elem_classes=["apg-toolbar"]):
            library = gr.Radio(list(LIBRARIES), value="Poses", label="Library", elem_classes=["apg-library"])
            search = gr.Textbox(label="Search", placeholder="Category, type, or ID", scale=3)
            search_button = gr.Button("Search", variant="secondary", scale=0, min_width=90)
            random_button = gr.Button("Random pick", variant="secondary", scale=0, min_width=110)

        with gr.Row(elem_classes=["apg-filters"]):
            primary = gr.Dropdown(["All"], value="All", label="System")
            secondary = gr.Dropdown(["All"], value="All", label="Category")
            safe_mode = gr.Checkbox(value=True, label="Safe mode (hide 18+)")
            use_depth = gr.Checkbox(value=False, label="Use depth image")

        page = gr.State(1)
        item_ids = gr.State([])
        selected_path = gr.State("")

        with gr.Row(elem_classes=["apg-workbench"]):
            with gr.Column(scale=4, elem_classes=["apg-browser"]):
                gallery = gr.Gallery(
                    label="References",
                    columns=6,
                    rows=2,
                    height=590,
                    object_fit="contain",
                    allow_preview=False,
                    show_download_button=False,
                    show_share_button=False,
                    elem_classes=["apg-gallery"],
                )
                with gr.Row(elem_classes=["apg-pagination"]):
                    previous = gr.Button("Previous", min_width=90)
                    page_info = gr.Markdown("Loading…", elem_classes=["apg-page-info"])
                    following = gr.Button("Next", min_width=90)

            with gr.Column(scale=2, min_width=320, elem_classes=["apg-inspector"]):
                selected_image = gr.Image(
                    label="Selected reference",
                    type="pil",
                    height=360,
                    interactive=False,
                    show_download_button=True,
                    elem_classes=["apg-selected-image"],
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
                    "Depth images use the `None` preprocessor. Regular pose images preselect `dw_openpose_full`. Choose the ControlNet model separately.",
                    elem_classes=["apg-help"],
                )

        gr.Markdown(source_status, elem_classes=["apg-source-status"])

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
            js="(text) => appendPoseGalleryPrompt(text, 'img2img')",
            show_progress=False,
        )
        send_prompt.click(
            fn=None,
            inputs=[prompt],
            js="(text) => appendPoseGalleryPrompt(text, 'txt2img')",
            show_progress=False,
        )

        try:
            from lib_controlnet.controlnet_ui.controlnet_ui_group import ControlNetUiGroup

            target = next((group for group in ControlNetUiGroup.all_ui_groups if not group.is_img2img), None)
        except Exception:
            target = None

        if target is not None:
            send_controlnet.click(
                fn=_image_for_controlnet,
                inputs=[selected_path, library, use_depth],
                outputs=[target.image.background, target.enabled, target.module],
                show_progress=False,
            ).then(
                fn=None,
                inputs=[prompt],
                js="(text) => appendPoseGalleryPrompt(text, 'txt2img')",
            )
        else:
            send_controlnet.interactive = False

    return [(interface, "AI Pose Gallery", "ai-pose-gallery")]


script_callbacks.on_ui_tabs(_gallery_ui)

