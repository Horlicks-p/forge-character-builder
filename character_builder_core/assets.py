"""Local asset path helpers with root-containment checks (plan §7.2)."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Optional

_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DRIVE = re.compile(r"^[a-zA-Z]:")

PLACEHOLDER_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "no-preview.png"


def safe_join(root: Optional[Path], relative: object) -> Optional[Path]:
    """Join ``relative`` under ``root``; ``None`` if it would escape ``root``.

    Rejects URLs, absolute paths, drive letters and ``..`` segments, and
    re-checks containment after resolving symlinks.
    """
    if root is None:
        return None
    text = str(relative or "").replace("\\", "/").strip()
    if not text or _URL.match(text) or _DRIVE.match(text) or text.startswith("/"):
        return None
    parts = PurePosixPath(text).parts
    if any(part == ".." for part in parts):
        return None
    base = Path(root).resolve()
    candidate = base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def existing_file(root: Optional[Path], relative: object) -> Optional[Path]:
    path = safe_join(root, relative)
    return path if path is not None and path.is_file() else None
