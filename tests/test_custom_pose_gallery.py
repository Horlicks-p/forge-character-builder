"""Pose Gallery -> Custom Poses against a stubbed Forge. Needs Gradio 4.40."""

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

HAS_GRADIO = importlib.util.find_spec("gradio") is not None

sys.path.insert(0, str(Path(__file__).parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def png_file(folder: Path, name: str, color=(10, 20, 30), size=(64, 48), mode="RGB") -> str:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    path = folder / name
    path.write_bytes(buffer.getvalue())
    return str(path)


@unittest.skipUnless(HAS_GRADIO, "gradio 4.40 not installed")
class CustomGalleryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        environ = mock.patch.dict(os.environ, {"FCB_CUSTOM_POSE_DIR": str(self.base / "library")})
        environ.start()
        self.addCleanup(environ.stop)

        from forge_harness import build_demo

        self.demo = build_demo(with_controlnet=True)
        self.script = sys.modules["character_builder_script"]
        self.store = self.script.CUSTOM_STORE
        self.panel = self.script.custom_pose_panel
        self.counter = 0

    def add(self, **options):
        self.counter += 1
        return self.store.add(png_file(self.base, f"pose{self.counter}.png", (self.counter, 20, 30)), **options).item

    def refresh(self, query="", safe=True, page=1):
        return self.script._refresh("Custom Poses", query, "All", "All", safe, False, page)

    def events(self):
        form = [mock.sentinel.__getattr__(f"form{i}") for i in range(8)]
        manage = [mock.sentinel.__getattr__(f"manage{i}") for i in range(11)]
        selection = [mock.sentinel.__getattr__(f"selection{i}") for i in range(5)]
        with mock.patch.object(self.panel.CustomPoseEvents, "_wire"):
            return self.panel.CustomPoseEvents(
                self.store,
                self.panel.AddForm(*form),
                self.panel.ManageControls(*manage),
                add_button=mock.sentinel.add,
                status=mock.sentinel.status,
                library=mock.sentinel.library,
                search=mock.sentinel.search,
                page=mock.sentinel.page,
                safe_mode=mock.sentinel.safe,
                selected_ref=mock.sentinel.ref,
                selection_outputs=selection,
                refresh_fn=self.script._refresh,
                refresh_inputs=[],
                refresh_outputs=[],
                non_custom_controls=[mock.sentinel.random, mock.sentinel.img2img],
                source_status=mock.sentinel.source_status,
                source_status_fn=lambda: "",
            )


class GalleryTests(CustomGalleryTestCase):
    def test_empty_library_explains_itself(self):
        gallery, ids, page, info, *_ = self.refresh()
        self.assertEqual((gallery, ids, page), ([], [], 1))
        self.assertEqual(info, "No custom poses yet. Add a local image to get started.")

    def test_filters_and_depth_toggle_are_hidden(self):
        *_, primary, secondary, use_depth = self.refresh()
        self.assertEqual((primary["visible"], secondary["visible"], use_depth["visible"]), (False, False, False))

    def test_anhao_libraries_show_their_filters_again(self):
        *_, primary, _secondary, use_depth = self.script._refresh("Poses", "", "All", "All", True, False, 1)
        self.assertTrue(primary["visible"])
        self.assertTrue(use_depth["visible"])

    def test_gallery_lists_newest_first_and_searches(self):
        first = self.add(name="Standing")
        second = self.add(name="Sitting")
        gallery, ids, _, info, *_ = self.refresh()
        self.assertEqual(ids, [f"Custom Poses:{second.id}", f"Custom Poses:{first.id}"])
        self.assertIn("Sitting", gallery[0][1])
        self.assertIn("2 items", info)
        self.assertEqual(self.refresh("stand")[1], [f"Custom Poses:{first.id}"])
        self.assertEqual(self.refresh("zzz")[3], self.panel.MSG_NO_MATCH)

    def test_pagination(self):
        with mock.patch.object(self.script, "PAGE_SIZE", 2), mock.patch("character_builder_core.session.PAGE_SIZE", 2):
            for _ in range(5):
                self.add()
            first = self.refresh(page=1)
            third = self.refresh(page=99)
        self.assertEqual(len(first[1]), 2)
        self.assertEqual((len(third[1]), third[2]), (1, 3))

    def test_safe_mode_filters_the_gallery(self):
        self.add(sensitive=True)
        self.assertEqual(self.refresh(safe=True)[3], self.panel.MSG_ALL_HIDDEN)
        self.assertEqual(len(self.refresh(safe=False)[1]), 1)

    def test_missing_thumbnail_keeps_the_card_selectable(self):
        item = self.add()
        self.store.thumbnail_path(item.id).unlink()
        gallery, ids, *_ = self.refresh()
        self.assertEqual(len(gallery), len(ids))
        self.assertIn("no-preview", gallery[0][0])

    def test_broken_library_reports_why(self):
        self.store.index_path.write_bytes(b"broken")
        self.assertIn("read-only", self.refresh()[3])
        self.assertIn("read-only", self.events().mode_update("Custom Poses")[mock.sentinel.status]["value"])

    def test_long_names_do_not_cover_the_thumbnail(self):
        self.add(name="x" * 120)
        caption = self.refresh()[0][0][1]
        self.assertEqual(len(caption), self.panel.CAPTION_LENGTH)
        self.assertTrue(caption.endswith("…"))
        self.assertEqual(self.store.items()[0].name, "x" * 120)

    def test_source_row_reports_count_and_folder(self):
        from character_builder_core import sources

        self.add()
        row = sources.custom_pose_health(self.store)
        self.assertTrue(row.ok)
        self.assertIn("1 saved", row.detail)
        self.assertIn(str(self.store.root), row.detail)


class SelectionTests(CustomGalleryTestCase):
    def test_pick_selects_the_pose(self):
        item = self.add(name="Pose A", input_kind="depth_map")
        ids = self.refresh()[1]
        image, ref, prompt, details, _ = self.script._pick_item("0:1", "Custom Poses", False, ids, True)
        self.assertEqual(ref, f"Custom Poses:{item.id}")
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(prompt, "")
        for text in ("Pose A", "Depth map", "Preprocessor: None", item.created_at):
            self.assertIn(text, details)

    def test_pick_ignores_a_gallery_from_another_library(self):
        from gradio import update

        ids = self.refresh()[1] or [f"Custom Poses:{self.add().id}"]
        unchanged = tuple(update() for _ in range(5))
        self.assertEqual(self.script._pick_item("0:1", "Poses", False, ids, True), unchanged)
        self.assertEqual(self.script._pick_item("0:1", "Custom Poses", False, ["1"], True), unchanged)

    def test_pick_refuses_deleted_and_hidden_poses(self):
        hidden = self.add(sensitive=True)
        ref = f"Custom Poses:{hidden.id}"
        self.assertIn("Safe Mode", self.script._pick_item("0:1", "Custom Poses", False, [ref], True)[3])
        self.store.delete(hidden.id)
        image, value, _, details, _ = self.script._pick_item("0:1", "Custom Poses", False, [ref], False)
        self.assertEqual((image, value, details), (None, "", "Item no longer exists"))

    def test_bad_pick_clears_the_selection(self):
        for bad in ("", "5:1", "x"):
            self.assertEqual(self.script._pick_item(bad, "Custom Poses", False, [], True)[1], "", bad)

    def test_random_pick_is_inert_for_custom_poses(self):
        self.add()
        self.assertEqual(self.script._random_item("Custom Poses", "", "All", "All", True, False)[1], "")


class SendTests(CustomGalleryTestCase):
    def test_send_maps_input_kind_to_preprocessor(self):
        for kind, expected in (
            ("reference_canny", "canny"),
            ("preprocessed_canny", "None"),
            ("openpose_skeleton", "None"),
            ("depth_map", "None"),
        ):
            item = self.add(input_kind=kind)
            array, enabled, module = self.script._image_for_controlnet(f"Custom Poses:{item.id}", True, True)
            self.assertEqual((array.shape, enabled, module), ((48, 64, 3), True, expected))

    def test_stale_or_hidden_selection_cannot_be_sent(self):
        import gradio

        item = self.add(sensitive=True)
        ref = f"Custom Poses:{item.id}"
        with self.assertRaisesRegex(gradio.Error, "Safe Mode"):
            self.script._image_for_controlnet(ref, False, True)
        self.store.delete(item.id)
        with self.assertRaisesRegex(gradio.Error, "no longer exists"):
            self.script._image_for_controlnet(ref, False, False)

    def test_preview_matches_what_is_sent(self):
        import numpy as np

        path = png_file(self.base, "alpha.png", (255, 0, 0, 0), mode="RGBA")
        item = self.store.add(path, input_kind="openpose_skeleton").item
        ref = f"Custom Poses:{item.id}"
        preview = self.panel.select(self.store, ref, True)[0]
        array, _, _ = self.script._image_for_controlnet(ref, False, True)
        self.assertTrue((np.asarray(preview) == array).all())
        self.assertEqual(array[0, 0].tolist(), [0, 0, 0])

    def test_anhao_send_is_unchanged(self):
        array, enabled, module = self.script._image_for_controlnet("Poses:1", False, True)
        self.assertEqual((array.shape, enabled, module), ((160, 120, 3), True, "canny"))


class FormTests(CustomGalleryTestCase):
    def test_save_selects_the_new_pose_and_closes_the_form(self):
        events = self.events()
        result = events.save_upload(png_file(self.base, "front.png"), "", "reference_canny", False, True)
        item = self.store.items()[0]
        self.assertEqual(item.name, "front")
        self.assertEqual(result[events.selection_outputs[1]], f"Custom Poses:{item.id}")
        self.assertEqual(result[events.status], "Saved: front")
        self.assertEqual(result[events.form.group]["visible"], False)
        self.assertEqual((result[events.search], result[events.page]), ("", 1))

    def test_save_without_a_file_keeps_the_form(self):
        events = self.events()
        self.assertEqual(events.save_upload(None, "x", "reference_canny", False, True), {events.status: self.panel.MSG_NEEDS_FILE})

    def test_invalid_upload_reports_and_keeps_the_form(self):
        events = self.events()
        bad = self.base / "bad.png"
        bad.write_bytes(b"nope")
        result = events.save_upload(str(bad), "", "reference_canny", False, True)
        self.assertEqual(list(result), [events.status])
        self.assertIn("not a valid", result[events.status])
        self.assertEqual(self.store.items(), [])

    def test_sensitive_save_under_safe_mode_is_not_selected(self):
        events = self.events()
        result = events.save_upload(png_file(self.base, "s.png"), "", "reference_canny", True, True)
        self.assertEqual(result[events.status], "Pose saved but hidden by Safe Mode. Disable Safe Mode to view it.")
        self.assertEqual(result[events.selection_outputs[1]], "")
        self.assertIsNone(result[events.selection_outputs[0]])
        self.assertEqual(len(self.store.items()), 1)

    def test_duplicate_selects_the_existing_pose(self):
        events = self.events()
        path = png_file(self.base, "same.png")
        events.save_upload(path, "", "reference_canny", False, True)
        result = events.save_upload(path, "other", "depth_map", False, True)
        self.assertEqual(result[events.status], "Already in the library: same")
        self.assertEqual(len(self.store.items()), 1)
        self.assertTrue(result[events.selection_outputs[1]].startswith("Custom Poses:"))

    def test_sensitive_duplicate_under_safe_mode_stays_hidden(self):
        events = self.events()
        path = png_file(self.base, "same.png")
        self.store.add(path, sensitive=True)
        result = events.save_upload(path, "", "reference_canny", False, True)
        self.assertEqual(result[events.status], self.panel.MSG_SAVED_HIDDEN)
        self.assertEqual(result[events.selection_outputs[1]], "")

    def test_shrunk_canny_map_warns_about_lines(self):
        events = self.events()
        path = png_file(self.base, "wide.png", size=(3000, 100))
        result = events.save_upload(path, "", "preprocessed_canny", False, True)
        self.assertIn("thin lines", result[events.status])

    def test_preview_shows_the_upload_without_saving(self):
        events = self.events()
        path = png_file(self.base, "alpha.png", (255, 0, 0, 0), mode="RGBA")
        white = events.preview_upload(path, "reference_canny")[0]
        black = events.preview_upload(path, "depth_map")[0]
        self.assertEqual((white.getpixel((0, 0)), black.getpixel((0, 0))), ((255, 255, 255), (0, 0, 0)))
        self.assertEqual(self.store.items(), [])
        self.assertEqual(events.preview_upload(None, "depth_map"), (None, ""))

    def test_preview_reports_invalid_files(self):
        events = self.events()
        bad = self.base / "bad.png"
        bad.write_bytes(b"nope")
        image, message = events.preview_upload(str(bad), "depth_map")
        self.assertIsNone(image)
        self.assertIn("not a valid", message)


class ManageTests(CustomGalleryTestCase):
    def test_manage_view_shows_only_for_visible_custom_poses(self):
        events = self.events()
        item = self.add(name="Pose", input_kind="depth_map", sensitive=True)
        ref = f"Custom Poses:{item.id}"
        shown = events.manage_view(ref, False)
        self.assertEqual((shown[0]["visible"], shown[1], shown[2], shown[3]), (True, "Pose", "depth_map", True))
        self.assertFalse(events.manage_view(ref, True)[0]["visible"])
        for other in ("", "Poses:1"):
            self.assertFalse(events.manage_view(other, False)[0]["visible"])

    def test_edit_updates_details_and_preprocessor(self):
        events = self.events()
        item = self.add(name="Old")
        ref = f"Custom Poses:{item.id}"
        result = events.save_details(ref, "New", "depth_map", False, True)
        self.assertEqual(result[events.status], "Saved details: New")
        self.assertIn("Preprocessor: None", result[events.selection_outputs[3]])
        self.assertEqual(self.script._image_for_controlnet(ref, False, True)[2], "None")

    def test_marking_sensitive_under_safe_mode_clears_the_selection(self):
        events = self.events()
        item = self.add()
        result = events.save_details(f"Custom Poses:{item.id}", "Name", "reference_canny", True, True)
        self.assertEqual(result[events.selection_outputs[1]], "")

    def test_edit_of_a_deleted_pose_clears_the_selection(self):
        events = self.events()
        item = self.add()
        self.store.delete(item.id)
        result = events.save_details(f"Custom Poses:{item.id}", "Name", "reference_canny", False, True)
        self.assertEqual((result[events.status], result[events.selection_outputs[1]]), ("Item no longer exists", ""))

    def test_blank_name_is_reported(self):
        events = self.events()
        item = self.add()
        result = events.save_details(f"Custom Poses:{item.id}", "   ", "reference_canny", False, True)
        self.assertEqual(list(result), [events.status])
        self.assertEqual(self.store.get(item.id), item)

    def test_delete_asks_first_and_can_be_cancelled(self):
        events = self.events()
        item = self.add(name="Keep me")
        asked = events.ask_delete(f"Custom Poses:{item.id}")
        self.assertIn("Keep me", asked[events.manage.confirm_text])
        self.assertIn("cannot be undone", asked[events.manage.confirm_text])
        self.assertEqual(asked[events.manage.delete_target], item.id)
        self.assertTrue(asked[events.manage.confirm_group]["visible"])
        self.assertIsNotNone(self.store.get(item.id))
        self.assertFalse(events.cancel_delete()[0]["visible"])
        self.assertIsNotNone(self.store.get(item.id))

    def test_confirm_deletes_the_named_pose_and_clears_the_selection(self):
        events = self.events()
        keep, remove = self.add(), self.add()
        result = events.confirm_delete(remove.id)
        self.assertEqual(self.store.items(), [keep])
        self.assertEqual(result[events.selection_outputs[1]], "")
        self.assertIsNone(result[events.selection_outputs[0]])

    def test_confirm_of_an_already_deleted_pose_does_not_touch_others(self):
        events = self.events()
        keep, gone = self.add(), self.add()
        self.store.delete(gone.id)
        result = events.confirm_delete(gone.id)
        self.assertEqual(result[events.status], "Item no longer exists")
        self.assertEqual(self.store.items(), [keep])

    def test_reconcile_clears_only_stale_custom_selections(self):
        from gradio import update

        unchanged = tuple(update() for _ in range(5))
        item = self.add(sensitive=True)
        ref = f"Custom Poses:{item.id}"
        self.assertEqual(self.panel.reconcile(self.store, ref, False), unchanged)
        self.assertEqual(self.panel.reconcile(self.store, "Poses:1", True), unchanged)
        self.assertEqual(self.panel.reconcile(self.store, ref, True)[3], "This pose is hidden by Safe Mode")
        self.store.delete(item.id)
        self.assertEqual(self.panel.reconcile(self.store, ref, False)[3], "Item no longer exists")


class WiringTests(CustomGalleryTestCase):
    def _config(self, **kwargs):
        import json
        import urllib.request

        from forge_harness import build_demo

        demo = build_demo(**kwargs)
        _, url, _ = demo.launch(prevent_thread_lock=True, server_port=None, quiet=True)
        self.addCleanup(demo.close)
        with urllib.request.urlopen(url.rstrip("/") + "/config") as response:
            return json.load(response)

    @staticmethod
    def _by_elem(config):
        return {c["props"]["elem_id"]: c["id"] for c in config["components"] if c.get("props", {}).get("elem_id")}

    def test_upload_events_are_bound(self):
        config = self._config(with_controlnet=True)
        ids = self._by_elem(config)
        targets = {tuple(t) for dep in config["dependencies"] for t in dep.get("targets", [])}
        for elem, event in (
            ("fcb_custom_file", "upload"),
            ("fcb_custom_file", "clear"),
            ("fcb_custom_kind", "input"),
            ("fcb_custom_save", "click"),
            ("fcb_custom_cancel", "click"),
            ("fcb_custom_add", "click"),
            ("fcb_custom_edit_save", "click"),
            ("fcb_custom_delete", "click"),
            ("fcb_custom_confirm_yes", "click"),
            ("fcb_custom_confirm_no", "click"),
        ):
            self.assertIn((ids[elem], event), targets, elem)

    def test_source_status_refreshes_after_library_changes(self):
        config = self._config(with_controlnet=True)
        panels = {c["id"] for c in config["components"] if "fcb-source-status" in (c.get("props", {}).get("elem_classes") or [])}
        writers = [dep for dep in config["dependencies"] if panels & set(dep["outputs"])]
        self.assertEqual(len(writers), 4)  # page load, save, edit, delete

    def test_add_controls_start_hidden(self):
        config = self._config(with_controlnet=True)
        by_id = {c["id"]: c for c in config["components"]}
        ids = self._by_elem(config)
        self.assertFalse(by_id[ids["fcb_custom_add"]]["props"]["visible"])
        self.assertFalse(by_id[ids["fcb_custom_status"]]["props"]["visible"])

    def test_send_switches_tab_even_with_an_empty_prompt(self):
        config = self._config(with_controlnet=True)
        scripts = [dep.get("js") or "" for dep in config["dependencies"]]
        self.assertTrue(any("characterBuilderSwitchTab('txt2img')" in script for script in scripts))

    def test_works_without_anhao_and_without_controlnet(self):
        with mock.patch("character_builder_core.anhao.find_gallery_root", return_value=None), mock.patch(
            "character_builder_core.anhao.find_gallery_candidate", return_value=None
        ):
            config = self._config(with_controlnet=False)
        script = sys.modules["character_builder_script"]
        self.assertIsNone(script.GALLERY_ROOT)
        library = next(c for c in config["components"] if c.get("props", {}).get("label") == "Library")
        self.assertIn("Custom Poses", [choice[1] for choice in library["props"]["choices"]])
        item = script.CUSTOM_STORE.add(png_file(self.base, "solo.png")).item
        self.assertEqual(script._refresh("Custom Poses", "", "All", "All", True, False, 1)[1], [f"Custom Poses:{item.id}"])

    def test_unavailable_store_does_not_break_the_tab(self):
        with mock.patch.dict(os.environ, {"FCB_CUSTOM_POSE_DIR": "relative/folder"}):
            config = self._config(with_controlnet=True)
        script = sys.modules["character_builder_script"]
        self.assertEqual(script.CUSTOM_STORE.status().state, "unavailable")
        self.assertIn("absolute", script._refresh("Custom Poses", "", "All", "All", True, False, 1)[3])
        self.assertGreater(len(config["dependencies"]), 40)

    def test_forge_data_path_is_used_when_no_folder_is_configured(self):
        from character_builder_core import custom_poses

        forge_root = self.base / "forge"
        fake = mock.Mock(data_path=str(forge_root))
        with mock.patch.dict(sys.modules, {"modules.paths_internal": fake}), mock.patch.object(sys.modules["modules"], "paths_internal", fake, create=True):
            store = custom_poses.open_store({}, self.script._forge_data_path)
        self.assertEqual(store.root, (forge_root / "data" / "forge-character-builder" / "custom-poses").resolve())


if __name__ == "__main__":
    unittest.main()
