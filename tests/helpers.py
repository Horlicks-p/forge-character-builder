"""Build tiny self-made datasets in a temp dir (no third-party data or images)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
CHARADB_FIXTURE = FIXTURES / "charadb_sample.json"

POSES = [
    {"id": 1, "体系": "全身单人・标准系", "分类": "站姿", "角度": "正面", "原图": "img/pose/a.jpg", "缩略图": "thumbs/pose/a.webp"},
    {"id": 2, "体系": "全身单人・性感系", "分类": "坐姿", "角度": "侧面", "原图": "img/pose/b.jpg", "缩略图": "thumbs/pose/b.webp"},
    {"id": 3, "体系": "全身单人・标准系", "分类": "蹲姿", "角度": "俯视", "原图": "img/pose/missing.jpg", "缩略图": "thumbs/pose/missing.webp"},
    {"id": 4, "体系": "全身单人・标准系", "分类": "站姿", "角度": "正面", "原图": "../../outside.jpg", "缩略图": "../outside.webp"},
]
OUTFITS = [
    {"id": 10, "分类": "春季穿搭", "关键词": "white blouse pleated mini skirt", "原图": "img/costume/c.jpg", "缩略图": "thumbs/costume/c.webp"},
    {"id": 11, "分类": "性感穿搭", "关键词": "black camisole", "原图": "img/costume/d.jpg", "缩略图": "thumbs/costume/d.webp"},
]
PORTRAITS = [{"id": 20, "分区": "正脸参考", "发长": "长发", "原图": "img/portrait/e.jpg", "缩略图": "thumbs/portrait/e.webp"}]

EXISTING_FILES = [
    "img/pose/a.jpg",
    "thumbs/pose/a.webp",
    "img/depth/pose/a.webp",
    "thumbs/depth/pose/a.webp",
    "img/pose/b.jpg",
    "thumbs/pose/b.webp",
    "img/costume/c.jpg",
    "thumbs/costume/c.webp",
]


def make_anhao(root: Path) -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    for name, items in (("pose-zh.json", POSES), ("costume-zh.json", OUTFITS), ("portrait-zh.json", PORTRAITS)):
        (data / name).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    for relative in EXISTING_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-really-an-image")
    (root.parent / "outside.jpg").write_bytes(b"outside the dataset root")
    return root


def make_charadb_images(root: Path, ids: list[str]) -> Path:
    (root / "thumb").mkdir(parents=True)
    for item_id in ids:
        (root / "thumb" / f"{item_id}.webp").write_bytes(b"thumb")
    return root
