import shutil
import tempfile
import unittest
from pathlib import Path

from character_builder_core import anhao, session, sources
from character_builder_core.charadb_adapter import load_catalog
from helpers import CHARADB_FIXTURE, make_anhao, make_charadb_images


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.anhao_root = make_anhao(self.tmp / "anhao").resolve()

    def tearDown(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self._tmp.cleanup()

    # --- CharaDB ---------------------------------------------------------------

    def test_charadb_states(self):
        images = make_charadb_images(self.tmp / "charadb", ["H001"])
        self.assertEqual(sources.charadb_health(load_catalog(CHARADB_FIXTURE, images)).state, sources.OK)

        tags_only = sources.charadb_health(load_catalog(CHARADB_FIXTURE))
        self.assertEqual(tags_only.state, sources.PARTIAL)
        self.assertIn("thumb/", tags_only.hint)

        missing = sources.charadb_health(load_catalog(None))
        self.assertEqual(missing.state, sources.MISSING)
        self.assertIn("CHARADB_ROOT", missing.hint)
        self.assertIn("vendor/charadb/", missing.hint)

        broken = self.tmp / "items_public.json"
        broken.write_text("{not json", encoding="utf-8")
        error = sources.charadb_health(load_catalog(broken))
        self.assertEqual(error.state, sources.ERROR)
        self.assertIn("could not be read", error.detail)

    # --- Anhao -----------------------------------------------------------------

    def test_anhao_ok_counts(self):
        health = sources.anhao_health(self.anhao_root)
        self.assertEqual(health.state, sources.OK)
        self.assertEqual(health.detail, "Anhao: 4 poses, 2 outfits")
        with_portraits = sources.anhao_health(self.anhao_root, libraries=("Poses", "Outfits", "Portraits"))
        self.assertIn("1 portraits", with_portraits.detail)

    def test_anhao_missing_root(self):
        health = sources.anhao_health(None)
        self.assertEqual(health.state, sources.MISSING)
        self.assertIn("ANHAO_GALLERY_ROOT", health.hint)

    def test_anhao_distinguishes_missing_and_broken_files(self):
        (self.anhao_root / "data" / "costume-zh.json").unlink()
        self.assertEqual(sources.probe_library(self.anhao_root, "Outfits").error, "`data/costume-zh.json` is missing")
        (self.anhao_root / "data" / "pose-zh.json").write_text("[{broken", encoding="utf-8")
        self.assertIn("could not be read", sources.probe_library(self.anhao_root, "Poses").error)
        (self.anhao_root / "data" / "portrait-zh.json").write_text('{"a": 1}', encoding="utf-8")
        self.assertIn("not a list", sources.probe_library(self.anhao_root, "Portraits").error)

        health = sources.anhao_health(self.anhao_root)
        self.assertEqual(health.state, sources.ERROR)
        self.assertIn("Poses:", health.detail)
        self.assertIn("Outfits:", health.detail)
        # The cached legacy loader keeps its contract: empty, never raises.
        self.assertEqual(anhao.load_library(self.anhao_root, "Poses"), ())

    def test_anhao_without_images_is_partial(self):
        shutil.rmtree(self.anhao_root / "thumbs")
        health = sources.anhao_health(self.anhao_root)
        self.assertEqual(health.state, sources.PARTIAL)
        self.assertIn("`thumbs/`", health.detail)

    def test_one_empty_library_is_partial(self):
        (self.anhao_root / "data" / "costume-zh.json").write_text("[]", encoding="utf-8")
        self.assertEqual(sources.probe_library(self.anhao_root, "Outfits"), sources.LibraryProbe(0))
        health = sources.anhao_health(self.anhao_root)
        self.assertEqual(health.state, sources.PARTIAL)
        self.assertEqual(health.detail, "Anhao: 4 poses, 0 outfits · empty: outfits")

    def test_all_visible_libraries_empty_is_error(self):
        for name in ("pose-zh.json", "costume-zh.json"):
            (self.anhao_root / "data" / name).write_text("[]", encoding="utf-8")
        health = sources.anhao_health(self.anhao_root)
        self.assertEqual(health.state, sources.ERROR)
        self.assertIn("every library is empty", health.detail)

    def test_rejected_root_names_the_missing_pose_file(self):
        (self.anhao_root / "data" / "pose-zh.json").unlink()
        self.assertIsNone(anhao.find_gallery_root(self.tmp, {"ANHAO_GALLERY_ROOT": str(self.anhao_root)}))
        candidate = anhao.find_gallery_candidate(self.tmp, {"ANHAO_GALLERY_ROOT": str(self.anhao_root)})
        self.assertEqual(candidate, self.anhao_root)

        health = sources.anhao_health(None, candidate=candidate)
        self.assertEqual(health.state, sources.ERROR)
        self.assertIn("`data/pose-zh.json` is missing", health.detail)
        self.assertIn(str(self.anhao_root), health.detail)

        ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=None, anhao_candidate=candidate)
        self.assertIn("pose-zh.json", ctx.source_health()[1].detail)

    def test_gallery_candidate_ignores_unrelated_folders(self):
        ext = self.tmp / "extensions" / "forge-character-builder"
        (ext / "vendor" / "charadb").mkdir(parents=True)  # vendor/ holding only CharaDB
        self.assertIsNone(anhao.find_gallery_candidate(ext, {}))
        self.assertIsNone(anhao.find_gallery_candidate(ext, {"ANHAO_GALLERY_ROOT": str(self.tmp / "nope")}))
        (ext / "vendor" / "data").mkdir()
        self.assertEqual(anhao.find_gallery_candidate(ext, {}), (ext / "vendor").resolve())

    # --- rendering -------------------------------------------------------------

    def test_render_shows_hint_only_when_not_ok(self):
        rows = [sources.SourceHealth("A", sources.OK, "A fine", "unused hint"), sources.SourceHealth("B", sources.MISSING, "B gone", "install B")]
        text = sources.render_markdown(rows)
        self.assertIn("✅ A fine", text)
        self.assertNotIn("unused hint", text)
        self.assertIn("⛔ B gone", text)
        self.assertIn("↳ install B", text)
        self.assertFalse(sources.all_ok(rows))
        self.assertTrue(sources.all_ok(rows[:1]))


