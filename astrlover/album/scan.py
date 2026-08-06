"""相册扫描：登记文件、.archive 分类、从推特文件名还原真实时间。"""

import re
import time
from pathlib import Path

from astrbot.api import logger

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ARCHIVE_EXT = ".archive"   # 博主目录里放一个此后缀文件，那一级目录名即分类名
_TW_EPOCH_MS = 1288834974657
# 推文 ID（snowflake）17~20 位数字，允许 -1 -2 媒体序号后缀
_SNOWFLAKE_RE = re.compile(r"^(\d{17,20})(?:-\d+)?$")
_UID_PREFIX_RE = re.compile(r"^\d+@")


def shot_time(path: Path, use_snowflake: bool = True) -> int:
    """推特下载的文件名就是推文 ID，高 42 位是毫秒时间戳——比 mtime 准得多
    （mtime 是下载时间，一次批量下载会让几千张图挤在同一天）。"""
    if use_snowflake:
        m = _SNOWFLAKE_RE.match(path.stem)
        if m:
            try:
                ms = (int(m.group(1)) >> 22) + _TW_EPOCH_MS
                ts = ms // 1000
                if 1288834974 < ts < time.time() + 86400:
                    return int(ts)
            except (ValueError, OverflowError):
                pass
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def folder_name(file: Path, root: Path, archive_dirs: dict[Path, str]) -> str:
    """向上找最近的 .archive 标记目录，返回其分类名（去掉 用户ID@ 前缀）。"""
    for parent in file.parents:
        if parent == root.parent:
            break
        if parent in archive_dirs:
            return archive_dirs[parent]
    return ""


class AlbumScanner:
    def __init__(self, store, album_dir_getter):
        self.store = store
        self._dir = album_dir_getter  # callable -> str（可热改配置）

    def root(self) -> Path | None:
        raw = str(self._dir() or "").strip()
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_dir() else None

    async def scan(self, prune: bool = False, use_snowflake: bool = True) -> dict:
        """扫目录登记新文件。幂等；prune=True 顺带删掉磁盘上已不存在的记录。"""
        root = self.root()
        if root is None:
            return {"error": "相册目录未配置或不存在"}

        archive_dirs: dict[Path, str] = {}
        for marker in root.rglob(f"*{ARCHIVE_EXT}"):
            name = _UID_PREFIX_RE.sub("", marker.parent.name)
            archive_dirs[marker.parent] = name

        added, total = 0, 0
        seen: set[str] = set()
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in PHOTO_EXTS:
                continue
            total += 1
            rel = str(p.relative_to(root)).replace("\\", "/")
            seen.add(rel)
            if await self.store.register(rel, folder_name(p, root, archive_dirs), shot_time(p, use_snowflake)):
                added += 1

        pruned = 0
        if prune:
            pruned = await self.store.prune_missing(seen)
        logger.info(f"[AstrLover] 相册扫描：磁盘 {total} 张，新增 {added}，清理 {pruned}")
        return {"total": total, "added": added, "pruned": pruned,
                "folders": sorted(set(archive_dirs.values()))}

    def abs_path(self, rel: str) -> Path | None:
        root = self.root()
        if root is None:
            return None
        p = (root / rel).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            return None
        return p if p.exists() else None
