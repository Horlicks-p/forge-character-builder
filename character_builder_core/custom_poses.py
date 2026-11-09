"""Custom Poses: pose images the user adds from the browser and keeps locally.

One image is one pose. The store owns a folder holding ``index.json``,
``images/<id>.png`` and ``thumbs/<id>.webp``; file locations are always derived
from the ID, so the index cannot point anywhere else.

No Gradio or Forge imports, so every rule is testable alone.
The store never raises for a broken library when reading: it reports the
problem through ``status()`` and refuses to write, so a damaged index can never
be replaced by an empty one.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Callable, Mapping, Optional, TypeVar

import numpy as np
from PIL import Image, ImageOps

from .assets import safe_join

logger = logging.getLogger(__name__)

# Image.open(formats=...) looks every name up directly, and WebP is only
# registered once all plugins are loaded.
Image.init()

SOURCE_ID = "custom_pose"
SCHEMA_VERSION = 1
ENV_ROOT = "FCB_CUSTOM_POSE_DIR"

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_DECODE_PIXELS = 25_000_000
MAX_STORED_EDGE = 2048
THUMB_EDGE = 320
MAX_NAME_LENGTH = 120
MAX_FILENAME_LENGTH = 255

KIND_REFERENCE_CANNY = "reference_canny"
KIND_PREPROCESSED_CANNY = "preprocessed_canny"
KIND_OPENPOSE = "openpose_skeleton"
KIND_DEPTH = "depth_map"

# input kind -> ControlNet preprocessor; "None" skips preprocessing.
INPUT_KINDS = {
    KIND_REFERENCE_CANNY: "canny",
    KIND_PREPROCESSED_CANNY: "None",
    KIND_OPENPOSE: "None",
    KIND_DEPTH: "None",
}
INPUT_KIND_LABELS = {
    KIND_REFERENCE_CANNY: "Pose reference (Canny)",
    KIND_PREPROCESSED_CANNY: "Preprocessed Canny map",
    KIND_OPENPOSE: "OpenPose skeleton",
    KIND_DEPTH: "Depth map",
}

STATUS_OK = "ok"
STATUS_READ_ONLY = "read_only"
STATUS_UNAVAILABLE = "unavailable"

ERROR_NO_ITEM = "Item no longer exists"
ERROR_HIDDEN = "This pose is hidden by Safe Mode"

# MPO has no opener of its own: the JPEG opener returns it for multi-picture files.
_OPENERS = ("PNG", "JPEG", "WEBP")
_ALLOWED_FORMATS = (*_OPENERS, "MPO")
_GRAY16_MODES = {"I;16", "I;16L", "I;16B"}
_ALLOWED_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK"} | _GRAY16_MODES

_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_ITEM_FIELDS = {"id", "name", "original_filename", "input_kind", "sensitive", "sha256", "created_at"}

# Windows reports a file another handle still holds as access denied (5) or a
# sharing violation (32). Only those are worth waiting out.
_RETRY_WINERRORS = {5, 32}
_RETRY_DELAYS = (0.05, 0.15, 0.45)

# One library per process, so one lock is enough and every store shares it.
_LOCK = threading.RLock()
_revision = 0

_T = TypeVar("_T")


class CustomPoseError(Exception):
    """An operation failed; the message is shown to the user as-is."""


@dataclass(frozen=True)
class CustomPose:
    id: str
    name: str
    original_filename: str
    input_kind: str
    sensitive: bool
    sha256: str  # of the uploaded bytes, not the stored PNG
    created_at: str

    @property
    def preprocessor(self) -> str:
        return INPUT_KINDS[self.input_kind]

    @property
    def input_label(self) -> str:
        return INPUT_KIND_LABELS[self.input_kind]


@dataclass(frozen=True)
class AddResult:
    item: CustomPose
    duplicate: bool = False
    resized: bool = False

    @property
    def lines_may_fade(self) -> bool:
        """A preprocessed edge map was shrunk, which dims one-pixel lines."""
        return self.resized and self.item.input_kind == KIND_PREPROCESSED_CANNY


@dataclass(frozen=True)
class DeleteResult:
    item: CustomPose
    orphans: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoreStatus:
    state: str
    message: str
    count: int = 0

    @property
    def writable(self) -> bool:
        return self.state == STATUS_OK


@dataclass(frozen=True)
class ControlImage:
    """What goes to ControlNet: flattened 8-bit RGB and its preprocessor."""

    item: CustomPose
    array: np.ndarray
    preprocessor: str


def revision() -> int:
    """Grows with every committed change to the library in this process."""
    with _LOCK:
        return _revision


# --- text ---------------------------------------------------------------------


def _clean_text(value: object, limit: int) -> str:
    text = "".join(ch for ch in str(value or "") if not unicodedata.category(ch).startswith("C"))
    return " ".join(text.split())[:limit].strip()


def _base_name(value: object) -> str:
    # PureWindowsPath understands both separator styles on every platform.
    return PureWindowsPath(str(value or "")).name


def _clean_filename(value: object) -> str:
    return _clean_text(_base_name(value), MAX_FILENAME_LENGTH)


def _default_name(filename: str) -> str:
    return _clean_text(PureWindowsPath(filename).stem, MAX_NAME_LENGTH) or "Untitled pose"


# --- windows-safe file operations -----------------------------------------------


def _retry(action: Callable[[], _T]) -> _T:
    for delay in (*_RETRY_DELAYS, None):
        try:
            return action()
        except PermissionError as error:
            if delay is None or getattr(error, "winerror", None) not in _RETRY_WINERRORS:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def _remove_quietly(path: Optional[Path]) -> bool:
    """Delete ``path``; ``False`` means it may still be on disk."""
    if path is None:
        return False
    try:
        _retry(path.unlink)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


# --- image handling --------------------------------------------------------------


def _read_upload(upload: object) -> bytes:
    path = Path(str(upload or ""))
    try:
        if not path.is_file():
            raise CustomPoseError("The uploaded file could not be found. Please upload it again.")
        size = path.stat().st_size
        if size == 0:
            raise CustomPoseError("The uploaded file is empty.")
        if size > MAX_UPLOAD_BYTES:
            raise CustomPoseError(f"The file is too large (limit: {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB).")
        with path.open("rb") as handle:
            data = handle.read(MAX_UPLOAD_BYTES + 1)
    except OSError as error:
        raise CustomPoseError(f"The uploaded file could not be read ({type(error).__name__}).") from error
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise CustomPoseError("The uploaded file changed while it was being read. Please upload it again.")
    return data


def _check_opened(image: Image.Image) -> None:
    """Everything that can be rejected from the header, before decoding pixels."""
    if image.format not in _ALLOWED_FORMATS:
        raise CustomPoseError("Unsupported image format. Use PNG, JPEG or WebP.")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise CustomPoseError("The image has no pixels.")
    if width * height > MAX_DECODE_PIXELS:
        raise CustomPoseError(f"The image is too large (limit: {MAX_DECODE_PIXELS // 1_000_000} megapixels).")
    if image.format != "MPO" and getattr(image, "is_animated", False):
        raise CustomPoseError("Animated images are not supported.")
    if image.mode == "I" and image.format == "PNG":
        raise CustomPoseError(
            "This 16-bit PNG was opened in a legacy mode by an old Pillow version. "
            "Custom Poses needs the Pillow version Forge Neo installs."
        )
    if image.mode not in _ALLOWED_MODES:
        raise CustomPoseError(f"Unsupported image color mode: {image.mode}.")
    if image.mode in _GRAY16_MODES and "transparency" in image.info:
        raise CustomPoseError("16-bit PNG images with a transparency key are not supported.")


def _to_canonical(image: Image.Image) -> Image.Image:
    """8-bit RGB, or RGBA only when something is actually transparent."""
    if image.mode in _GRAY16_MODES:
        values = np.asarray(image).astype(np.uint32)
        scaled = ((values * 255 + 32767) // 65535).astype(np.uint8)
        return Image.fromarray(scaled).convert("RGB")
    if image.has_transparency_data:
        rgba = image.convert("RGBA")
        if rgba.getchannel("A").getextrema()[0] < 255:
            return rgba
        return rgba.convert("RGB")
    return image.convert("RGB")


def _fit_stored_edge(image: Image.Image) -> tuple[Image.Image, bool]:
    longest = max(image.size)
    if longest <= MAX_STORED_EDGE:
        return image, False
    scale = MAX_STORED_EDGE / longest
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS), True


def _decode_upload(data: bytes) -> tuple[Image.Image, bool]:
    """Validated canonical image and whether it had to be shrunk."""
    try:
        # ``formats`` keeps Forge's extra HEIF / JXL openers away from uploads.
        with Image.open(io.BytesIO(data), formats=_OPENERS) as image:
            _check_opened(image)
            image.load()
            canonical = _to_canonical(ImageOps.exif_transpose(image))
    except CustomPoseError:
        raise
    except Image.DecompressionBombError as error:
        raise CustomPoseError(f"The image is too large (limit: {MAX_DECODE_PIXELS // 1_000_000} megapixels).") from error
    except Exception as error:  # Pillow raises many types on hostile input
        raise CustomPoseError("The file is not a valid PNG, JPEG or WebP image.") from error
    return _fit_stored_edge(canonical)


def _flatten(image: Image.Image, input_kind: str) -> Image.Image:
    """The exact RGB picture ControlNet and the preview both get."""
    if image.mode == "RGB":
        return image
    background = (255, 255, 255, 255) if input_kind == KIND_REFERENCE_CANNY else (0, 0, 0, 255)
    return Image.alpha_composite(Image.new("RGBA", image.size, background), image.convert("RGBA")).convert("RGB")


# --- index ----------------------------------------------------------------------


def _parse_item(raw: object) -> CustomPose:
    if not isinstance(raw, dict) or set(raw) != _ITEM_FIELDS:
        raise ValueError("an item has missing or unknown fields")
    item_id, kind, digest, created = raw["id"], raw["input_kind"], raw["sha256"], raw["created_at"]
    if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
        raise ValueError("an item has an invalid ID")
    if not isinstance(kind, str) or kind not in INPUT_KINDS:
        raise ValueError(f"item {item_id} has an unknown input kind")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError(f"item {item_id} has an invalid hash")
    if not isinstance(created, str) or not _TIMESTAMP.fullmatch(created):
        raise ValueError(f"item {item_id} has an invalid date")
    if not isinstance(raw["sensitive"], bool):
        raise ValueError(f"item {item_id} has an invalid sensitive flag")
    if not isinstance(raw["name"], str) or not raw["name"].strip() or not isinstance(raw["original_filename"], str):
        raise ValueError(f"item {item_id} has an invalid name")
    return CustomPose(**raw)


def _parse_index(data: bytes) -> list[CustomPose]:
    try:
        document = json.loads(data.decode("utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("items"), list):
            raise ValueError("the file is not a Custom Poses index")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version {document.get('schema_version')!r}")
        items = [_parse_item(raw) for raw in document["items"]]
    except ValueError as error:  # includes JSON and UTF-8 decoding errors
        raise CustomPoseError(f"The Custom Poses index is invalid, so the library is read-only: {error}.") from error
    if len({item.id for item in items}) != len(items) or len({item.sha256 for item in items}) != len(items):
        raise CustomPoseError("The Custom Poses index is invalid, so the library is read-only: duplicate IDs or hashes.")
    return items


def _serialize_index(items: list[CustomPose]) -> bytes:
    document = {
        "schema_version": SCHEMA_VERSION,
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "original_filename": item.original_filename,
                "input_kind": item.input_kind,
                "sensitive": item.sensitive,
                "sha256": item.sha256,
                "created_at": item.created_at,
            }
            for item in items
        ],
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# --- store ----------------------------------------------------------------------


class CustomPoseStore:
    def __init__(self, root: Optional[Path], unavailable_reason: str = ""):
        self.root = Path(root).resolve() if root is not None else None
        self.unavailable_reason = unavailable_reason

    @property
    def index_path(self) -> Path:
        return self._root() / "index.json"

    @property
    def backup_path(self) -> Path:
        return self._root() / "index.json.bak"

    def _root(self) -> Path:
        if self.root is None:
            raise CustomPoseError(f"Custom Poses unavailable: {self.unavailable_reason}")
        return self.root

    # ------------------------------------------------------------ file locations

    def _located(self, relative: str) -> Optional[Path]:
        """The derived location, or ``None`` if a symlink redirects it."""
        expected = self._root().joinpath(*relative.split("/"))
        return expected if safe_join(self._root(), relative) == expected else None

    def image_path(self, item_id: str) -> Optional[Path]:
        return self._located(f"images/{item_id}.png")

    def thumbnail_path(self, item_id: str) -> Optional[Path]:
        return self._located(f"thumbs/{item_id}.webp")

    # ------------------------------------------------------------- index access

    def _load(self) -> list[CustomPose]:
        """Current items; the caller holds the lock. Raises if unusable."""
        try:
            data = _retry(self.index_path.read_bytes)
        except FileNotFoundError:
            if self.backup_path.exists():
                raise CustomPoseError(
                    "The Custom Poses index is missing but a backup exists, so the library is read-only. "
                    f"Restore {self.backup_path.name} to index.json."
                ) from None
            return []
        except OSError as error:
            raise CustomPoseError(f"The Custom Poses index could not be read ({type(error).__name__}).") from error
        return _parse_index(data)

    def _commit(self, items: list[CustomPose]) -> None:
        """Atomically replace the index; the caller holds the lock and has just
        run ``_load`` successfully, so the file being backed up is known good."""
        global _revision
        temporary = self.index_path.with_name("index.json.tmp")
        backup_temporary = self.index_path.with_name("index.json.bak.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(_serialize_index(items))
                handle.flush()
                os.fsync(handle.fileno())
            if self.index_path.exists():
                backup_temporary.write_bytes(_retry(self.index_path.read_bytes))
                _retry(lambda: os.replace(backup_temporary, self.backup_path))
            _retry(lambda: os.replace(temporary, self.index_path))
        except OSError as error:
            _remove_quietly(temporary)
            _remove_quietly(backup_temporary)
            raise CustomPoseError(f"The Custom Poses index could not be saved ({type(error).__name__}).") from error
        _revision += 1

    def status(self) -> StoreStatus:
        if self.root is None:
            return StoreStatus(STATUS_UNAVAILABLE, f"Custom Poses unavailable: {self.unavailable_reason}")
        try:
            with _LOCK:
                count = len(self._load())
        except CustomPoseError as error:
            return StoreStatus(STATUS_READ_ONLY, str(error))
        return StoreStatus(STATUS_OK, f"{count:,} custom poses", count)

    # --------------------------------------------------------------------- reads

    def _snapshot(self) -> list[CustomPose]:
        if self.root is None:
            return []
        try:
            with _LOCK:
                return self._load()
        except CustomPoseError:
            return []  # status() explains why

    def items(self, query: str = "", safe_mode: bool = False) -> list[CustomPose]:
        """Matching poses, newest first. Empty if the library is unusable."""
        needle = (query or "").strip().casefold()
        matches = [
            item
            for item in reversed(self._snapshot())
            if not (safe_mode and item.sensitive) and (not needle or needle in f"{item.name} {item.id}".casefold())
        ]
        return sorted(matches, key=lambda item: item.created_at, reverse=True)

    def get(self, item_id: object) -> Optional[CustomPose]:
        return next((item for item in self._snapshot() if item.id == item_id), None)

    def orphans(self) -> list[str]:
        """Library files no index record claims. Only reported, never deleted."""
        if self.root is None:
            return []
        try:
            with _LOCK:
                known = self._load()
        except CustomPoseError:
            return []  # without a trustworthy index, nothing can be called an orphan
        claimed = {f"images/{item.id}.png" for item in known} | {f"thumbs/{item.id}.webp" for item in known}
        found: list[str] = []
        for folder in ("images", "thumbs"):
            directory = self.root / folder
            if directory.is_dir():
                found += [f"{folder}/{entry.name}" for entry in sorted(directory.iterdir())]
        return [name for name in found if name not in claimed]

    def _render(self, item_id: object, safe_mode: bool) -> tuple[CustomPose, Image.Image]:
        item = self.get(item_id)
        if item is None:
            problem = self.status()
            raise CustomPoseError(ERROR_NO_ITEM if problem.state == STATUS_OK else problem.message)
        if safe_mode and item.sensitive:
            raise CustomPoseError(ERROR_HIDDEN)
        path = self.image_path(item.id)
        try:
            if path is None:
                raise OSError("redirected path")
            with Image.open(path, formats=("PNG",)) as image:
                if image.width * image.height > MAX_DECODE_PIXELS or image.mode not in {"RGB", "RGBA"}:
                    raise OSError("unexpected stored image")
                image.load()
                return item, _flatten(image, item.input_kind)
        except FileNotFoundError as error:
            raise CustomPoseError(f"Missing reference image for {item.name}.") from error
        except Exception as error:  # a damaged file must only fail this one send
            raise CustomPoseError(f"The stored image for {item.name} could not be read.") from error

    def preview_image(self, item_id: object, *, safe_mode: bool) -> Image.Image:
        """The picture that would be sent, for the selected-pose preview."""
        return self._render(item_id, safe_mode)[1]

    def load_control_image(self, item_id: object, *, safe_mode: bool) -> ControlImage:
        """Re-reads ID, metadata and file so a stale selection cannot be sent."""
        item, image = self._render(item_id, safe_mode)
        return ControlImage(item, np.asarray(image), item.preprocessor)

    # -------------------------------------------------------------------- writes

    def _stage(self, item_id: str, image: Image.Image) -> tuple[Path, Path]:
        """Write and verify temporary files next to their final locations."""
        root = self._root()
        image_temporary = root / "images" / f".{item_id}.png.tmp"
        thumb_temporary = root / "thumbs" / f".{item_id}.webp.tmp"
        try:
            (root / "images").mkdir(exist_ok=True)
            (root / "thumbs").mkdir(exist_ok=True)
            image.info.clear()  # drops EXIF, ICC and text metadata
            image.save(image_temporary, "PNG")
            thumbnail = image.copy()
            thumbnail.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
            thumbnail.save(thumb_temporary, "WEBP", quality=85)
            for temporary, expected in ((image_temporary, "PNG"), (thumb_temporary, "WEBP")):
                with Image.open(temporary, formats=(expected,)) as written:
                    written.verify()
        except Exception as error:
            _remove_quietly(image_temporary)
            _remove_quietly(thumb_temporary)
            raise CustomPoseError(f"The image could not be saved ({type(error).__name__}).") from error
        return image_temporary, thumb_temporary

    def _final_paths(self, item_id: str) -> tuple[Path, Path]:
        image_path, thumb_path = self.image_path(item_id), self.thumbnail_path(item_id)
        if image_path is None or thumb_path is None:
            raise CustomPoseError("The Custom Poses folder contains a redirected path.")
        return image_path, thumb_path

    def _install(self, item_id: str, staged: tuple[Path, Path]) -> tuple[Path, Path]:
        image_path, thumb_path = self._final_paths(item_id)
        try:
            _retry(lambda: os.replace(staged[0], image_path))
            _retry(lambda: os.replace(staged[1], thumb_path))
        except OSError as error:
            _remove_quietly(image_path)
            raise CustomPoseError(f"The image could not be saved ({type(error).__name__}).") from error
        return image_path, thumb_path

    def add(
        self,
        upload: object,
        *,
        name: str = "",
        input_kind: str = KIND_REFERENCE_CANNY,
        sensitive: bool = False,
        original_filename: Optional[str] = None,
    ) -> AddResult:
        """Validate and store one uploaded file.

        A file already in the library (same bytes) is not stored again; the
        result then has ``duplicate=True`` and the existing item.
        """
        self._root()
        if input_kind not in INPUT_KINDS:
            raise CustomPoseError(f"Unknown input type: {input_kind}.")

        # Hashing and decoding stay outside the lock so one big upload cannot
        # stall every other session.
        data = _read_upload(upload)
        digest = hashlib.sha256(data).hexdigest()
        with _LOCK:
            existing = next((item for item in self._load() if item.sha256 == digest), None)
        if existing is not None:
            return AddResult(existing, duplicate=True)

        canonical, resized = _decode_upload(data)
        filename = _clean_filename(original_filename if original_filename is not None else upload)
        item = CustomPose(
            id=uuid.uuid4().hex,
            name=_clean_text(name, MAX_NAME_LENGTH) or _default_name(filename),
            original_filename=filename,
            input_kind=input_kind,
            sensitive=bool(sensitive),
            sha256=digest,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._final_paths(item.id)  # never write through a redirected folder
        staged = self._stage(item.id, canonical)
        try:
            with _LOCK:
                items = self._load()
                existing = next((known for known in items if known.sha256 == digest), None)
                if existing is not None:  # another session won the race
                    return AddResult(existing, duplicate=True)
                installed = self._install(item.id, staged)
                try:
                    self._commit([*items, item])
                except CustomPoseError:
                    for path in installed:
                        _remove_quietly(path)
                    raise
        finally:
            for temporary in staged:
                _remove_quietly(temporary)
        return AddResult(item, resized=resized)

    def update(
        self,
        item_id: object,
        *,
        name: Optional[str] = None,
        input_kind: Optional[str] = None,
        sensitive: Optional[bool] = None,
    ) -> CustomPose:
        """Change editable details; ID, files, hash and date never change."""
        self._root()
        changes: dict = {}
        if name is not None:
            changes["name"] = _clean_text(name, MAX_NAME_LENGTH)
            if not changes["name"]:
                raise CustomPoseError("The name cannot be empty.")
        if input_kind is not None:
            if input_kind not in INPUT_KINDS:
                raise CustomPoseError(f"Unknown input type: {input_kind}.")
            changes["input_kind"] = input_kind
        if sensitive is not None:
            changes["sensitive"] = bool(sensitive)
        with _LOCK:
            items = self._load()
            position = next((i for i, item in enumerate(items) if item.id == item_id), None)
            if position is None:
                raise CustomPoseError(ERROR_NO_ITEM)
            updated = replace(items[position], **changes)
            if updated != items[position]:
                items[position] = updated
                self._commit(items)
        return updated

    def delete(self, item_id: object) -> DeleteResult:
        """Remove the record first, then its two files.

        A crash or a locked file can only leave an orphan file, never a record
        pointing at nothing. Paths always come from the stored ID.
        """
        self._root()
        with _LOCK:
            items = self._load()
            item = next((known for known in items if known.id == item_id), None)
            if item is None:
                raise CustomPoseError(ERROR_NO_ITEM)
            self._commit([known for known in items if known.id != item.id])
        orphans = []
        for relative, path in (
            (f"images/{item.id}.png", self.image_path(item.id)),
            (f"thumbs/{item.id}.webp", self.thumbnail_path(item.id)),
        ):
            if not _remove_quietly(path):
                orphans.append(relative)
        if orphans:
            logger.warning("Custom Poses: could not delete %s", ", ".join(orphans))
        return DeleteResult(item, tuple(orphans))


# --- opening ----------------------------------------------------------------------


def find_root(
    environ: Optional[Mapping[str, str]] = None,
    data_path: Optional[Callable[[], Optional[str]]] = None,
) -> tuple[Optional[Path], str]:
    """``(root, "")`` or ``(None, reason)``.

    The environment variable wins and ``data_path`` (the caller's way to ask
    Forge for its data folder) is then not called. There is deliberately no
    guess from the extension's own location: a wrong guess would silently start
    a second library.
    """
    environ = os.environ if environ is None else environ
    configured = (environ.get(ENV_ROOT) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            return None, f"{ENV_ROOT} must be an absolute path."
        return path, ""
    base = data_path() if data_path is not None else None
    if not base:
        return None, f"Forge's data folder could not be determined. Set {ENV_ROOT} to choose a folder."
    return Path(base) / "data" / "forge-character-builder" / "custom-poses", ""


def open_store(
    environ: Optional[Mapping[str, str]] = None,
    data_path: Optional[Callable[[], Optional[str]]] = None,
) -> CustomPoseStore:
    """Open the library. Never raises: problems become an unavailable store,
    so the rest of the extension and Forge itself keep working."""
    root, reason = find_root(environ, data_path)
    if root is None:
        return CustomPoseStore(None, reason)
    try:
        for folder in (root / "images", root / "thumbs"):
            folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root):
            pass
    except OSError as error:
        return CustomPoseStore(None, f"{root} is not writable ({type(error).__name__}).")
    store = CustomPoseStore(root)
    orphans = store.orphans()
    if orphans:
        logger.warning("Custom Poses: %d file(s) in %s have no index record: %s", len(orphans), root, ", ".join(orphans[:20]))
    return store
