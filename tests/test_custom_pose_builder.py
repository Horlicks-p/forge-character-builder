"""Character Builder Step 3 with Custom Poses."""

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from character_builder_core import anhao, custom_poses, session
from character_builder_core.charadb_adapter import load_catalog
from character_builder_core.models import BuilderSelection, ItemRef

HAS_GRADIO = importlib.util.find_spec("gradio") is not None
CUSTOM = custom_poses.SOURCE_ID


def png_bytes(color=(10, 20, 30), size=(64, 48), mode="RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


class LibraryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.counter = 0

    def add(self, store=None, color=None, mode="RGB", **options):
        self.counter += 1
        path = self.base / f"pose{self.counter}.png"
        path.write_bytes(png_bytes(color or (self.counter, 20, 30), mode=mode))
        return (store or self.store).add(str(path), **options).item

    def pose(self, item, **changes):
        return BuilderSelection(pose=ItemRef(CUSTOM, item.id), **changes)


class SessionTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.store = custom_poses.open_store({custom_poses.ENV_ROOT: str(self.base / "library")})
        self.ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=None, custom_store=self.store)

    def test_custom_is_the_default_without_anhao(self):
        self.assertEqual(self.ctx.default_pose_source(), CUSTOM)
        self.assertEqual([value for _, value in self.ctx.pose_source_choices()], [anhao.POSE_SOURCE, CUSTOM])

    def test_context_without_a_store_offers_only_anhao(self):
        ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=None)
        self.assertEqual(ctx.default_pose_source(), anhao.POSE_SOURCE)
        self.assertEqual(len(ctx.pose_source_choices()), 1)
        self.assertEqual(session.pose_gallery(ctx, CUSTOM, "", "All", "All", True, False, 1)[0].total, 0)

    def test_pose_hint_points_at_custom_poses_when_anhao_is_missing(self):
        self.assertIn("Custom Poses", self.ctx.pose_hint())
        self.assertNotIn("No poses", self.ctx.pose_hint())

    def test_pose_hint_stays_a_blocker_when_the_store_is_unavailable(self):
        store = custom_poses.open_store({custom_poses.ENV_ROOT: "relative/folder"})
        ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=None, custom_store=store)
        self.assertIn("No poses", ctx.pose_hint())

    def test_source_health_lists_custom_poses(self):
        self.add()
        rows = {row.name: row for row in self.ctx.source_health()}
        self.assertIn("1 saved", rows["Custom Poses"].detail)

    def test_gallery_state_lines_up_with_the_cards(self):
        older, newer = self.add(name="Older"), self.add(name="Newer")
        result, state = session.pose_gallery(self.ctx, CUSTOM, "", "All", "All", True, False, 1)
        self.assertEqual(state.source_id, CUSTOM)
        self.assertEqual(state.revision, custom_poses.revision())
        self.assertEqual(state.refs, (ItemRef(CUSTOM, newer.id), ItemRef(CUSTOM, older.id)))
        self.assertEqual([caption for _, caption in result.images], ["Newer", "Older"])

    def test_gallery_search_and_empty_messages(self):
        self.assertIn("Pose Gallery", session.pose_gallery(self.ctx, CUSTOM, "", "All", "All", True, False, 1)[0].info)
        self.add(name="Standing", sensitive=True)
        self.assertEqual(session.pose_gallery(self.ctx, CUSTOM, "", "All", "All", True, False, 1)[0].info, session.MSG_ALL_HIDDEN)
        self.assertEqual(session.pose_gallery(self.ctx, CUSTOM, "zzz", "All", "All", False, False, 1)[0].info, session.MSG_NO_MATCH)
        self.assertEqual(len(session.pose_gallery(self.ctx, CUSTOM, "stand", "All", "All", False, False, 1)[1].refs), 1)

    def test_custom_and_anhao_lookups_do_not_cross(self):
        item = self.add()
        ref = ItemRef(CUSTOM, item.id)
        self.assertEqual(session.custom_pose_record(self.ctx, ref), item)
        self.assertIsNone(session.pose_record(self.ctx, ref))
        self.assertIsNone(session.custom_pose_record(self.ctx, ItemRef(anhao.POSE_SOURCE, item.id)))
        self.assertEqual(session.pose_angle_choices(self.ctx, self.pose(item)), [])

    def test_details_are_plain_and_the_markdown_summary_omits_the_name(self):
        item = self.add(name="**bold** <b>x</b>", input_kind=custom_poses.KIND_DEPTH)
        selection = self.pose(item)
        details = session.pose_details(self.ctx, selection)
        for text in ("**bold** <b>x</b>", "Depth map", "Preprocessor: None"):
            self.assertIn(text, details)
        summary, preview = session.pose_summary(self.ctx, selection)
        self.assertNotIn("bold", summary)
        self.assertIn("Depth map", summary)
        self.assertEqual(Path(preview), self.store.thumbnail_path(item.id))

    def test_summary_of_a_deleted_pose_is_unavailable(self):
        item = self.add()
        self.store.delete(item.id)
        summary, preview = session.pose_summary(self.ctx, self.pose(item))
        self.assertIn("unavailable", summary)
        self.assertIsNone(preview)

    def test_source_change_clears_only_a_pose_from_another_source(self):
        item = self.add()
        selection = self.pose(item, outfit=ItemRef("outfit", "1"))
        self.assertEqual(session.change_pose_source(selection, CUSTOM), (selection, []))
        cleared, notices = session.change_pose_source(selection, anhao.POSE_SOURCE)
        self.assertIsNone(cleared.pose)
        self.assertEqual(cleared.outfit, ItemRef("outfit", "1"))
        self.assertIn("pose source changed", notices[0])

    def test_safe_mode_clears_a_sensitive_custom_pose(self):
        selection = self.pose(self.add(sensitive=True))
        self.assertEqual(session.apply_safe_mode(self.ctx, selection, False), (selection, []))
        cleared, notices = session.apply_safe_mode(self.ctx, selection, True)
        self.assertIsNone(cleared.pose)
        self.assertIn("Safe mode", notices[0])

    def test_reconcile_clears_deleted_and_newly_hidden_poses(self):
        item = self.add()
        selection = self.pose(item)
        self.assertEqual(session.reconcile_pose(self.ctx, selection, True), (selection, []))
        self.store.update(item.id, sensitive=True)
        cleared, notices = session.reconcile_pose(self.ctx, selection, True)
        self.assertIsNone(cleared.pose)
        self.assertIn(custom_poses.ERROR_HIDDEN, notices[0])
        self.assertEqual(session.reconcile_pose(self.ctx, selection, False), (selection, []))
        self.store.delete(item.id)
        self.assertIn(custom_poses.ERROR_NO_ITEM, session.reconcile_pose(self.ctx, selection, False)[1][0])

    def test_reconcile_leaves_anhao_poses_alone(self):
        selection = BuilderSelection(pose=ItemRef(anhao.POSE_SOURCE, "1"))
        self.assertEqual(session.reconcile_pose(self.ctx, selection, True), (selection, []))

    def test_resolve_follows_the_saved_input_type(self):
        for kind, expected in (
            (custom_poses.KIND_REFERENCE_CANNY, "canny"),
            (custom_poses.KIND_PREPROCESSED_CANNY, "None"),
            (custom_poses.KIND_OPENPOSE, "None"),
            (custom_poses.KIND_DEPTH, "None"),
        ):
            item = self.add(input_kind=kind)
            # pose_mode must not override the saved type.
            _, module = session.resolve_pose_image(self.ctx, self.pose(item, pose_mode="depth"), True)
            self.assertEqual(module, expected)

    def test_builder_sends_the_same_pixels_as_the_gallery(self):
        item = self.add(color=(255, 0, 0, 0), mode="RGBA", input_kind=custom_poses.KIND_OPENPOSE)
        array, _ = session.resolve_pose_image(self.ctx, self.pose(item), True)
        self.assertIsInstance(array, np.ndarray)
        self.assertTrue((array == self.store.load_control_image(item.id, safe_mode=True).array).all())
        self.assertEqual(array[0, 0].tolist(), [0, 0, 0])

    def test_resolve_refuses_hidden_and_deleted_poses(self):
        item = self.add(sensitive=True)
        selection = self.pose(item)
        with self.assertRaisesRegex(session.SendError, "hidden by Safe Mode"):
            session.resolve_pose_image(self.ctx, selection, True)
        self.store.delete(item.id)
        with self.assertRaisesRegex(session.SendError, custom_poses.ERROR_NO_ITEM):
            session.resolve_pose_image(self.ctx, selection, False)

    def test_send_all_needs_a_prompt_but_then_uses_the_custom_image(self):
        item = self.add(input_kind=custom_poses.KIND_DEPTH)
        with self.assertRaises(session.SendError):
            session.preflight_send_all(self.ctx, self.pose(item), "  ", True)
        array, module = session.preflight_send_all(self.ctx, self.pose(item), "black hair", True)
        self.assertEqual((array.shape, module), ((48, 64, 3), "None"))

    def test_saved_prompt_never_reaches_the_builder_prompt(self):
        item = self.add(prompt="standing, arms at sides")
        update = session.regenerate(self.ctx, self.pose(item), "", "", True)
        self.assertNotIn("standing", update.final)

    def test_large_builder_page_is_paginated(self):
        with mock.patch.object(session, "PAGE_SIZE", 2):
            for _ in range(3):
                self.add()
            result, state = session.pose_gallery(self.ctx, CUSTOM, "", "All", "All", True, False, 9)
        self.assertEqual((result.page, result.total_pages, len(state.refs)), (2, 2, 1))


