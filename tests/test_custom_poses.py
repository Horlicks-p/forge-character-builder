"""Pure-Python tests for the Custom Poses store. Every image is generated here."""

import io
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageFile, ImageOps

from character_builder_core import custom_poses
from character_builder_core.custom_poses import (
    ERROR_HIDDEN,
    ERROR_NO_ITEM,
    KIND_DEPTH,
    KIND_OPENPOSE,
    KIND_PREPROCESSED_CANNY,
    KIND_REFERENCE_CANNY,
    STATUS_OK,
    STATUS_READ_ONLY,
    STATUS_UNAVAILABLE,
    CustomPoseError,
    CustomPoseStore,
)


class SharingViolation(PermissionError):
    winerror = 32


class AccessDenied(PermissionError):
    winerror = 5


def encode(image, fmt="PNG", **options):
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def solid(color=(10, 20, 30), size=(64, 48), mode="RGB"):
    return Image.new(mode, size, color)


def png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def gray16_png(width=4096, height=4, trns=None, orientation=None):
    """Written by hand so the fixture is truly 16-bit regardless of Pillow."""
    samples = np.linspace(0, 65535, width, dtype=np.uint16).tolist()
    row = b"\x00" + b"".join(struct.pack(">H", value) for value in samples)
    body = png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0))
    if trns is not None:
        body += png_chunk(b"tRNS", struct.pack(">H", trns))
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        body += png_chunk(b"eXIf", exif.tobytes()[len(b"Exif\0\0"):])
    return b"\x89PNG\r\n\x1a\n" + body + png_chunk(b"IDAT", zlib.compress(row * height)) + png_chunk(b"IEND", b"")


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "library"
        self.store = custom_poses.open_store(environ={custom_poses.ENV_ROOT: str(self.root)})
        self.assertEqual(self.store.status().state, STATUS_OK)
        self.counter = 0

    def upload(self, data, name="upload.png"):
        self.counter += 1
        folder = self.base / f"upload{self.counter}"
        folder.mkdir()
        path = folder / name
        path.write_bytes(data)
        return path

    def add(self, data=None, filename="upload.png", **options):
        if data is None:
            self.counter += 1
            data = encode(solid((self.counter % 256, 40, 50)))
        return self.store.add(self.upload(data, filename), **options)

    def library_files(self):
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*") if p.is_file())

    def assert_no_residue(self):
        self.assertEqual(self.library_files(), [])
        self.assertEqual(self.store.items(), [])

    def write_index(self, items, schema=custom_poses.SCHEMA_VERSION):
        self.store.index_path.write_text(json.dumps({"schema_version": schema, "items": items}), encoding="utf-8")

    def valid_record(self, **changes):
        record = {
            "id": "a" * 32,
            "name": "Pose",
            "original_filename": "pose.png",
            "input_kind": KIND_DEPTH,
            "sensitive": False,
            "sha256": "b" * 64,
            "created_at": "2026-11-09T07:00:00Z",
        }
        record.update(changes)
        return record


