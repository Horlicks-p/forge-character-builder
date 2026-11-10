"""Data source health for the UI: what is installed, what is missing and how
to fix it. Pure Python; every probe is cheap (file checks, cached loads) and
never raises, so a broken source only degrades its own part of the UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import anhao, custom_poses
from .charadb_adapter import CharaDBCatalog

OK = "ok"
PARTIAL = "partial"
MISSING = "missing"
ERROR = "error"

_ICONS = {OK: "✅", PARTIAL: "⚠️", MISSING: "⛔", ERROR: "❌"}

CHARADB_HINT = (
    "Extract the CharaDB release zip into `extensions/forge-character-builder/vendor/charadb/` "
    "(it needs `data.js` or `items_public.json`; `thumb/` adds previews), "
    "or set the `CHARADB_ROOT` environment variable to that folder."
)
CHARADB_PREVIEW_HINT = "Add the release's `thumb/` folder next to the index to show previews."
ANHAO_HINT = (
    "Copy the ai-pose-gallery-zh dataset into `extensions/forge-character-builder/vendor/` "
    "so that `vendor/data/pose-zh.json` exists, put it next to this extension as "
    "`extensions/ai-pose-gallery-zh/`, or set the `ANHAO_GALLERY_ROOT` environment variable."
)
ANHAO_IMAGES_HINT = "The dataset's `img/` and `thumbs/` folders are needed for thumbnails and ControlNet."


@dataclass(frozen=True)
class SourceHealth:
    name: str
    state: str
    detail: str
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK


@dataclass(frozen=True)
class LibraryProbe:
    count: int
    error: str = ""


def probe_library(root: Optional[Path], label: str) -> LibraryProbe:
    """Uncached check of one Anhao library file that says *why* it is empty.

    ``anhao.load_library`` keeps returning ``()`` on any problem; this sibling
    exists only so the status panel can tell missing, unreadable and empty apart.
    """
    if root is None:
        return LibraryProbe(0, "dataset not found")
    path = Path(root) / "data" / anhao.LIBRARIES[label]["file"]
    if not path.is_file():
        return LibraryProbe(0, f"`data/{path.name}` is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return LibraryProbe(0, f"`data/{path.name}` could not be read ({type(error).__name__})")
    if not isinstance(data, list):
        return LibraryProbe(0, f"`data/{path.name}` is not a list")
    return LibraryProbe(sum(1 for item in data if isinstance(item, dict)))


def charadb_health(catalog: CharaDBCatalog) -> SourceHealth:
    if catalog.available:
        if catalog.image_root:
            return SourceHealth("CharaDB", OK, catalog.message)
        return SourceHealth("CharaDB", PARTIAL, catalog.message, CHARADB_PREVIEW_HINT)
    if catalog.path is None:
        return SourceHealth("CharaDB", MISSING, "CharaDB not installed — hair, eye and outfit options are unavailable.", CHARADB_HINT)
    return SourceHealth("CharaDB", ERROR, catalog.message, CHARADB_HINT)


ANHAO_DATA_HINT = "Copy the dataset's `data/*.json` files again; an empty library hides that part of the UI."


def _library_label(root: Path, label: str, count: int) -> str:
    """``"1,439 pose images (288 poses)"`` when views are grouped, else ``"4 poses"``."""
    if label == "Poses" and count:
        groups = anhao.pose_group_count(root)
        if groups and groups != count:
            return f"{count:,} pose images ({groups:,} poses)"
    return f"{count:,} {label.lower()}"


def anhao_health(
    root: Optional[Path],
    *,
    libraries: Iterable[str] = ("Poses",),
    candidate: Optional[Path] = None,
) -> SourceHealth:
    """Health of the Anhao dataset.

    ``root`` is the usable root (``anhao.find_gallery_root``); ``candidate``
    is a folder that looks like an install attempt but was rejected (e.g. it
    lacks ``data/pose-zh.json``), so the panel can name the missing file.
    """
    probe_root = root if root is not None else candidate
    if probe_root is None:
        return SourceHealth("Anhao", MISSING, "Anhao not installed — Anhao poses are unavailable.", ANHAO_HINT)

    probes = {label: probe_library(probe_root, label) for label in libraries}
    problems = [f"{label}: {probe.error}" for label, probe in probes.items() if probe.error]
    readable = {label: probe.count for label, probe in probes.items() if not probe.error}
    empty = [label for label, count in readable.items() if count == 0]
    detail = "Anhao: " + (", ".join(_library_label(probe_root, label, count) for label, count in readable.items()) or "no usable libraries")

    if root is None:
        reason = "; ".join(problems) or "`data/pose-zh.json` is missing"
        return SourceHealth("Anhao", ERROR, f"Anhao folder `{candidate}` is not usable · {reason}", ANHAO_HINT)
    if problems:
        return SourceHealth("Anhao", ERROR, detail + " · " + "; ".join(problems), ANHAO_HINT)
    if readable and len(empty) == len(readable):
        return SourceHealth("Anhao", ERROR, detail + " · every library is empty", ANHAO_DATA_HINT)
    if empty:
        names = ", ".join(label.lower() for label in empty)
        return SourceHealth("Anhao", PARTIAL, f"{detail} · empty: {names}", ANHAO_DATA_HINT)
    missing_dirs = [name for name in ("img", "thumbs") if not (Path(root) / name).is_dir()]
    if missing_dirs:
        folders = ", ".join(f"`{name}/`" for name in missing_dirs)
        return SourceHealth("Anhao", PARTIAL, f"{detail} · missing {folders}", ANHAO_IMAGES_HINT)
    return SourceHealth("Anhao", OK, detail)


CUSTOM_POSES_HINT = f"Set the `{custom_poses.ENV_ROOT}` environment variable to a writable folder."


def custom_pose_health(store: custom_poses.CustomPoseStore) -> SourceHealth:
    status = store.status()
    if status.state == custom_poses.STATUS_OK:
        return SourceHealth("Custom Poses", OK, f"Custom Poses: {status.count:,} saved · `{store.root}`")
    if status.state == custom_poses.STATUS_UNAVAILABLE:
        return SourceHealth("Custom Poses", MISSING, status.message, CUSTOM_POSES_HINT)
    return SourceHealth("Custom Poses", ERROR, status.message)


def render_markdown(rows: Iterable[SourceHealth]) -> str:
    lines = []
    for row in rows:
        lines.append(f"{_ICONS.get(row.state, '•')} {row.detail}")
        if row.hint and not row.ok:
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {row.hint}")
    return "  \n".join(lines)


def all_ok(rows: Iterable[SourceHealth]) -> bool:
    return all(row.ok for row in rows)