class StepHintTests(unittest.TestCase):
    def setUp(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.anhao_root = make_anhao(Path(self._tmp.name) / "anhao").resolve()

    def tearDown(self):
        anhao.load_library.cache_clear()
        anhao._index.cache_clear()
        self._tmp.cleanup()

    def test_everything_installed_has_no_hints(self):
        ctx = session.BuilderContext(charadb=load_catalog(CHARADB_FIXTURE), anhao_root=self.anhao_root)
        self.assertEqual((ctx.hairstyle_hint(), ctx.outfit_hint(), ctx.pose_hint()), ("", "", ""))
        self.assertEqual([row.name for row in ctx.source_health()], ["CharaDB", "Anhao"])

    def test_nothing_installed(self):
        ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=None)
        self.assertEqual(ctx.outfit_source_choices(), [])
        self.assertIn("CHARADB_ROOT", ctx.hairstyle_hint())
        self.assertIn("No outfit source", ctx.outfit_hint())
        self.assertIn("ANHAO_GALLERY_ROOT", ctx.pose_hint())

    def test_hidden_anhao_outfits_point_to_settings(self):
        ctx = session.BuilderContext(charadb=load_catalog(None), anhao_root=self.anhao_root)
        self.assertEqual(ctx.outfit_hint(show_anhao=True), "")
        self.assertEqual(ctx.outfit_source_choices(show_anhao=False), [])
        self.assertIn("Settings → Forge Character Builder", ctx.outfit_hint(show_anhao=False))


if __name__ == "__main__":
    unittest.main()
