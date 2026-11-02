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
    "姿勢": {
        "key": "pose",
        "file": "pose-zh.json",
        "primary": "体系",
        "secondary": "分类",
        "title": "姿势名称",
        "thumb": "缩略图",
        "image": "原图",
    },
    "服裝": {
        "key": "costume",
        "file": "costume-zh.json",
        "primary": "分类",
        "secondary": None,
        "title": "服装名称",
        "thumb": "缩略图",
        "image": "原图",
    },
    "人物": {
        "key": "portrait",
        "file": "portrait-zh.json",
        "primary": "分区",
        "secondary": "发长",
        "title": "标题",
        "thumb": "缩略图",
        "image": "原图",
    },
}

NSFW_POSE_SYSTEMS = {"全身單人・性感系", "全身单人・性感系", "雙人纏綿", "双人缠绵"}
NSFW_COSTUME_CATEGORIES = {"內衣・睡衣", "内衣・睡衣", "性感穿搭"}


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
    if label == "姿勢":
        return item.get("体系", "") in NSFW_POSE_SYSTEMS
    if label == "服裝":
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
    primary = "" if primary in (None, "", "全部") else primary
    secondary = "" if secondary in (None, "", "全部") else secondary

    results: list[dict] = []
    for item in _load_library(label):
        if safe_mode and _is_nsfw(label, item):
            continue
        if primary and item.get(config["primary"], "") != primary:
            continue
        if secondary and config["secondary"] and item.get(config["secondary"], "") != secondary:
            continue
        if normalized_query:
            haystack = f"{item.get('id', '')} {item.get('检索', '')} {item.get(config['title'], '')}".casefold()
            if normalized_query not in haystack:
                continue
        results.append(item)
    return results


def _option_values(label: str, field: str | None, safe_mode: bool) -> list[str]:
    if not field:
        return ["全部"]
    values = {
        str(item.get(field, "")).strip()
        for item in _load_library(label)
        if item.get(field) and (not safe_mode or not _is_nsfw(label, item))
    }
    return ["全部", *sorted(values)]


def _depth_path(item: dict) -> Path:
    raw = str(item.get("原图", "")).replace("\\", "/").lstrip("/")
    if raw.startswith("img/"):
        stem = raw[len("img/") :].rsplit(".", 1)[0]
        return GALLERY_ROOT / "img" / "depth" / f"{stem}.webp"
    return GALLERY_ROOT / raw


def _asset_path(label: str, item: dict, *, thumbnail: bool, use_depth: bool) -> Path:
    if label == "姿勢" and use_depth:
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
    config = LIBRARIES[label]
    return str(item.get(config["title"], item.get("id", "未命名")))


def _prompt_for(label: str, item: dict) -> str:
    if label == "姿勢":
        angle = item.get("角度", "")
        category = item.get("分类", "")
        title = _item_title(label, item)
        system = item.get("体系", "")
        return ", ".join(part for part in [f"{angle}視角" if angle else "", title, category, system, "character pose reference"] if part)

    if label == "服裝":
        english = str(item.get("关键词", "")).strip()
        title = _item_title(label, item)
        return ", ".join(part for part in [english, title, "detailed outfit"] if part)

    hair_length = item.get("发长", "")
    hairstyle = item.get("发型名称", "")
    title = _item_title(label, item)
    return ", ".join(part for part in ["character portrait reference", hair_length, hairstyle, title] if part)


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
        return [], [], 1, "找不到圖庫資料。請安裝原始 ai-pose-gallery-zh 資料庫。"

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

    info = f"第 {page} / {total_pages} 頁，共 {len(items):,} 筆"
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
    primary_value = primary if primary in primary_options else "全部"
    secondary_value = secondary if secondary in secondary_options else "全部"
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
        gr.update(choices=primary_options, value=primary_value, label=config["primary"]),
        gr.update(
            choices=secondary_options,
            value=secondary_value,
            label=config["secondary"] or "第二篩選",
            visible=bool(config["secondary"]),
        ),
        gr.update(visible=label == "姿勢"),
    )


def _change_library(label, query, safe_mode, use_depth):
    return _refresh(label, query, "全部", "全部", safe_mode, use_depth, 1)


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
        return None, "", "", "尚未選取素材"

    item = _find_item(label, item_ids[evt.index])
    if item is None:
        return None, "", "", "找不到選取的素材"

    path = _asset_path(label, item, thumbnail=False, use_depth=bool(use_depth))
    if not path.is_file():
        return None, "", "", f"找不到圖片：{path.name}"

    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    title = _item_title(label, item)
    details = f"{title}\nID：{item.get('id', '')}"
    if label == "姿勢":
        details += f"\n模式：{'Depth 控制圖' if use_depth else '姿勢參考圖'}"
    return image, str(path), _prompt_for(label, item), details


