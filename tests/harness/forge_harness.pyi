"""Minimal Forge Neo stand-in for UI testing without a WebUI.

Builds the same shape Forge does: a txt2img Blocks holding a prompt box and a
fake ControlNet group (``image.background`` / ``enabled`` / ``module``), then
the extension's ``on_ui_tabs`` interface, then a main demo that renders both.

Requires Gradio 4.40 (Forge Neo's version). Not part of the shipped extension.

    python tests/harness/forge_harness.py [--port 7861]

Environment: ANHAO_GALLERY_ROOT / CHARADB_ROOT as for the extension. When
ANHAO_GALLERY_ROOT is unset, a tiny self-made dataset is generated in a temp dir.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import gradio as gr

ROOT = Path(__file__).resolve().parents[2]

from gradio.events import Dependency

class FakeLogicalImage(gr.Textbox):
    """Neo's ForgeCanvas.background is a Textbox subclass fed numpy arrays."""

    def postprocess(self, value):
        if value is None:
            return ""
        shape = getattr(value, "shape", None)
        return f"array {tuple(shape)}" if shape is not None else str(value)

    def get_block_name(self):
        return "textbox"


class FakeControlNetGroup:
    all_ui_groups: list = []

    def __init__(self, is_img2img: bool):
        self.is_img2img = is_img2img
        self.image = types.SimpleNamespace(background=FakeLogicalImage(label="ControlNet image", elem_id=f"cn_image_{'img2img' if is_img2img else 'txt2img'}"))
        self.enabled = gr.Checkbox(label="Enable", value=False, elem_id=f"cn_enabled_{'img2img' if is_img2img else 'txt2img'}")
        self.module = gr.Dropdown(["None", "canny", "depth_anything"], value="None", label="Preprocessor", elem_id=f"cn_module_{'img2img' if is_img2img else 'txt2img'}")
        FakeControlNetGroup.all_ui_groups.append(self)


def _install_stubs(with_controlnet: bool) -> list:
    callbacks: list = []
    modules = types.ModuleType("modules")
    script_callbacks = types.ModuleType("modules.script_callbacks")
    script_callbacks.on_ui_tabs = callbacks.append
    infotext_utils = types.ModuleType("modules.infotext_utils")
    infotext_utils.ParamBinding = lambda **kwargs: kwargs
    infotext_utils.register_paste_params_button = lambda binding: None
    modules.script_callbacks, modules.infotext_utils = script_callbacks, infotext_utils
    sys.modules.update({"modules": modules, "modules.script_callbacks": script_callbacks, "modules.infotext_utils": infotext_utils})

    for name in [n for n in sys.modules if n.startswith("lib_controlnet")]:
        del sys.modules[name]
    if with_controlnet:
        names = ["lib_controlnet", "lib_controlnet.controlnet_ui", "lib_controlnet.controlnet_ui.controlnet_ui_group"]
        for name in names:
            sys.modules[name] = types.ModuleType(name)
        sys.modules[names[-1]].ControlNetUiGroup = FakeControlNetGroup
    return callbacks


def make_demo_dataset(root: Path) -> Path:
    """Self-made Anhao-shaped dataset with real (generated) images."""
    from PIL import Image, ImageDraw

    poses = [
        {"id": 1, "体系": "全身单人・标准系", "分类": "站姿", "角度": "正面", "原图": "img/pose/a.png", "缩略图": "thumbs/pose/a.png"},
        {"id": 2, "体系": "全身单人・性感系", "分类": "坐姿", "角度": "侧面", "原图": "img/pose/b.png", "缩略图": "thumbs/pose/b.png"},
        {"id": 3, "体系": "全身单人・标准系", "分类": "蹲姿", "角度": "俯视", "原图": "img/pose/missing.png", "缩略图": "thumbs/pose/c.png"},
    ]
    outfits = [{"id": 10, "分类": "春季穿搭", "关键词": "white blouse pleated mini skirt", "原图": "img/costume/c.png", "缩略图": "thumbs/costume/c.png"}]
    (root / "data").mkdir(parents=True, exist_ok=True)
    for name, items in (("pose-zh.json", poses), ("costume-zh.json", outfits), ("portrait-zh.json", [])):
        (root / "data" / name).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def image(relative: str, color, size=(96, 128)):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", size, color)
        ImageDraw.Draw(img).text((8, 8), relative.split("/")[-1], fill=(255, 255, 255))
        img.save(path)

    image("img/pose/a.png", (40, 90, 160), (120, 160))
    image("thumbs/pose/a.png", (40, 90, 160))
    image("img/depth/pose/a.webp", (90, 90, 90), (120, 160))
    image("thumbs/depth/pose/a.webp", (90, 90, 90))
    image("img/pose/b.png", (160, 60, 60))
    image("thumbs/pose/b.png", (160, 60, 60))
    image("thumbs/pose/c.png", (60, 140, 60))
    image("img/costume/c.png", (200, 180, 120))
    image("thumbs/costume/c.png", (200, 180, 120))
    return root


def build_demo(*, with_controlnet: bool = True) -> gr.Blocks:
    if not os.environ.get("ANHAO_GALLERY_ROOT"):
        os.environ["ANHAO_GALLERY_ROOT"] = str(make_demo_dataset(Path(tempfile.mkdtemp(prefix="fcb-anhao-"))))
    callbacks = _install_stubs(with_controlnet)
    FakeControlNetGroup.all_ui_groups.clear()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))  # what Forge Neo does for each extension root

    with gr.Blocks() as txt2img_interface:
        txt2img_prompt = gr.Textbox(label="Prompt", elem_id="txt2img_prompt", lines=3)
        if with_controlnet:
            FakeControlNetGroup(is_img2img=False)
            FakeControlNetGroup(is_img2img=True)

    spec = importlib.util.spec_from_file_location("character_builder_script", ROOT / "scripts" / "character_builder.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (interface, title, tab_id), = callbacks[0]()

    shim = """
    <script>
      window.gradioApp = () => document;
      window.updateInput = (el) => el.dispatchEvent(new Event('input', { bubbles: true }));
      window.switch_to_txt2img = () => { window.__switchedTo = 'txt2img'; };
      window.switch_to_img2img = () => { window.__switchedTo = 'img2img'; };
    </script>
    <script>%s</script>
    """ % (ROOT / "javascript" / "character_builder.js").read_text(encoding="utf-8")
    css = (ROOT / "style.css").read_text(encoding="utf-8")

    with gr.Blocks(head=shim, css=css, title="Forge harness") as demo:
        with gr.Tabs():
            with gr.Tab("txt2img", id="txt2img"):
                txt2img_interface.render()
            with gr.Tab(title, id=tab_id, elem_id=f"tab_{tab_id}"):
                interface.render()
    demo.harness_prompt = txt2img_prompt
    return demo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--no-controlnet", action="store_true")
    args = parser.parse_args()
    build_demo(with_controlnet=not args.no_controlnet).launch(server_port=args.port)