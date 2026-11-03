import unittest
from pathlib import Path

from character_builder_core.builder import build_character_prompt
from character_builder_core.charadb_adapter import load_catalog
from character_builder_core.models import BuilderSelection, ItemRef
from character_builder_core.prompts import dedupe_tags, format_outfit_keywords, join_tags, merge_tags, split_tags

FIXTURE = Path(__file__).parent / "fixtures" / "charadb_sample.json"


def ref(item_id):
    return ItemRef("charadb", item_id)


class TagHelperTests(unittest.TestCase):
    def test_split_keeps_multiword_tags(self):
        self.assertEqual(
            split_tags("serafuku, blue sailor collar,red neckerchief ,  pleated skirt"),
            ["serafuku", "blue sailor collar", "red neckerchief", "pleated skirt"],
        )

    def test_split_handles_empty_values(self):
        for value in (None, "", " , ,, ", []):
            self.assertEqual(split_tags(value), [])

    def test_dedupe_is_whole_tag_and_case_insensitive(self):
        self.assertEqual(
            dedupe_tags(["skirt", "pleated skirt", " Skirt ", "Pleated  Skirt"]),
            ["skirt", "pleated skirt"],
        )

    def test_join_has_no_empty_or_trailing_commas(self):
        self.assertEqual(join_tags(["a", "", "  ", "b"]), "a, b")
        self.assertEqual(join_tags([]), "")

    def test_merge_skips_empty_groups(self):
        self.assertEqual(merge_tags("black hair", None, "", "long hair, black hair"), ["black hair", "long hair"])

    def test_format_outfit_keywords_keeps_garment_phrases(self):
        self.assertEqual(format_outfit_keywords("white sneakers"), "white sneakers")
        self.assertEqual(
            format_outfit_keywords("pleated mini skirt white sneakers"),
            "pleated mini skirt, white sneakers",
        )

    def test_format_outfit_keywords_respects_existing_commas(self):
        self.assertEqual(format_outfit_keywords("blue sailor collar, white sneakers"), "blue sailor collar, white sneakers")
        self.assertEqual(format_outfit_keywords(None), "")


class CharacterPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(FIXTURE)

    def build(self, **fields):
        return build_character_prompt(BuilderSelection(**fields), self.catalog)

    def test_empty_selection_is_empty(self):
        parts = self.build()
        self.assertEqual((parts.appearance, parts.outfit, parts.final), ("", "", ""))

    def test_hairstyle_with_color(self):
        parts = self.build(hairstyle=ref("H001"), hair_color=ref("H049"))
        self.assertEqual(parts.final, "black hair, long hair")
        self.assertNotIn("brown hair", parts.final)

    def test_hairstyle_alone_has_no_default_color(self):
        self.assertEqual(self.build(hairstyle=ref("H001")).final, "long hair")

    def test_variant_supplies_color(self):
        self.assertEqual(self.build(hairstyle=ref("H001-black")).final, "black hair, long hair")
        self.assertEqual(
            self.build(hairstyle=ref("H001"), hairstyle_variant="H001-blonde").final,
            "blonde hair, long hair",
        )

    def test_explicit_hair_color_overrides_variant(self):
        final = self.build(hairstyle=ref("H001-black"), hair_color=ref("H052")).final
        self.assertEqual(final, "blonde hair, long hair")

    def test_mismatched_variant_is_ignored(self):
        parts = self.build(hairstyle=ref("H010"), hairstyle_variant="H001-black")
        self.assertEqual(parts.final, "twintails, long hair")
        self.assertTrue(parts.warnings)

    def test_changing_hair_color_keeps_outfit_colors(self):
        black = self.build(hair_color=ref("H049"), outfit=ref("O130")).final
        blonde = self.build(hair_color=ref("H052"), outfit=ref("O130")).final
        self.assertEqual(blonde, "blonde hair, business suit, black suit, white shirt, black skirt")
        self.assertNotIn("black hair", blonde)
        self.assertIn("black skirt", black)

    def test_outfit_hair_color_is_stripped_without_selection(self):
        parts = self.build(outfit=ref("O130"))
        self.assertEqual(parts.outfit, "business suit, black suit, white shirt, black skirt")

    def test_multi_tag_hair_color(self):
        self.assertEqual(
            self.build(hair_color=ref("H066")).final,
            "two-tone hair, black hair, pink hair, colored inner hair",
        )

    def test_bangs_eyes_and_shape_stack(self):
        parts = self.build(bangs=ref("H037"), eye_color=ref("H095"), eye_shape=ref("H120"))
        self.assertEqual(parts.final, "blunt bangs, brown eyes, tareme")

    def test_eye_shape_sample_color_is_not_emitted(self):
        self.assertEqual(self.build(eye_shape=ref("H120")).final, "tareme")
        final = self.build(eye_color=ref("H097"), eye_shape=ref("H120")).final
        self.assertEqual(final, "blue eyes, tareme")

    def test_charadb_outfit_tags_kept_intact(self):
        self.assertEqual(
            self.build(outfit=ref("O001")).outfit,
            "serafuku, sailor collar, blue sailor collar, red neckerchief, pleated skirt, blue skirt",
        )

    def test_full_example(self):
        parts = self.build(
            hairstyle=ref("H001"),
            hair_color=ref("H049"),
            bangs=ref("H037"),
            eye_color=ref("H095"),
            outfit=ref("O001"),
            pose=ItemRef("anhao_pose", "123"),
            pose_mode="depth",
        )
        self.assertEqual(
            parts.final,
            "black hair, long hair, blunt bangs, brown eyes, serafuku, sailor collar, "
            "blue sailor collar, red neckerchief, pleated skirt, blue skirt",
        )
        self.assertEqual(parts.appearance, "black hair, long hair, blunt bangs, brown eyes")

    def test_pose_never_adds_tags(self):
        self.assertEqual(self.build(pose=ItemRef("charadb", "P001")).final, "")

    def test_subject_tags_are_opt_in(self):
        self.assertEqual(self.build(hair_color=ref("H049"), include_subject=True).final, "1girl, solo, black hair")

    def test_outfit_duplicates_removed(self):
        self.assertEqual(self.build(outfit=ref("O999")).outfit, "skirt, pleated skirt")

    def test_unknown_or_wrong_kind_ids_do_not_raise(self):
        parts = self.build(
            hairstyle=ref("NOPE"),
            hair_color=ref("H001"),  # a hairstyle, not a colour
            outfit=ItemRef("missing_source", "O1"),
            eye_shape=ref("H095"),  # an eye colour, not a shape
        )
        self.assertEqual(parts.final, "")
        self.assertEqual(len(parts.warnings), 4)

    def test_safe_mode_skips_nsfw_outfit(self):
        selection = BuilderSelection(outfit=ref("O231"))
        safe = build_character_prompt(selection, self.catalog, safe_mode=True)
        self.assertEqual(safe.outfit, "")
        self.assertTrue(safe.warnings)
        unsafe = build_character_prompt(selection, self.catalog, safe_mode=False)
        self.assertEqual(unsafe.outfit, "micro bikini, bikini, swimsuit")

    def test_works_with_unavailable_catalog(self):
        parts = build_character_prompt(BuilderSelection(hairstyle=ref("H001")), load_catalog(None))
        self.assertEqual(parts.final, "")


if __name__ == "__main__":
    unittest.main()
