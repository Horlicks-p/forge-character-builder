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