class ImportTests(StoreTestCase):
    def test_png_jpeg_webp_survive_reopening(self):
        ids = []
        for index, (fmt, name) in enumerate((("PNG", "a.png"), ("JPEG", "b.jpg"), ("WEBP", "c.webp"))):
            ids.append(self.add(encode(solid((100 + index, 90, 80)), fmt), name).item.id)
        reopened = custom_poses.open_store(environ={custom_poses.ENV_ROOT: str(self.root)})
        self.assertEqual({item.id for item in reopened.items()}, set(ids))
        for item_id in ids:
            self.assertEqual(reopened.load_control_image(item_id, safe_mode=True).array.shape, (48, 64, 3))
            with Image.open(reopened.image_path(item_id)) as stored:
                self.assertEqual(stored.format, "PNG")
            with Image.open(reopened.thumbnail_path(item_id)) as thumb:
                self.assertEqual(thumb.format, "WEBP")

    def test_content_decides_format_not_the_filename(self):
        result = self.add(encode(solid(), "JPEG"), "really-a-jpeg.png")
        self.assertFalse(result.duplicate)
        with self.assertRaises(CustomPoseError):
            self.add(b"this is plain text", "fake.png")

    def test_multi_picture_jpeg_uses_first_frame(self):
        first, second = solid((200, 0, 0)), solid((0, 0, 200))
        data = encode(first, "MPO", save_all=True, append_images=[second])
        with Image.open(io.BytesIO(data)) as probe:
            self.assertEqual(probe.format, "MPO")
        item = self.add(data, "phone.jpg").item
        red, _, blue = self.store.load_control_image(item.id, safe_mode=True).array[10, 10]
        self.assertGreater(int(red), int(blue))

    def test_animated_images_are_rejected(self):
        frames = [solid((index * 60, 0, 0)) for index in range(3)]
        for fmt in ("PNG", "WEBP"):
            data = encode(frames[0], fmt, save_all=True, append_images=frames[1:], duration=100)
            with self.assertRaisesRegex(CustomPoseError, "Animated"):
                self.add(data, f"anim.{fmt.lower()}")
        self.assert_no_residue()

    def test_other_formats_are_rejected(self):
        for fmt in ("GIF", "BMP", "TIFF"):
            with self.assertRaises(CustomPoseError, msg=fmt):
                self.add(encode(solid(), fmt), "disguised.png")
        self.assert_no_residue()

    def test_extra_registered_openers_cannot_reach_uploads(self):
        class FakeHeif(ImageFile.ImageFile):
            format = "HEIF"

            def _open(self):
                self._mode = "RGB"
                self._size = (8, 8)
                self.tile = []

        Image.init()
        Image.register_open("HEIF", FakeHeif, lambda prefix: prefix[:8] == b"FAKEHEIF")
        self.addCleanup(lambda: (Image.OPEN.pop("HEIF", None), Image.ID.remove("HEIF")))
        self.assertEqual(Image.open(io.BytesIO(b"FAKEHEIF" + b"\0" * 32)).format, "HEIF")
        with self.assertRaises(CustomPoseError):
            self.add(b"FAKEHEIF" + b"\0" * 32, "photo.png")

    def test_broken_uploads_leave_nothing_behind(self):
        good = encode(solid())
        for label, data in (("empty", b""), ("corrupt", b"\x89PNG\r\n\x1a\ngarbage"), ("truncated", good[: len(good) // 2])):
            with self.assertRaises(CustomPoseError, msg=label):
                self.add(data)
        with self.assertRaises(CustomPoseError):
            self.store.add(self.base / "does-not-exist.png")
        self.assert_no_residue()

    def test_upload_size_limit_runs_before_decoding(self):
        with mock.patch.object(custom_poses, "MAX_UPLOAD_BYTES", 16), mock.patch.object(custom_poses, "_decode_upload") as decode:
            with self.assertRaisesRegex(CustomPoseError, "too large"):
                self.add(encode(solid()))
        decode.assert_not_called()
        self.assert_no_residue()

    def test_pixel_limit_holds_when_pillow_protection_is_off(self):
        data = encode(Image.new("L", (6000, 6000)))
        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", None):
            with mock.patch.object(ImageFile.ImageFile, "load", side_effect=AssertionError("decoded")):
                with self.assertRaisesRegex(CustomPoseError, "megapixels"):
                    self.add(data)
        self.assert_no_residue()

    def test_pillow_bomb_error_is_reported_as_too_large(self):
        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            with self.assertRaisesRegex(CustomPoseError, "megapixels"):
                self.add(encode(solid()))
        self.assert_no_residue()

    def test_exif_orientation_is_applied_and_metadata_removed(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees to display correctly
        image = solid(size=(60, 20))
        image.paste((255, 0, 0), (0, 0, 10, 20))  # red on the left edge
        item = self.add(encode(image, "JPEG", exif=exif, quality=100), "rotated.jpg").item
        array = self.store.load_control_image(item.id, safe_mode=True).array
        self.assertEqual(array.shape[:2], (60, 20))
        self.assertGreater(int(array[2, 10, 0]), 200)  # the red edge moved to the top
        with Image.open(self.store.image_path(item.id)) as stored:
            self.assertEqual(dict(stored.getexif()), {})
            self.assertNotIn("icc_profile", stored.info)

    def test_landscape_and_portrait_keep_their_ratio(self):
        for size in ((300, 100), (100, 300)):
            item = self.add(encode(solid(size=size))).item
            self.assertEqual(self.store.load_control_image(item.id, safe_mode=True).array.shape[:2], size[::-1])
            with Image.open(self.store.thumbnail_path(item.id)) as thumb:
                self.assertEqual(max(thumb.size), max(size) if max(size) < custom_poses.THUMB_EDGE else custom_poses.THUMB_EDGE)


class NormalizationTests(StoreTestCase):
    def test_real_16_bit_gradient_keeps_its_levels(self):
        item = self.add(gray16_png(), "depth.png", input_kind=KIND_DEPTH).item
        array = self.store.load_control_image(item.id, safe_mode=True).array
        channel = array[:, :, 0]
        self.assertGreaterEqual(len(np.unique(channel)), 200)
        self.assertEqual((int(channel[0, 0]), int(channel[0, -1])), (0, 255))
        self.assertTrue((array[:, :, 0] == array[:, :, 2]).all())

    def test_16_bit_orientation_is_applied(self):
        item = self.add(gray16_png(64, 4, orientation=6), input_kind=KIND_DEPTH).item
        array = self.store.load_control_image(item.id, safe_mode=True).array
        self.assertEqual(array.shape[:2], (64, 4))
        self.assertGreater(len(np.unique(array)), 32)

    def test_16_bit_mapping_uses_the_full_range(self):
        data = np.array([[0, 128, 257, 32768, 65535]], dtype=np.uint16)
        result = custom_poses._to_canonical(Image.fromarray(data))
        self.assertEqual(np.asarray(result)[0, :, 0].tolist(), [0, 0, 1, 128, 255])

    def test_16_bit_png_with_transparency_key_is_rejected(self):
        with self.assertRaisesRegex(CustomPoseError, "transparency"):
            self.add(gray16_png(64, 4, trns=0))
        self.assert_no_residue()

    def test_float_and_legacy_integer_modes_are_rejected(self):
        for mode in ("I", "F"):
            image = Image.new(mode, (4, 4))
            image.format = "PNG"
            with self.assertRaises(CustomPoseError, msg=mode) as caught:
                custom_poses._check_opened(image)
            if mode == "I":
                self.assertIn("Pillow", str(caught.exception))

    def test_fully_opaque_rgba_is_stored_as_rgb(self):
        item = self.add(encode(solid((1, 2, 3, 255), mode="RGBA"))).item
        with Image.open(self.store.image_path(item.id)) as stored:
            self.assertEqual(stored.mode, "RGB")

    def _transparent(self):
        pixels = np.full((32, 32, 4), 128, np.uint8)
        pixels[..., 3] = 0
        pixels[10:20, 10:20] = (255, 0, 0, 255)
        return encode(Image.fromarray(pixels, "RGBA"))

    def test_transparency_is_flattened_by_the_current_input_kind(self):
        item = self.add(self._transparent(), input_kind=KIND_OPENPOSE).item
        with Image.open(self.store.image_path(item.id)) as stored:
            self.assertEqual(stored.mode, "RGBA")
        self.assertEqual(self.store.load_control_image(item.id, safe_mode=True).array[0, 0].tolist(), [0, 0, 0])
        self.store.update(item.id, input_kind=KIND_REFERENCE_CANNY)
        sent = self.store.load_control_image(item.id, safe_mode=True)
        self.assertEqual(sent.array[0, 0].tolist(), [255, 255, 255])
        self.assertEqual(sent.array[15, 15].tolist(), [255, 0, 0])
        self.assertEqual(sent.preprocessor, "canny")

    def test_palette_and_gray_alpha_transparency(self):
        palette = Image.new("P", (16, 16), 0)
        item = self.add(encode(palette, transparency=0), input_kind=KIND_REFERENCE_CANNY).item
        self.assertEqual(self.store.load_control_image(item.id, safe_mode=True).array[0, 0].tolist(), [255, 255, 255])
        gray_alpha = Image.new("LA", (16, 16), (200, 0))
        item = self.add(encode(gray_alpha), input_kind=KIND_DEPTH).item
        self.assertEqual(self.store.load_control_image(item.id, safe_mode=True).array[0, 0].tolist(), [0, 0, 0])

    def test_preview_is_exactly_what_is_sent_and_never_the_thumbnail(self):
        item = self.add(self._transparent(), input_kind=KIND_OPENPOSE).item
        self.store.thumbnail_path(item.id).unlink()
        preview = self.store.preview_image(item.id, safe_mode=True)
        self.assertEqual(preview.mode, "RGB")
        self.assertTrue((np.asarray(preview) == self.store.load_control_image(item.id, safe_mode=True).array).all())

    def test_long_edge_is_limited_and_hash_uses_original_bytes(self):
        import hashlib

        data = encode(solid(size=(4096, 1024)))
        result = self.add(data)
        self.assertTrue(result.resized)
        self.assertEqual(self.store.load_control_image(result.item.id, safe_mode=True).array.shape[:2], (512, 2048))
        self.assertEqual(result.item.sha256, hashlib.sha256(data).hexdigest())
        self.assertTrue(self.add(data).duplicate)

    def test_images_within_the_limit_keep_their_size(self):
        result = self.add(encode(solid(size=(custom_poses.MAX_STORED_EDGE, 100))))
        self.assertFalse(result.resized)
        self.assertEqual(self.store.load_control_image(result.item.id, safe_mode=True).array.shape[:2], (100, custom_poses.MAX_STORED_EDGE))

    def test_stored_edge_is_a_module_constant(self):
        with mock.patch.object(custom_poses, "MAX_STORED_EDGE", 100):
            result = self.add(encode(solid(size=(200, 50))))
        self.assertEqual(self.store.load_control_image(result.item.id, safe_mode=True).array.shape[:2], (25, 100))

    def test_one_pixel_lines_survive_downscaling(self):
        size = 6000
        pixels = np.zeros((size, 1000), np.uint8)
        pixels[:, 500] = 255
        small, resized = custom_poses._fit_stored_edge(Image.fromarray(pixels))
        self.assertTrue(resized)
        self.assertGreaterEqual(int(np.asarray(small).max(axis=1).min()), 64)

    def test_only_shrunk_preprocessed_canny_warns_about_lines(self):
        big = encode(solid(size=(3000, 100)))
        self.assertTrue(self.add(big, input_kind=KIND_PREPROCESSED_CANNY).lines_may_fade)
        self.assertFalse(self.add(encode(solid((1, 1, 1), size=(3000, 100))), input_kind=KIND_DEPTH).lines_may_fade)
        self.assertFalse(self.add(encode(solid((2, 2, 2))), input_kind=KIND_PREPROCESSED_CANNY).lines_may_fade)

    def test_preprocessor_mapping(self):
        for kind, expected in (
            (KIND_REFERENCE_CANNY, "canny"),
            (KIND_PREPROCESSED_CANNY, "None"),
            (KIND_OPENPOSE, "None"),
            (KIND_DEPTH, "None"),
        ):
            item = self.add(input_kind=kind).item
            self.assertEqual(self.store.load_control_image(item.id, safe_mode=True).preprocessor, expected)


class ManagementTests(StoreTestCase):
    def test_files_use_the_generated_id_never_the_name(self):
        item = self.add(original_filename="C:\\Users\\me\\secret\\my pose.png").item
        self.assertRegex(item.id, r"^[0-9a-f]{32}$")
        self.assertEqual(item.original_filename, "my pose.png")
        self.assertEqual(item.name, "my pose")
        self.assertEqual(self.library_files(), [f"images/{item.id}.png", "index.json", f"thumbs/{item.id}.webp"])

        named = self.add(name="../../evil").item
        self.assertEqual(named.name, "../../evil")
        self.assertTrue(self.store.image_path(named.id).is_file())

    def test_names_are_cleaned(self):
        self.assertEqual(self.add(name="  a\x00b\n  c\u202e ").item.name, "ab c")
        self.assertEqual(len(self.add(name="x" * 500).item.name), custom_poses.MAX_NAME_LENGTH)
        self.assertEqual(self.add(name="   ", original_filename=".png").item.name, ".png")

    def test_unknown_input_kind_is_refused(self):
        with self.assertRaises(CustomPoseError):
            self.add(input_kind="lineart")
        item = self.add().item
        with self.assertRaises(CustomPoseError):
            self.store.update(item.id, input_kind="lineart")
        self.assertEqual(self.store.get(item.id), item)

    def test_duplicate_returns_the_existing_item(self):
        data = encode(solid())
        first = self.add(data, name="first").item
        second = self.add(data, name="second", sensitive=True)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.item, first)
        self.assertEqual(len(self.store.items()), 1)

    def test_update_changes_details_only(self):
        item = self.add(name="old").item
        updated = self.store.update(item.id, name="new", input_kind=KIND_OPENPOSE, sensitive=True)
        self.assertEqual((updated.name, updated.input_kind, updated.sensitive), ("new", KIND_OPENPOSE, True))
        self.assertEqual((updated.id, updated.sha256, updated.created_at), (item.id, item.sha256, item.created_at))
        self.assertTrue(self.store.image_path(item.id).is_file())
        with self.assertRaises(CustomPoseError):
            self.store.update(item.id, name="   ")

    def test_delete_removes_only_that_item(self):
        keep, remove = self.add().item, self.add().item
        result = self.store.delete(remove.id)
        self.assertEqual((result.item, result.orphans), (remove, ()))
        self.assertEqual(self.store.items(), [keep])
        self.assertEqual(self.library_files(), sorted([f"images/{keep.id}.png", "index.json", "index.json.bak", f"thumbs/{keep.id}.webp"]))

    def test_stale_ids_fail_cleanly(self):
        item = self.add().item
        self.store.delete(item.id)
        other = self.add().item
        for action in (
            lambda: self.store.delete(item.id),
            lambda: self.store.update(item.id, name="x"),
            lambda: self.store.load_control_image(item.id, safe_mode=False),
        ):
            with self.assertRaisesRegex(CustomPoseError, ERROR_NO_ITEM):
                action()
        self.assertEqual(self.store.items(), [other])

    def test_delete_accepts_no_paths(self):
        victim = self.base / "victim.txt"
        victim.write_text("keep me")
        for bad in (str(victim), "../victim.txt", "", None):
            with self.assertRaisesRegex(CustomPoseError, ERROR_NO_ITEM, msg=bad):
                self.store.delete(bad)
        self.assertTrue(victim.exists())

    def test_search_and_newest_first_order(self):
        first = self.add(name="Standing Pose").item
        second = self.add(name="sitting").item
        self.assertEqual([item.id for item in self.store.items()], [second.id, first.id])
        self.assertEqual(self.store.items("STANDING"), [first])
        self.assertEqual(self.store.items(second.id[:8]), [second])

    def test_safe_mode_hides_and_blocks_sensitive_items(self):
        item = self.add(sensitive=True).item
        self.assertEqual(self.store.items(safe_mode=True), [])
        self.assertEqual(self.store.items(safe_mode=False), [item])
        for action in (self.store.load_control_image, self.store.preview_image):
            with self.assertRaisesRegex(CustomPoseError, ERROR_HIDDEN):
                action(item.id, safe_mode=True)
        self.assertEqual(self.store.load_control_image(item.id, safe_mode=False).item, item)

    def test_missing_or_damaged_stored_image_blocks_sending(self):
        item = self.add().item
        path = self.store.image_path(item.id)
        path.write_bytes(b"damaged")
        with self.assertRaisesRegex(CustomPoseError, "could not be read"):
            self.store.load_control_image(item.id, safe_mode=False)
        path.unlink()
        with self.assertRaisesRegex(CustomPoseError, "Missing"):
            self.store.load_control_image(item.id, safe_mode=False)


class IndexSafetyTests(StoreTestCase):
    def assert_read_only(self):
        before = self.store.index_path.read_bytes()
        status = self.store.status()
        self.assertEqual(status.state, STATUS_READ_ONLY, status.message)
        self.assertFalse(status.writable)
        self.assertEqual(self.store.items(), [])
        with self.assertRaises(CustomPoseError):
            self.add()
        self.assertEqual(self.store.index_path.read_bytes(), before)
        self.assertFalse(self.store.backup_path.exists())

    def test_invalid_ids_make_the_index_read_only(self):
        for bad in ("../../outside", "C:\\windows\\x", "\\\\server\\share", "/etc/passwd", "A" * 32, "a" * 31, "a" * 32 + "\n"):
            with self.subTest(bad):
                self.write_index([self.valid_record(id=bad)])
                self.assert_read_only()

    def test_invalid_records_make_the_index_read_only(self):
        cases = {
            "kind": [self.valid_record(input_kind="lineart")],
            "hash": [self.valid_record(sha256="xyz")],
            "date": [self.valid_record(created_at="yesterday")],
            "flag": [self.valid_record(sensitive="no")],
            "name": [self.valid_record(name=" ")],
            "path field": [self.valid_record(image="../outside.png")],
            "duplicate id": [self.valid_record(), self.valid_record(sha256="c" * 64)],
            "duplicate hash": [self.valid_record(), self.valid_record(id="d" * 32)],
        }
        for label, items in cases.items():
            with self.subTest(label):
                self.write_index(items)
                self.assert_read_only()

    def test_unknown_schema_is_never_rebuilt(self):
        self.write_index([self.valid_record()], schema=99)
        self.assert_read_only()

    def test_corrupt_index_is_kept_and_does_not_replace_the_backup(self):
        self.add()
        self.add()
        good_backup = self.store.backup_path.read_bytes()
        for corrupt in (b"{ not json", b"\xff\xfe\x00", b"[]", b""):
            with self.subTest(corrupt):
                self.store.index_path.write_bytes(corrupt)
                self.assertEqual(self.store.status().state, STATUS_READ_ONLY)
                with self.assertRaises(CustomPoseError):
                    self.add()
                self.assertEqual(self.store.index_path.read_bytes(), corrupt)
                self.assertEqual(self.store.backup_path.read_bytes(), good_backup)

    def test_repairing_the_index_restores_writing_without_restart(self):
        item = self.add().item
        good = self.store.index_path.read_bytes()
        self.store.index_path.write_bytes(b"broken")
        self.assertEqual(self.store.items(), [])
        self.store.index_path.write_bytes(good)
        self.assertEqual(self.store.items(), [item])
        self.assertEqual(self.store.status().state, STATUS_OK)

    def test_missing_index_with_a_backup_is_not_treated_as_empty(self):
        self.add()
        self.add()
        self.store.index_path.unlink()
        self.assertEqual(self.store.status().state, STATUS_READ_ONLY)
        with self.assertRaises(CustomPoseError):
            self.add()
        self.assertFalse(self.store.index_path.exists())

    def test_symlinks_are_not_followed(self):
        item = self.add().item
        outside = self.base / "outside.png"
        outside.write_bytes(encode(solid((9, 9, 9))))
        path = self.store.image_path(item.id)
        path.unlink()
        try:
            os.symlink(outside, path)
        except OSError:
            self.skipTest("symlinks need extra privileges here")
        self.assertIsNone(self.store.image_path(item.id))
        with self.assertRaises(CustomPoseError):
            self.store.load_control_image(item.id, safe_mode=False)
        result = self.store.delete(item.id)
        self.assertIn(f"images/{item.id}.png", result.orphans)
        self.assertTrue(outside.exists())


    @unittest.skipUnless(os.name == "nt", "NTFS junctions are Windows-only")
    def test_junctioned_folder_is_not_followed(self):
        import _winapi
        import shutil

        item = self.add().item
        outside = self.base / "outside"
        outside.mkdir()
        decoy = outside / f"{item.id}.png"
        decoy.write_bytes(encode(solid((9, 9, 9))))
        shutil.rmtree(self.root / "images")
        _winapi.CreateJunction(str(outside), str(self.root / "images"))
        self.addCleanup(lambda: os.path.isdir(self.root / "images") and os.rmdir(self.root / "images"))

        self.assertIsNone(self.store.image_path(item.id))
        with self.assertRaises(CustomPoseError):
            self.store.load_control_image(item.id, safe_mode=False)
        with mock.patch.object(self.store, "_stage") as stage:
            with self.assertRaisesRegex(CustomPoseError, "redirected"):
                self.add()
        stage.assert_not_called()
        result = self.store.delete(item.id)
        self.assertIn(f"images/{item.id}.png", result.orphans)
        self.assertEqual([p.name for p in outside.iterdir()], [decoy.name])


class FailureTests(StoreTestCase):
    @staticmethod
    def failing_replace(target_name, error):
        real = os.replace

        def replace(source, destination):
            if Path(destination).name == target_name:
                raise error
            return real(source, destination)

        return replace

    def test_failed_index_write_rolls_back_the_new_files(self):
        existing = self.add().item
        before = self.store.index_path.read_bytes()
        with mock.patch.object(custom_poses.os, "replace", self.failing_replace("index.json", OSError("disk full"))):
            with self.assertRaisesRegex(CustomPoseError, "could not be saved"):
                self.add()
        self.assertEqual(self.store.index_path.read_bytes(), before)
        self.assertEqual(self.store.items(), [existing])
        self.assertEqual(self.store.orphans(), [])
        self.assertEqual(sorted(p.name for p in self.root.glob("*.tmp")), [])

    def test_failed_image_save_leaves_nothing(self):
        data = encode(solid())
        with mock.patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(CustomPoseError, "could not be saved"):
                self.add(data)
        self.assert_no_residue()

    def test_failed_install_leaves_nothing(self):
        real = os.replace

        def replace(source, destination):
            if str(destination).endswith(".webp"):
                raise OSError("disk full")
            return real(source, destination)

        with mock.patch.object(custom_poses.os, "replace", replace):
            with self.assertRaises(CustomPoseError):
                self.add()
        self.assert_no_residue()

    def test_sharing_violations_are_retried_three_times(self):
        item = self.add().item
        real = os.replace
        calls = []

        def replace(source, destination):
            if Path(destination).name == "index.json":
                calls.append(1)
                if len(calls) <= 3:
                    raise SharingViolation("in use")
            return real(source, destination)

        with mock.patch.object(custom_poses.os, "replace", replace), mock.patch.object(custom_poses.time, "sleep") as sleep:
            self.store.update(item.id, name="changed")
        self.assertEqual(len(calls), 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.15, 0.45])
        self.assertEqual(self.store.get(item.id).name, "changed")

    def test_retries_stop_after_three_and_report_an_error(self):
        item = self.add().item
        with mock.patch.object(custom_poses.os, "replace", self.failing_replace("index.json", AccessDenied("denied"))):
            with mock.patch.object(custom_poses.time, "sleep") as sleep:
                with self.assertRaises(CustomPoseError):
                    self.store.update(item.id, name="changed")
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(self.store.get(item.id), item)

    def test_other_errors_are_not_retried(self):
        item = self.add().item
        for error in (OSError("disk full"), PermissionError("plain permission error")):
            with self.subTest(type(error).__name__):
                with mock.patch.object(custom_poses.os, "replace", self.failing_replace("index.json", error)):
                    with mock.patch.object(custom_poses.time, "sleep") as sleep:
                        with self.assertRaises(CustomPoseError):
                            self.store.update(item.id, name="changed")
                sleep.assert_not_called()

    def test_locked_image_becomes_an_orphan_and_the_record_is_gone(self):
        item = self.add().item
        with mock.patch.object(Path, "unlink", side_effect=SharingViolation("in use")):
            with mock.patch.object(custom_poses.time, "sleep"):
                result = self.store.delete(item.id)
        self.assertEqual(set(result.orphans), {f"images/{item.id}.png", f"thumbs/{item.id}.webp"})
        self.assertIsNone(self.store.get(item.id))
        self.assertEqual(self.store.orphans(), [f"images/{item.id}.png", f"thumbs/{item.id}.webp"])

    def test_orphans_are_reported_but_never_deleted(self):
        stray = self.root / "images" / ("f" * 32 + ".png")
        stray.write_bytes(b"stray")
        reopened = custom_poses.open_store(environ={custom_poses.ENV_ROOT: str(self.root)})
        self.assertEqual(reopened.orphans(), [f"images/{stray.name}"])
        self.assertTrue(stray.exists())

    def test_corrupt_index_reports_no_orphans(self):
        item = self.add().item
        self.store.index_path.write_bytes(b"broken")
        self.assertEqual(self.store.orphans(), [])
        self.assertTrue(self.store.image_path(item.id).exists())


class RevisionTests(StoreTestCase):
    def test_each_commit_increments_once(self):
        start = custom_poses.revision()
        item = self.add().item
        self.assertEqual(custom_poses.revision(), start + 1)
        self.store.update(item.id, name="renamed")
        self.assertEqual(custom_poses.revision(), start + 2)
        self.store.delete(item.id)
        self.assertEqual(custom_poses.revision(), start + 3)

    def test_no_change_means_no_revision(self):
        data = encode(solid())
        item = self.add(data, name="same").item
        start = custom_poses.revision()
        self.store.update(item.id, name="same")
        self.assertTrue(self.add(data).duplicate)
        self.store.items()
        self.assertEqual(custom_poses.revision(), start)

    def test_failed_commit_does_not_change_revision(self):
        item = self.add().item
        start = custom_poses.revision()
        real = os.replace

        def replace(source, destination):
            if Path(destination).name == "index.json":
                raise OSError("disk full")
            return real(source, destination)

        with mock.patch.object(custom_poses.os, "replace", replace):
            with self.assertRaises(CustomPoseError):
                self.store.update(item.id, name="changed")
        self.assertEqual(custom_poses.revision(), start)

    def test_delete_with_orphans_still_changes_revision(self):
        item = self.add().item
        start = custom_poses.revision()
        with mock.patch.object(Path, "unlink", side_effect=SharingViolation("in use")), mock.patch.object(custom_poses.time, "sleep"):
            self.assertTrue(self.store.delete(item.id).orphans)
        self.assertEqual(custom_poses.revision(), start + 1)


class ConcurrencyTests(StoreTestCase):
    def test_parallel_sessions_lose_no_updates(self):
        uploads = [self.upload(encode(solid((index, 7, 9)))) for index in range(24)]
        errors = []

        def worker(path):
            try:
                self.store.add(path)
            except Exception as error:  # surfaced by the assertion below
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(path,)) for path in uploads]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.items()), 24)

        survivors = self.store.items()
        victims, keep = survivors[:12], survivors[12:]
        threads = [threading.Thread(target=self.store.delete, args=(item.id,)) for item in victims]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual({item.id for item in self.store.items()}, {item.id for item in keep})

    def test_same_file_uploaded_twice_at_once_is_stored_once(self):
        data = encode(solid())
        paths = [self.upload(data) for _ in range(8)]
        results = []
        threads = [threading.Thread(target=lambda p=path: results.append(self.store.add(p))) for path in paths]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 8)
        self.assertEqual(len(self.store.items()), 1)
        self.assertEqual(len({result.item.id for result in results}), 1)
        self.assertEqual(self.store.orphans(), [])

    def test_decoding_does_not_hold_the_lock(self):
        acquired = []

        def spy(data):
            def probe():
                free = custom_poses._LOCK.acquire(blocking=False)
                acquired.append(free)
                if free:
                    custom_poses._LOCK.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            return real(data)

        real = custom_poses._decode_upload
        with mock.patch.object(custom_poses, "_decode_upload", spy):
            self.add()
        self.assertEqual(acquired, [True])


class RootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_environment_wins_and_does_not_touch_forge(self):
        target = self.base / "mine"
        untouched = mock.Mock(side_effect=AssertionError("should not be called"))
        root, reason = custom_poses.find_root({custom_poses.ENV_ROOT: str(target)}, untouched)
        self.assertEqual((root, reason), (target, ""))
        untouched.assert_not_called()

    def test_forge_data_path_is_the_default(self):
        root, _ = custom_poses.find_root({}, lambda: str(self.base))
        self.assertEqual(root, self.base / "data" / "forge-character-builder" / "custom-poses")

    def test_no_guessing_when_forge_is_absent(self):
        store = custom_poses.open_store({}, lambda: None)
        self.assertEqual(store.status().state, STATUS_UNAVAILABLE)
        self.assertIn(custom_poses.ENV_ROOT, store.status().message)
        self.assertEqual(store.items(), [])
        self.assertIsNone(store.get("a" * 32))
        self.assertEqual(store.orphans(), [])
        with self.assertRaises(CustomPoseError):
            store.add(self.base / "x.png")
        with self.assertRaises(CustomPoseError):
            store.load_control_image("a" * 32, safe_mode=False)

    def test_without_a_data_path_provider_the_library_is_unavailable(self):
        self.assertEqual(custom_poses.open_store({}).status().state, STATUS_UNAVAILABLE)

    def test_relative_environment_path_is_refused(self):
        store = custom_poses.open_store({custom_poses.ENV_ROOT: "relative/folder"}, lambda: None)
        self.assertEqual(store.status().state, STATUS_UNAVAILABLE)
        self.assertFalse(Path("relative/folder").exists())

    def test_blank_environment_variable_counts_as_unset(self):
        root, _ = custom_poses.find_root({custom_poses.ENV_ROOT: "   "}, lambda: str(self.base))
        self.assertEqual(root, self.base / "data" / "forge-character-builder" / "custom-poses")

    def test_invalid_environment_path_never_falls_back(self):
        blocker = self.base / "a-file"
        blocker.write_text("not a folder")
        fallback = self.base / "fallback"
        store = custom_poses.open_store({custom_poses.ENV_ROOT: str(blocker / "library")}, lambda: str(fallback))
        self.assertEqual(store.status().state, STATUS_UNAVAILABLE)
        self.assertFalse(fallback.exists())

    def test_opening_creates_the_folders(self):
        target = self.base / "deep" / "library"
        store = custom_poses.open_store({custom_poses.ENV_ROOT: str(target)})
        self.assertEqual(store.status().state, STATUS_OK)
        self.assertTrue((target / "images").is_dir())
        self.assertTrue((target / "thumbs").is_dir())
        self.assertFalse((target / "index.json").exists())

    def test_store_without_a_root_is_unavailable(self):
        self.assertEqual(CustomPoseStore(None, "reason").status().message, "Custom Poses unavailable: reason")


if __name__ == "__main__":
    unittest.main()
