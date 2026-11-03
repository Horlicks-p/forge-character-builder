"""Tier-2 smoke test: build and serve the UI with Gradio 4.40 (Forge Neo's
version) against a stubbed Forge. Skipped when Gradio is not installed."""

import importlib.util
import json
import sys
import unittest
import urllib.request
from pathlib import Path

HAS_GRADIO = importlib.util.find_spec("gradio") is not None

sys.path.insert(0, str(Path(__file__).parent / "harness"))


def gradio_update():
    import gradio

    return gradio.update()


@unittest.skipUnless(HAS_GRADIO, "gradio 4.40 not installed")
class UIBuildTests(unittest.TestCase):
    def _serve(self, **kwargs):
        from forge_harness import build_demo

        demo = build_demo(**kwargs)
        _, url, _ = demo.launch(prevent_thread_lock=True, server_port=None, quiet=True)
        self.addCleanup(demo.close)
        with urllib.request.urlopen(url.rstrip("/") + "/config") as response:
            return demo, json.load(response)

    def test_builds_and_serves_with_controlnet(self):
        import gradio

        self.assertEqual(gradio.__version__, "4.40.0")
        _, config = self._serve(with_controlnet=True)
        labels = {c.get("props", {}).get("label") for c in config["components"]}
        for label in ("Character Builder", "Pose Gallery", "Final Prompt (editable)", "Hair color", "Eye shape", "Hairstyles", "Outfits", "Poses"):
            self.assertIn(label, labels)
        values = {str(c.get("props", {}).get("value")) for c in config["components"]}
        self.assertIn("Send All", values)
        self.assertGreater(len(config["dependencies"]), 40)

    @staticmethod
    def _choices(config, label):
        for component in config["components"]:
            props = component.get("props", {})
            if props.get("label") == label:
                return [choice[1] if isinstance(choice, (list, tuple)) else choice for choice in props.get("choices", [])]
        raise AssertionError(f"no component labelled {label!r}")

    def test_portraits_hidden_by_default(self):
        _, config = self._serve(with_controlnet=True)
        self.assertEqual(self._choices(config, "Library"), ["Poses", "Outfits"])
        self.assertIn("anhao_outfit", self._choices(config, "Source"))
        texts = " ".join(str(c.get("props", {}).get("label")) for c in config["components"])
        self.assertIn("Data sources", texts)

    def test_settings_toggle_portraits_and_anhao_outfits(self):
        _, config = self._serve(with_controlnet=True, options={"fcb_show_portraits": True, "fcb_show_anhao_outfits": False})
        self.assertEqual(self._choices(config, "Library"), ["Poses", "Outfits", "Portraits"])
        self.assertNotIn("anhao_outfit", self._choices(config, "Source"))

    def _legacy_script(self):
        from forge_harness import build_demo

        build_demo(with_controlnet=True)
        return sys.modules["character_builder_script"]

    def test_legacy_send_resolves_image_and_preprocessor_together(self):
        script = self._legacy_script()
        # A pose picked in Normal mode, then "Use depth image" ticked: the
        # depth image goes with None (before: the normal image went with None).
        normal, module = script._controlnet_source("Poses:1", False, True)
        self.assertEqual((normal.name, module), ("a.png", "canny"))
        depth, module = script._controlnet_source("Poses:1", True, True)
        self.assertEqual((depth.parent.name, depth.name, module), ("pose", "a.webp", "None"))
        self.assertIn("depth", depth.parts)
        # Non-pose libraries ignore the depth checkbox and use None.
        outfit, module = script._controlnet_source("Outfits:10", True, True)
        self.assertEqual((outfit.name, module), ("c.png", "None"))

        array, enabled, module = script._image_for_controlnet("Poses:1", True, True)
        self.assertEqual((array.shape, enabled, module), ((160, 120, 3), True, "None"))

    def test_legacy_send_rejects_stale_or_hidden_selection(self):
        import gradio

        script = self._legacy_script()
        for ref in ("", "Bogus:1", "Poses:999", "/tmp/x.png"):
            with self.assertRaises(gradio.Error, msg=ref):
                script._controlnet_source(ref, False, True)
        with self.assertRaises(gradio.Error):
            script._controlnet_source("Poses:2", False, True)  # 18+ while Safe mode is on
        self.assertEqual(script._controlnet_source("Poses:2", False, False)[1], "canny")

    def test_legacy_preview_follows_depth_toggle(self):
        script = self._legacy_script()
        _, details = script._refresh_preview("Poses:1", True)
        self.assertIn("Depth control image", details)
        _, details = script._refresh_preview("Poses:1", False)
        self.assertIn("Pose reference image", details)
        image, details = script._refresh_preview("", True)  # nothing selected: no change
        self.assertEqual((image, details), (gradio_update(), gradio_update()))

    def test_builds_without_controlnet(self):
        _, config = self._serve(with_controlnet=False)
        texts = " ".join(str(c.get("props", {}).get("value")) for c in config["components"])
        self.assertIn("ControlNet unavailable", texts)


@unittest.skipUnless(HAS_GRADIO, "gradio 4.40 not installed")
class PickParsingTests(unittest.TestCase):
    def test_parse_pick(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from character_builder_ui.builder_tab import _parse_pick

        self.assertEqual(_parse_pick("3:1699999999999"), 3)
        self.assertEqual(_parse_pick("0:1"), 0)
        for bad in ("", None, "abc", ":5"):
            self.assertIsNone(_parse_pick(bad), bad)


if __name__ == "__main__":
    unittest.main()