def _random_item(label, query, primary, secondary, safe_mode, use_depth):
    items = _filtered_items(label, query, primary, secondary, safe_mode)
    if not items:
        return None, "", "", "目前篩選條件沒有素材"
    item = random.choice(items)
    path = _asset_path(label, item, thumbnail=False, use_depth=bool(use_depth))
    if not path.is_file():
        return None, "", "", "隨機素材的圖片不存在"
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    details = f"{_item_title(label, item)}\nID：{item.get('id', '')}"
    return image, str(path), _prompt_for(label, item), details


def _image_for_controlnet(path: str, label: str, use_depth: bool):
    if not path:
        raise gr.Error("請先選取一張素材")
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    array = np.asarray(image)
    if label == "姿勢" and use_depth:
        return array, True, "None"
    if label == "姿勢":
        return array, True, "dw_openpose_full"
    return array, True, "None"


def _gallery_ui():
    source_status = (
        f"資料來源：{GALLERY_ROOT}"
        if GALLERY_ROOT is not None
        else "尚未安裝圖庫資料；請依 README 將 ai-pose-gallery-zh 放在相鄰資料夾。"
    )

    with gr.Blocks(elem_id="ai_pose_gallery") as interface:
        gr.HTML(
            """
            <header class="apg-hero">
              <div>
                <h1>AI 姿勢圖庫</h1>
                <p>挑選姿勢、服裝與人物參考，直接送進 Forge 工作流程。</p>
              </div>
              <div class="apg-count">4,345 <span>筆中文素材</span></div>
            </header>
            """
        )

        with gr.Row(elem_classes=["apg-toolbar"]):
            library = gr.Radio(list(LIBRARIES), value="姿勢", label="圖庫", elem_classes=["apg-library"])
            search = gr.Textbox(label="搜尋", placeholder="名稱、分類或 ID", scale=3)
            search_button = gr.Button("搜尋", variant="secondary", scale=0, min_width=90)
            random_button = gr.Button("隨機挑選", variant="secondary", scale=0, min_width=110)

        with gr.Row(elem_classes=["apg-filters"]):
            primary = gr.Dropdown(["全部"], value="全部", label="體系")
            secondary = gr.Dropdown(["全部"], value="全部", label="分類")
            safe_mode = gr.Checkbox(value=True, label="安全模式（隱藏 18+）")
            use_depth = gr.Checkbox(value=False, label="使用 Depth 圖")

        page = gr.State(1)
        item_ids = gr.State([])
        selected_path = gr.State("")

        with gr.Row(elem_classes=["apg-workbench"]):
            with gr.Column(scale=4, elem_classes=["apg-browser"]):
                gallery = gr.Gallery(
                    label="素材",
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
                    previous = gr.Button("上一頁", min_width=90)
                    page_info = gr.Markdown("載入中…", elem_classes=["apg-page-info"])
                    following = gr.Button("下一頁", min_width=90)

            with gr.Column(scale=2, min_width=320, elem_classes=["apg-inspector"]):
                selected_image = gr.Image(
                    label="選中素材",
                    type="pil",
                    height=360,
                    interactive=False,
                    show_download_button=True,
                    elem_classes=["apg-selected-image"],
                )
                selection_details = gr.Textbox(label="素材資訊", lines=3, interactive=False)
                prompt = gr.Textbox(
                    label="提示詞",
                    lines=5,
                    placeholder="選取素材後會產生可編輯的提示詞",
                    show_copy_button=True,
                )
                send_controlnet = gr.Button("送到 txt2img＋ControlNet 1", variant="primary")
                send_img2img = gr.Button("送到 img2img", variant="secondary")
                send_prompt = gr.Button("只送提示詞到 txt2img", variant="secondary")
                gr.Markdown(
                    "Depth 圖會使用 `None` 預處理器；普通姿勢圖會預選 `dw_openpose_full`。ControlNet 模型仍由你選擇。",
                    elem_classes=["apg-help"],
                )

        gr.Markdown(source_status, elem_classes=["apg-source-status"])

        outputs = [gallery, item_ids, page, page_info, primary, secondary, use_depth]
        refresh_inputs = [library, search, primary, secondary, safe_mode, use_depth, page]

        interface.load(
            fn=lambda: _refresh("姿勢", "", "全部", "全部", True, False, 1),
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
                source_text_component=prompt,
                source_image_component=selected_image,
            )
        )
        infotext_utils.register_paste_params_button(
            infotext_utils.ParamBinding(
                paste_button=send_prompt,
                tabname="txt2img",
                source_text_component=prompt,
            )
        )
        infotext_utils.register_paste_params_button(
            infotext_utils.ParamBinding(
                paste_button=send_controlnet,
                tabname="txt2img",
                source_text_component=prompt,
            )
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
            )
        else:
            send_controlnet.interactive = False

    return [(interface, "AI 姿勢圖庫", "ai-pose-gallery")]


script_callbacks.on_ui_tabs(_gallery_ui)

