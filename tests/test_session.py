import json
import tempfile
import unittest
from pathlib import Path

from character_builder_core import anhao, session
from character_builder_core.assets import PLACEHOLDER_IMAGE, safe_join
from character_builder_core.charadb_adapter import find_image_root, find_items_file, load_catalog, parse_items
from character_builder_core.models import KIND_EYE_SHAPE, BuilderSelection, ItemRef
from helpers import CHARADB_FIXTURE, make_anhao, make_charadb_images


def cref(item_id):
    return ItemRef("charadb", item_id)


def pose(item_id, mode="normal"):
    return dict(pose=ItemRef(anhao.POSE_SOURCE, str(item_id)), pose_mode=mode)


class TempDataTest(unittest.TestCase):
    def setUp(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.anhao_root = make_anhao(self.tmp / "anhao").resolve()
        self.charadb_images = make_charadb_images(self.tmp / "charadb", ["H001", "H001-black", "O001"]).resolve()
        catalog = load_catalog(CHARADB_FIXTURE, self.charadb_images)
        self.ctx = session.BuilderContext(charadb=catalog, anhao_root=self.anhao_root)

    def tearDown(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self._tmp.cleanup()


class AssetTests(unittest.TestCase):
    def test_safe_join_rejects_escapes(self):
        root = Path(tempfile.gettempdir())
        for bad in ("../x.png", "a/../../x.png", "/etc/passwd", "C:/Windows/x.png", "https://example.com/x.png", "", None):
            self.assertIsNone(safe_join(root, bad), bad)
        self.assertEqual(safe_join(root, "thumb/a.webp"), (root / "thumb" / "a.webp").resolve())
        self.assertIsNone(safe_join(None, "thumb/a.webp"))

    def test_placeholder_is_shipped(self):
        self.assertTrue(PLACEHOLDER_IMAGE.is_file())


class CharaDBReleaseLayoutTests(unittest.TestCase):
    def test_data_js_is_parsed_and_variant_colors_derived(self):
        entries = json.loads(CHARADB_FIXTURE.read_text(encoding="utf-8"))
        for entry in entries:  # data.js drops these fields
            entry.pop("color", None)
            entry.pop("color_jp", None)
        text = "window.DB=" + json.dumps({"tier": "public", "items": entries}, ensure_ascii=False) + ";"
        self.assertEqual(len(parse_items(text)), len(entries))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "vendor" / "charadb"
            root.mkdir(parents=True)
            (root / "data.js").write_text(text, encoding="utf-8")
            (root / "thumb").mkdir()
            ext = Path(tmp)
            items_file = find_items_file(ext, environ={})
            self.assertEqual(items_file.name, "data.js")
            self.assertEqual(find_image_root(ext, environ={}), root.resolve())
            catalog = load_catalog(items_file, find_image_root(ext, environ={}))
        self.assertTrue(catalog.available)
        self.assertEqual([v.color_tag for v in catalog.variants["H001"]], ["black hair", "blonde hair"])
        self.assertIn("previews installed", catalog.message)

    def test_items_json_preferred_over_data_js(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "vendor" / "charadb"
            (root / "data").mkdir(parents=True)
            (root / "data" / "items_public.json").write_text("[]", encoding="utf-8")
            (root / "data.js").write_text("window.DB={\"items\": []};", encoding="utf-8")
            self.assertEqual(find_items_file(Path(tmp), environ={}).name, "items_public.json")


class AnhaoTests(TempDataTest):
    def test_root_env_names(self):
        ext = self.tmp / "ext"
        self.assertEqual(anhao.find_gallery_root(ext, environ={"ANHAO_GALLERY_ROOT": str(self.anhao_root)}), self.anhao_root)
        self.assertEqual(anhao.find_gallery_root(ext, environ={"AI_POSE_GALLERY_ROOT": str(self.anhao_root)}), self.anhao_root)
        self.assertIsNone(anhao.find_gallery_root(ext, environ={}))

    def test_filtering_and_safe_mode(self):
        ids = [i["id"] for i in anhao.filtered_items(self.anhao_root, "Poses", "", "All", "All", True)]
        self.assertEqual(ids, [1, 3, 4])
        ids = [i["id"] for i in anhao.filtered_items(self.anhao_root, "Poses", "", "All", "All", False)]
        self.assertEqual(ids, [1, 2, 3, 4])

    def test_asset_paths_stay_inside_root(self):
        escaping = anhao.find_item(self.anhao_root, "Poses", 4)
        for thumbnail in (True, False):
            path = anhao.asset_path(self.anhao_root, "Poses", escaping, thumbnail=thumbnail, use_depth=False)
            self.assertFalse(path.is_file())
        good = anhao.find_item(self.anhao_root, "Poses", 1)
        self.assertTrue(anhao.asset_path(self.anhao_root, "Poses", good, thumbnail=False, use_depth=True).is_file())
        self.assertEqual(anhao.asset_path(self.anhao_root, "Poses", good, thumbnail=True, use_depth=True).parent.name, "pose")

    def test_missing_library_is_empty(self):
        self.assertEqual(anhao.load_library(self.tmp / "nowhere", "Poses"), ())
        self.assertEqual(anhao.load_library(None, "Poses"), ())

    def test_outfit_item_has_no_legacy_suffix(self):
        item = anhao.AnhaoOutfits(self.anhao_root).get("10")
        self.assertEqual(item.normalized_tags, ("white blouse", "pleated mini skirt"))
        self.assertNotIn("detailed outfit", ", ".join(item.normalized_tags))
        self.assertEqual(item.category, "Spring outfits")
        self.assertTrue(anhao.AnhaoOutfits(self.anhao_root).get("11").nsfw)
        self.assertIsNone(anhao.AnhaoOutfits(self.anhao_root).get("999"))

    def test_legacy_prompt_unchanged(self):
        item = anhao.find_item(self.anhao_root, "Outfits", 10)
        self.assertEqual(anhao.prompt_for("Outfits", item), "white blouse, pleated mini skirt, detailed outfit")


class GalleryTests(TempDataTest):
    def test_dropdown_choices(self):
        choices = session.dropdown_choices(self.ctx, KIND_EYE_SHAPE)
        self.assertEqual(choices[0], ("None", ""))
        self.assertIn(("tareme", "H120"), choices)

    def test_hairstyle_page_thumbnails(self):
        page = session.hairstyle_page(self.ctx, "", 1)
        self.assertEqual(page.ids, ["H001", "H010"])
        self.assertTrue(page.images[0][0].endswith("H001.webp"))
        self.assertEqual(page.images[1][0], str(PLACEHOLDER_IMAGE))
        # Hair color black -> show the black variant thumbnail.
        page = session.hairstyle_page(self.ctx, "", 1, "H049")
        self.assertTrue(page.images[0][0].endswith("H001-black.webp"))
        self.assertEqual(session.hairstyle_page(self.ctx, "twin", 1).ids, ["H010"])

    def test_outfit_pages(self):
        charadb_page = session.outfit_page(self.ctx, "charadb", "All", "", True, 1)
        self.assertEqual(charadb_page.ids, ["O001", "O130", "O999"])  # O231 hidden by safe mode
        self.assertEqual(session.outfit_page(self.ctx, "charadb", "School uniform", "", True, 1).ids, ["O001"])
        anhao_page = session.outfit_page(self.ctx, anhao.OUTFIT_SOURCE, "All", "", False, 1)
        self.assertEqual(anhao_page.ids, ["10", "11"])
        self.assertTrue(anhao_page.images[0][0].endswith("c.webp"))
        self.assertEqual(anhao_page.images[1][0], str(PLACEHOLDER_IMAGE))
        self.assertIn("Swimwear", session.outfit_categories(self.ctx, "charadb", False))
        self.assertEqual(session.outfit_page(self.ctx, "unknown", "All", "", True, 1).ids, [])

    def test_pose_page_and_paging_bounds(self):
        page = session.pose_page(self.ctx, "", "All", "All", True, False, 99)
        self.assertEqual((page.ids, page.page, page.total_pages), (["1", "3", "4"], 1, 1))
        self.assertEqual(page.images[1][0], str(PLACEHOLDER_IMAGE))
        self.assertIn("Page 1 of 1", page.info)

    def test_outfit_sources_listed(self):
        self.assertEqual(
            self.ctx.outfit_source_choices(),
            [("CharaDB (Anime)", "charadb"), ("Anhao (Original)", anhao.OUTFIT_SOURCE)],
        )
        self.assertEqual(self.ctx.outfit_source_choices(show_anhao=False), [("CharaDB (Anime)", "charadb")])


class PromptStateTests(TempDataTest):
    def test_regenerate_overwrites_untouched_prompt(self):
        sel = BuilderSelection(hair_color=cref("H049"), hairstyle=cref("H001"))
        update = session.regenerate(self.ctx, sel, "", "", True)
        self.assertEqual((update.final, update.last_generated, update.notice), ("black hair, long hair", "black hair, long hair", ""))

    def test_regenerate_keeps_manual_edit(self):
        sel = BuilderSelection(hair_color=cref("H052"))
        update = session.regenerate(self.ctx, sel, "black hair, my edit", "black hair", True)
        self.assertEqual(update.final, "black hair, my edit")
        self.assertEqual(update.last_generated, "blonde hair")
        self.assertEqual(update.notice, session.EDITED_NOTICE)
        forced = session.regenerate(self.ctx, sel, "black hair, my edit", "black hair", True, force=True)
        self.assertEqual((forced.final, forced.notice), ("blonde hair", ""))

    def test_anhao_outfit_in_prompt(self):
        sel = BuilderSelection(outfit=ItemRef(anhao.OUTFIT_SOURCE, "10"))
        self.assertEqual(session.regenerate(self.ctx, sel, "", "", True).final, "white blouse, pleated mini skirt")

    def test_safe_mode_clears_hidden_selections(self):
        for outfit in (cref("O231"), ItemRef(anhao.OUTFIT_SOURCE, "11")):
            sel = BuilderSelection(outfit=outfit, **pose(2))
            cleared, notices = session.apply_safe_mode(self.ctx, sel, True)
            self.assertIsNone(cleared.outfit)
            self.assertIsNone(cleared.pose)
            self.assertEqual(len(notices), 2)
            kept, notices = session.apply_safe_mode(self.ctx, sel, False)
            self.assertEqual((kept, notices), (sel, []))

    def test_summaries(self):
        sel = BuilderSelection(hairstyle=cref("H001-black"), outfit=cref("O130"), **pose(1, "depth"))
        self.assertIn("long hair", session.hairstyle_summary(self.ctx, sel))
        summary, raw = session.outfit_summary(self.ctx, sel)
        self.assertIn("#O130", summary)
        self.assertIn("black hair", raw)  # raw keeps what normalization strips
        pose_md, preview = session.pose_summary(self.ctx, sel)
        self.assertIn("Depth", pose_md)
        self.assertTrue(preview.endswith("a.webp"))
        self.assertIn("select a pose", session.pose_summary(self.ctx, BuilderSelection())[0])


class SendTests(TempDataTest):
    def test_normal_and_depth_use_current_mode(self):
        path, module = session.resolve_pose_image(self.ctx, BuilderSelection(**pose(1)), True)
        self.assertEqual((path.name, path.parent.name, module), ("a.jpg", "pose", "canny"))
        path, module = session.resolve_pose_image(self.ctx, BuilderSelection(**pose(1, "depth")), True)
        self.assertEqual((path.name, path.parent.parent.name, module), ("a.webp", "depth", "None"))

    def test_errors(self):
        cases = [
            (BuilderSelection(), "Select a pose"),
            (BuilderSelection(**pose(3)), "Missing reference image"),
            (BuilderSelection(**pose(4)), "Missing reference image"),
            (BuilderSelection(**pose(999)), "not available"),
            (BuilderSelection(**pose(2)), "hidden by Safe mode"),
            (BuilderSelection(**pose(3, "depth")), "Missing reference image"),
        ]
        for sel, message in cases:
            with self.assertRaises(session.SendError) as caught:
                session.resolve_pose_image(self.ctx, sel, True)
            self.assertIn(message, str(caught.exception))
        path, _ = session.resolve_pose_image(self.ctx, BuilderSelection(**pose(2)), False)
        self.assertEqual(path.name, "b.jpg")

    def test_send_all_requires_prompt(self):
        with self.assertRaises(session.SendError) as caught:
            session.preflight_send_all(self.ctx, BuilderSelection(**pose(1)), "  ", True)
        self.assertIn("Final Prompt is empty", str(caught.exception))
        path, module = session.preflight_send_all(self.ctx, BuilderSelection(**pose(1)), "black hair", True)
        self.assertEqual((path.name, module), ("a.jpg", "canny"))


if __name__ == "__main__":
    unittest.main()
