import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from ai_pose_gallery_core.charadb_adapter import build_catalog, find_items_file, load_catalog, parse_items
from ai_pose_gallery_core.models import (
    KIND_BANGS,
    KIND_EYE_COLOR,
    KIND_EYE_SHAPE,
    KIND_HAIR_ACCESSORY,
    KIND_HAIR_COLOR,
    KIND_HAIRSTYLE,
    KIND_OUTFIT,
    KIND_POSE,
)

FIXTURE = Path(__file__).parent / "fixtures" / "charadb_sample.json"
CJK = re.compile(r"[぀-ヿ㐀-鿿＀-￯]")


class ParseTests(unittest.TestCase):
    def test_json_array(self):
        self.assertEqual(parse_items('[{"id": "H1"}, 3]'), [{"id": "H1"}])

    def test_json_lines_and_bom(self):
        text = '﻿{"id": "H1"}\n\n{"id": "H2"}\n'
        self.assertEqual([e["id"] for e in parse_items(text)], ["H1", "H2"])

    def test_items_object(self):
        self.assertEqual(parse_items('{"items": [{"id": "H1"}]}'), [{"id": "H1"}])


class LoadTests(unittest.TestCase):
    def test_missing_file_is_unavailable_not_error(self):
        catalog = load_catalog(None)
        self.assertFalse(catalog.available)
        self.assertIn("not installed", catalog.message)
        self.assertEqual(catalog.list_kind(KIND_OUTFIT), [])

    def test_broken_file_is_unavailable_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items_public.json"
            path.write_text("{not json", encoding="utf-8")
            catalog = load_catalog(path)
        self.assertFalse(catalog.available)
        self.assertIn("could not be read", catalog.message)

    def test_find_items_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ext"
            self.assertIsNone(find_items_file(root, environ={}))
            custom = Path(tmp) / "custom" / "data"
            custom.mkdir(parents=True)
            (custom / "items_public.json").write_text("[]", encoding="utf-8")
            found = find_items_file(root, environ={"CHARADB_ROOT": str(custom.parent)})
            self.assertEqual(found, (custom / "items_public.json").resolve())
            vendor = root / "vendor" / "charadb" / "data"
            vendor.mkdir(parents=True)
            (vendor / "items_public.json").write_text("[]", encoding="utf-8")
            self.assertEqual(find_items_file(root, environ={}), (vendor / "items_public.json").resolve())


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(FIXTURE)

    def test_kinds(self):
        counts = self.catalog.counts()
        self.assertEqual(counts[KIND_HAIRSTYLE], 2)
        self.assertEqual(counts[KIND_BANGS], 1)
        self.assertEqual(counts[KIND_HAIR_COLOR], 3)
        self.assertEqual(counts[KIND_HAIR_ACCESSORY], 1)
        self.assertEqual(counts[KIND_EYE_COLOR], 2)
        self.assertEqual(counts[KIND_EYE_SHAPE], 1)
        self.assertEqual(counts[KIND_OUTFIT], 4)
        self.assertEqual(counts[KIND_POSE], 1)
        # empty id, unknown set, orphan variant without colour
        self.assertEqual(self.catalog.skipped, 3)

    def test_variants_are_not_separate_hairstyles(self):
        ids = [item.item_id for item in self.catalog.list_kind(KIND_HAIRSTYLE)]
        self.assertEqual(ids, ["H001", "H010"])
        self.assertEqual([v.color_tag for v in self.catalog.variants["H001"]], ["black hair", "blonde hair"])
        style, variant = self.catalog.resolve_hairstyle("H001-black")
        self.assertEqual((style.item_id, variant.color_tag), ("H001", "black hair"))
        self.assertEqual(self.catalog.resolve_hairstyle("H037"), (None, None))

    def test_ui_strings_are_english(self):
        for item in self.catalog.items.values():
            self.assertIsNone(CJK.search(item.display_name), item.display_name)
            self.assertIsNone(CJK.search(item.category), item.category)
        self.assertEqual(self.catalog.get("O001").category, "School uniform")
        self.assertEqual(self.catalog.get("O999").category, "Other")
        self.assertEqual(self.catalog.get("H120").display_name, "tareme")
        self.assertTrue(self.catalog.get("O001").display_name.startswith("O001 · serafuku"))
        self.assertEqual(self.catalog.get("O001").metadata["jp"], "セーラー服(紺)")

    def test_raw_tags_preserved(self):
        item = self.catalog.get("O130")
        self.assertIn("black hair", item.raw_tags)
        self.assertNotIn("black hair", item.normalized_tags)

    def test_safe_mode_listing(self):
        all_ids = {i.item_id for i in self.catalog.list_kind(KIND_OUTFIT)}
        safe_ids = {i.item_id for i in self.catalog.list_kind(KIND_OUTFIT, safe_mode=True)}
        self.assertEqual(all_ids - safe_ids, {"O231"})


    def test_eye_shape_with_only_color_emits_nothing(self):
        # Regression: an eye-shape entry whose only tag is an eye colour must not
        # fall back to emitting that colour.
        catalog = build_catalog([
            {"id": "E1", "set": "hair", "cat": "目の色", "sub": "eye", "tags": "brown eyes"},
            {"id": "S1", "set": "hair", "cat": "瞳の形", "sub": "eye", "tags": "brown eyes"},
        ])
        shape = catalog.get("S1")
        self.assertEqual(shape.normalized_tags, ())
        self.assertEqual(shape.raw_tags, "brown eyes")
        self.assertEqual(shape.display_name, "S1")

@unittest.skipUnless(os.environ.get("CHARADB_ITEMS"), "set CHARADB_ITEMS to a real items_public.json")
class RealDataTests(unittest.TestCase):
    """Optional check against the real CharaDB index (not committed)."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(Path(os.environ["CHARADB_ITEMS"]))

    def test_expected_shape(self):
        counts = self.catalog.counts()
        self.assertTrue(self.catalog.available)
        self.assertEqual(self.catalog.skipped, 0)
        self.assertGreater(counts[KIND_HAIRSTYLE], 0)
        self.assertGreater(counts[KIND_OUTFIT], 0)
        self.assertEqual(
            counts[KIND_HAIRSTYLE] + sum(len(v) for v in self.catalog.variants.values()) + counts[KIND_HAIR_COLOR]
            + counts[KIND_BANGS] + counts[KIND_HAIR_ACCESSORY] + counts[KIND_EYE_COLOR] + counts[KIND_EYE_SHAPE],
            sum(1 for e in json.loads(Path(os.environ["CHARADB_ITEMS"]).read_text(encoding="utf-8")) if e.get("set") == "hair"),
        )

    def test_all_ui_strings_english(self):
        for item in self.catalog.items.values():
            self.assertIsNone(CJK.search(item.display_name), item.item_id)
            self.assertNotEqual(item.category, "Other", item.item_id)

    def test_no_eye_shape_or_outfit_leaks_colors(self):
        for item in self.catalog.list_kind(KIND_EYE_SHAPE):
            leaked = {t.casefold() for t in item.normalized_tags} & self.catalog.eye_color_vocab
            self.assertFalse(leaked, item.item_id)
        for item in self.catalog.list_kind(KIND_OUTFIT):
            leaked = {t.casefold() for t in item.normalized_tags} & self.catalog.hair_color_vocab
            self.assertFalse(leaked, item.item_id)


if __name__ == "__main__":
    unittest.main()
