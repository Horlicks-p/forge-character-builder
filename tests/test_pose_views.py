"""Pure-Python tests for grouping Anhao's 4-5 views into logical poses."""
import json
import tempfile
import unittest
from pathlib import Path

from character_builder_core import anhao, session
from character_builder_core.charadb_adapter import load_catalog
from character_builder_core.models import BuilderSelection, ItemRef


class PoseViewTests(unittest.TestCase):
    def setUp(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.records = []

        def add(system, number, angles):
            for angle, suffix in angles:
                item_id = f"{system}_{number}_{suffix}"
                image = f"img/poses/{item_id}.png"
                thumb = f"thumbs/poses/{item_id}.webp"
                self.records.append({
                    "id": item_id, "体系": system, "姿势编号": number,
                    "分类": "站姿", "姿势名称": "Sample pose", "角度": angle,
                    "原图": image, "缩略图": thumb,
                })
                for relative in (image, thumb, f"img/depth/poses/{item_id}.webp"):
                    path = self.root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"fixture")

        angles = [("正面", "front"), ("俯视", "high"), ("仰视", "low"), ("侧面", "side"), ("斜侧", "diag")]
        add("standard", "P001", angles)
        add("standard", "P002", [angle for angle in angles if angle[1] != "low"])
        add("other", "P001", [("正面", "front")])
        (self.root / "data" / "pose-zh.json").write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
        self.ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=self.root)

    def tearDown(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self.temp.cleanup()

    def test_group_key_includes_system_and_pose_number(self):
        grouped = anhao.pose_group_representatives(self.records)
        self.assertEqual([item["id"] for item in grouped],
                         ["standard_P001_front", "standard_P002_front", "other_P001_front"])
        self.assertNotEqual(anhao.pose_group_key(grouped[0]), anhao.pose_group_key(grouped[2]))

    def test_four_and_five_views_preserved(self):
        full = anhao.pose_views(self.root, self.records[0])
        partial = anhao.pose_views(self.root, self.records[5])
        self.assertEqual([item["角度"] for item in full], ["正面", "俯视", "仰视", "侧面", "斜侧"])
        self.assertEqual([item["角度"] for item in partial], ["正面", "俯视", "侧面", "斜侧"])
        self.assertEqual(len(full), 5)
        self.assertEqual(len(partial), 4)

    def test_gallery_has_three_cards_not_ten(self):
        page = session.pose_page(self.ctx, "", "All", "All", True, False, 1)
        self.assertEqual(page.ids, ["standard_P001_front", "standard_P002_front", "other_P001_front"])
        self.assertEqual(page.total, 3)
        self.assertIn("5 views", page.images[0][1])
        self.assertIn("4 views", page.images[1][1])
        self.assertEqual(len(page.images), 3)

    def test_selected_angle_changes_only_within_same_pose(self):
        selected = BuilderSelection(pose=ItemRef(anhao.POSE_SOURCE, "standard_P001_front"))
        choices = session.pose_angle_choices(self.ctx, selected)
        self.assertEqual(len(choices), 5)
        self.assertEqual(choices[0], ("Front", "standard_P001_front"))
        changed = session.select_pose_angle(self.ctx, selected, "standard_P001_side")
        self.assertEqual(changed.pose.item_id, "standard_P001_side")
        rejected = session.select_pose_angle(self.ctx, selected, "other_P001_front")
        self.assertEqual(rejected, selected)
        rejected = session.select_pose_angle(self.ctx, selected, "../outside")
        self.assertEqual(rejected, selected)

    def test_controlnet_resolves_chosen_angle_and_current_mode(self):
        sel = BuilderSelection(pose=ItemRef(anhao.POSE_SOURCE, "standard_P001_side"))
        path, module = session.resolve_pose_image(self.ctx, sel, True)
        self.assertEqual((path.name, module), ("standard_P001_side.png", "canny"))
        path, module = session.resolve_pose_image(self.ctx, sel.with_changes(pose_mode="depth"), True)
        self.assertEqual((path.name, module), ("standard_P001_side.webp", "None"))
        self.assertEqual(path.parent.name, "poses")

    def test_ungrouped_custom_records_remain_separate(self):
        a = {"id": "raw1", "角度": "正面"}
        b = {"id": "raw2", "角度": "侧面"}
        self.assertEqual(anhao.pose_group_representatives([a, b]), [a, b])


if __name__ == "__main__":
    unittest.main()