@unittest.skipUnless(HAS_GRADIO, "gradio 4.40 not installed")
class BuilderEventTests(LibraryTestCase):
    def build(self, **kwargs):
        from forge_harness import build_demo

        self.demo = build_demo(**kwargs)
        self.script = sys.modules["character_builder_script"]
        self.store = self.script.CUSTOM_STORE

    def setUp(self):
        super().setUp()
        environ = mock.patch.dict(os.environ, {"FCB_CUSTOM_POSE_DIR": str(self.base / "library")})
        environ.start()
        self.addCleanup(environ.stop)
        self.build(with_controlnet=True)

    def fn(self, name):
        return next(block.fn for block in self.demo.fns.values() if getattr(block.fn, "__name__", "") == name)

    def block(self, **props):
        def matches(block):
            return all(getattr(block, key, None) == value for key, value in props.items())

        return next(block for block in self.demo.blocks.values() if matches(block))

    def value(self, result, label):
        return next(value for block, value in result.items() if getattr(block, "label", None) == label)

    def pick(self, state, index=0, sel=None, final="", last="", safe=True, mode="Normal"):
        return self.fn("pick_pose")(f"{index}:1", state, mode, sel or BuilderSelection(), final, last, safe)

    def state(self, safe=True):
        return session.pose_gallery(self.script._builder_context(), CUSTOM, "", "All", "All", safe, False, 1)[1]

    def selected(self, result):
        return next(value for block, value in result.items() if isinstance(value, BuilderSelection))

    def test_radio_offers_both_sources_and_starts_on_anhao(self):
        radio = self.block(elem_id="fcb_pose_source")
        self.assertEqual([choice[1] for choice in radio.choices], [anhao.POSE_SOURCE, CUSTOM])
        self.assertEqual(radio.value, anhao.POSE_SOURCE)

    def test_builder_starts_on_custom_without_anhao(self):
        with mock.patch("character_builder_core.anhao.find_gallery_root", return_value=None), mock.patch(
            "character_builder_core.anhao.find_gallery_candidate", return_value=None
        ):
            self.build(with_controlnet=True)
        self.assertEqual(self.block(elem_id="fcb_pose_source").value, CUSTOM)
        self.assertFalse(self.block(label="System").visible)
        self.assertFalse(self.block(label="Control image").visible)
        item = self.add()
        selected = self.selected(self.pick(self.state()))
        self.assertEqual(selected.pose, ItemRef(CUSTOM, item.id))
        array, enabled, module, _ = self.fn("send_pose")(selected, True)
        self.assertEqual((array.shape, enabled, module), ((48, 64, 3), True, "canny"))

    def test_pick_uses_the_page_it_was_rendered_with(self):
        item = self.add()
        result = self.pick(self.state())
        self.assertEqual(self.selected(result).pose, ItemRef(CUSTOM, item.id))
        self.assertTrue(self.value(result, "Pose details").get("visible"))
        self.assertIn("Preprocessor: canny", self.value(result, "Pose details")["value"])

    def test_pick_shows_no_view_angle_for_custom_poses(self):
        self.add()
        self.assertFalse(self.value(self.pick(self.state()), "View angle")["visible"])

    def test_pick_ignores_depth_mode_for_custom_poses(self):
        self.add()
        self.assertEqual(self.selected(self.pick(self.state(), mode="Depth")).pose_mode, "normal")

    def test_pick_of_a_deleted_pose_keeps_the_current_selection(self):
        keep, gone = self.add(), self.add()
        state = self.state()
        self.store.delete(gone.id)
        current = BuilderSelection(pose=ItemRef(CUSTOM, keep.id))
        result = self.pick(state, 0, current)  # newest first, so slot 0 is the deleted one
        self.assertEqual(self.selected(result).pose, current.pose)
        self.assertIn(custom_poses.ERROR_NO_ITEM, self._status(result))

    def _status(self, result):
        return next(str(value) for block, value in result.items() if getattr(block, "elem_id", "") == "fcb_status")

    def test_out_of_range_pick_changes_nothing(self):
        self.assertIsNone(self.selected(self.pick(session.PosePageState(CUSTOM), 3)).pose)

    def test_source_change_empties_the_page_and_keeps_an_edited_prompt(self):
        item = self.add()
        selection = BuilderSelection(pose=ItemRef(CUSTOM, item.id))
        result = self.fn("begin_pose_source_change")(anhao.POSE_SOURCE, selection, "my own words", "generated", True)
        self.assertIsNone(self.selected(result).pose)
        self.assertEqual(self.value(result, "Final Prompt (editable)"), "my own words")
        self.assertEqual(self.value(result, "Poses"), [])
        state = next(value for value in result.values() if isinstance(value, session.PosePageState))
        self.assertEqual((state.source_id, state.refs), (anhao.POSE_SOURCE, ()))
        self.assertIn("pose source changed", self._status(result))

    def test_custom_source_never_asks_for_anhao_filters(self):
        with mock.patch.object(session, "pose_filter_options", side_effect=AssertionError("Anhao only")):
            first, second = self.fn("pose_filters")(CUSTOM, True, "All", "All")
        self.assertEqual((first["visible"], second["visible"]), (False, False))

    def test_anhao_filters_come_back(self):
        first, _ = self.fn("pose_filters")(anhao.POSE_SOURCE, True, "All", "All")
        self.assertTrue(first["visible"])
        self.assertIn("All", first["choices"])

    def test_view_clears_a_pose_deleted_elsewhere(self):
        item = self.add()
        selection = BuilderSelection(pose=ItemRef(CUSTOM, item.id))
        self.store.delete(item.id)
        result = self.fn("enter_builder")(self.state(), "", 1, selection, "", "", True)
        self.assertIsNone(self.selected(result).pose)
        self.assertIn(custom_poses.ERROR_NO_ITEM, self._status(result))

    def test_entering_refreshes_the_gallery_only_after_a_change(self):
        state = self.state()
        unchanged = self.fn("enter_builder")(state, "", 1, BuilderSelection(), "", "", True)
        self.assertEqual(len(unchanged), 1)
        item = self.add()
        result = self.fn("enter_builder")(state, "", 1, BuilderSelection(), "", "", True)
        fresh = next(value for value in result.values() if isinstance(value, session.PosePageState))
        self.assertEqual(fresh.refs, (ItemRef(CUSTOM, item.id),))
        self.assertEqual(fresh.revision, custom_poses.revision())

    def test_entering_on_anhao_never_touches_the_gallery(self):
        self.add()
        state = session.PosePageState(anhao.POSE_SOURCE, 0, ())
        self.assertEqual(len(self.fn("enter_builder")(state, "", 1, BuilderSelection(), "", "", True)), 1)

    def test_refresh_keeps_the_status_line_while_the_pose_is_valid(self):
        selection = BuilderSelection(pose=ItemRef(CUSTOM, self.add().id))
        self.assertEqual(len(self.fn("refresh_stale_pose")(selection, "", "", True)), 1)

    def test_send_all_returns_the_custom_image(self):
        item = self.add(input_kind=custom_poses.KIND_OPENPOSE)
        array, enabled, module, _ = self.fn("send_all")(BuilderSelection(pose=ItemRef(CUSTOM, item.id)), "black hair", True)
        self.assertTrue(enabled)
        self.assertEqual((array.shape, module), ((48, 64, 3), "None"))

    def test_send_of_a_hidden_pose_is_refused(self):
        import gradio

        item = self.add(sensitive=True)
        with self.assertRaisesRegex(gradio.Error, "hidden by Safe Mode"):
            self.fn("send_pose")(BuilderSelection(pose=ItemRef(CUSTOM, item.id)), True)

    def test_anhao_pick_still_works(self):
        state = session.pose_gallery(self.script._builder_context(), anhao.POSE_SOURCE, "", "All", "All", True, False, 1)[1]
        self.assertEqual(self.selected(self.pick(state, mode="Depth")).pose_mode, "depth")
        self.assertEqual(self.selected(self.pick(state)).pose.source_id, anhao.POSE_SOURCE)

    def test_entry_and_step_tabs_both_recheck_the_library(self):
        tabs = {block.label: block._id for block in self.demo.blocks.values() if type(block).__name__ == "Tab"}
        selects = {
            target[0]
            for block in self.demo.fns.values()
            if getattr(block.fn, "__name__", "") == "enter_builder"
            for target in block.targets
            if target[1] == "select"
        }
        self.assertEqual(selects, {tabs["Character Builder"], tabs["3 · Pose"]})

    def test_page_load_fills_the_pose_state(self):
        load = self.fn("initial_load")()
        state = next(value for value in load.values() if isinstance(value, session.PosePageState))
        self.assertEqual(state.source_id, anhao.POSE_SOURCE)
        self.assertTrue(state.refs)


if __name__ == "__main__":
    unittest.main()
