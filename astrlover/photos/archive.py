"""聊天图片存档：两层描述 + 原图落盘 + 编号分配。

AstrBot 把图片以 base64 data URL 写进对话历史，之后每轮原样重发；
几十张之后既烧 token 又稀释注意力。但直接删又不行——他会说"昨天那张"。
所以把图换成文字留在时间线上，原图落盘随时可取回。

两层描述信息类型不同，不只是详略不同：
  目录层  她自己写的一句（她的视角与当时语境），跟正文一次生成，不额外调模型
  细节层  独立视觉模型写的客观画面（构图/材质/颜色/文字/光线），异步解析
"""

import base64
import binascii
import hashlib
import re
import time
from pathlib import Path

from astrbot.api import logger

DATA_URL_RE = re.compile(r"^data:image/(?P<ext>[\w+.-]+);base64,(?P<b64>.+)$", re.S)

# 她在回复末尾附的图片描述，发出去前会被剥掉
IMG_NOTE_RE = re.compile(
    r"<img_note\s+id=[\"']?#?(?P<id>\w+)[\"']?\s*>(?P<desc>.*?)</img_note>", re.S
)
# AstrBot 把当前时间写进 user 消息正文，这是历史里唯一可靠的时间锚点
CTX_TIME_RE = re.compile(r"Current datetime:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")

NOTES_PER_TURN = 5   # 一轮最多请求几条描述，多了模型容易敷衍或被截断
DETAIL_MAX_FAILS = 3


class PhotoArchive:
    """photo_archive 表 + context_photos/ 目录的封装。"""

    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.dir = app.data_dir / "context_photos"
        # base64 指纹 → sha，省掉每轮对全部图片重算哈希
        self._key_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    @staticmethod
    def parse_data_url(url: str):
        return DATA_URL_RE.match((url or "").strip())

    def sha_of(self, data_url: str) -> str | None:
        """内容哈希（稳定标识）。

        必须用 sha256：内置 hash() 每次进程启动都不同，重启后同图会被当新图。
        但上下文几十张图时每轮要过几十 MB base64，所以按"长度+首尾 32 字符"
        做指纹缓存——尾部是压缩数据末段，叠上长度撞不到一起。
        """
        hit = self.parse_data_url(data_url)
        if not hit:
            return None
        b64 = hit.group("b64")
        probe = f"{len(b64)}:{b64[:32]}:{b64[-32:]}"
        if (cached := self._key_cache.get(probe)) is not None:
            return cached
        sha = hashlib.sha256(b64.encode()).hexdigest()[:16]
        if len(self._key_cache) > 512:
            self._key_cache.clear()
        self._key_cache[probe] = sha
        return sha

    # ------------------------------------------------------------------
    async def register_data_url(self, data_url: str, seen_ts: int = 0) -> int | None:
        """登记并落盘，返回编号（即 photo_archive.id）。已存在则复用。"""
        sha = self.sha_of(data_url)
        if sha is None:
            return None
        row = await self.db.fetchone("SELECT id, file FROM photo_archive WHERE sha=?", (sha,))
        if row:
            if not (self.app.data_dir / row["file"]).exists():
                await self._write_file(data_url, row["id"])
            return int(row["id"])

        pid = await self.db.execute(
            "INSERT INTO photo_archive(sha, file, seen_ts) VALUES (?,?,?)",
            (sha, "", seen_ts or int(time.time())),
        )
        rel = await self._write_file(data_url, pid)
        if rel is None:
            await self.db.execute("DELETE FROM photo_archive WHERE id=?", (pid,))
            return None
        await self.db.execute("UPDATE photo_archive SET file=? WHERE id=?", (rel, pid))
        return int(pid)

    async def register_bytes(self, data: bytes, mime: str = "image/jpeg", seen_ts: int = 0) -> int | None:
        """静默期走消息文件那条路时用：读回 base64 再走同一个登记函数，
        保证同一张图在两条路径上算出同一个编号。"""
        b64 = base64.b64encode(data).decode()
        return await self.register_data_url(f"data:{mime};base64,{b64}", seen_ts)

    async def _write_file(self, data_url: str, pid: int) -> str | None:
        hit = self.parse_data_url(data_url)
        if not hit:
            return None
        ext = hit.group("ext").split("+")[0]
        if ext == "jpeg":
            ext = "jpg"
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{pid}.{ext}"
        try:
            path.write_bytes(base64.b64decode(hit.group("b64")))
        except (binascii.Error, ValueError, OSError) as e:
            logger.warning(f"[AstrLover] 图片存盘失败 #{pid}：{e}")
            return None
        return str(path.relative_to(self.app.data_dir)).replace("\\", "/")

    # ------------------------------------------------------------------
    async def get(self, pid: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM photo_archive WHERE id=?", (pid,))

    def abs_path(self, row: dict) -> Path | None:
        if not row or not row.get("file"):
            return None
        p = self.app.data_dir / row["file"]
        return p if p.exists() else None

    async def set_catalog(self, pid: int, text: str):
        await self.db.execute(
            "UPDATE photo_archive SET catalog=? WHERE id=?", (text[:200], pid)
        )

    async def set_detail(self, pid: int, text: str):
        await self.db.execute(
            "UPDATE photo_archive SET detail=?, detail_fail=0 WHERE id=?", (text, pid)
        )

    async def bump_detail_fail(self, pid: int):
        await self.db.execute(
            "UPDATE photo_archive SET detail_fail=detail_fail+1 WHERE id=?", (pid,)
        )

    async def missing_catalog(self, pids: list[int]) -> list[int]:
        if not pids:
            return []
        marks = ",".join("?" * len(pids))
        rows = await self.db.fetchall(
            f"SELECT id FROM photo_archive WHERE id IN ({marks}) AND catalog=''", tuple(pids)
        )
        order = {p: i for i, p in enumerate(pids)}
        return sorted((int(r["id"]) for r in rows), key=lambda x: order.get(x, 0))

    async def needs_detail(self, pids: list[int]) -> list[dict]:
        if not pids:
            return []
        marks = ",".join("?" * len(pids))
        return await self.db.fetchall(
            f"SELECT * FROM photo_archive WHERE id IN ({marks}) AND detail='' "
            f"AND detail_fail < ?",
            (*pids, DETAIL_MAX_FAILS),
        )

    async def search(self, keywords: str = "", day: str = "", limit: int = 8) -> list[dict]:
        """两层一起搜：目录层和细节层任意一层命中即可（取交集会漏）。"""
        words = [w for w in re.split(r"[\s，,、]+", keywords.strip()) if w]
        sql = "SELECT * FROM photo_archive WHERE 1=1"
        params: list = []
        for w in words:
            sql += " AND (catalog LIKE ? OR detail LIKE ?)"
            params += [f"%{w}%", f"%{w}%"]
        rows = await self.db.fetchall(sql + " ORDER BY id DESC LIMIT 200", tuple(params))
        if day:
            d = day.strip()
            def match(ts: int) -> bool:
                if not ts:
                    return False
                stamp = time.strftime("%Y-%m-%d", time.localtime(ts))
                return stamp == d if len(d) == 10 else stamp.endswith(d)
            rows = [r for r in rows if match(r["seen_ts"])]
        return rows[:limit]

    async def stats(self) -> dict:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN catalog != '' THEN 1 ELSE 0 END) AS c, "
            "SUM(CASE WHEN detail != '' THEN 1 ELSE 0 END) AS d FROM photo_archive"
        )
        return {"total": int(row["n"] or 0), "catalog": int(row["c"] or 0), "detail": int(row["d"] or 0)}


def harvest_notes(text: str) -> tuple[str, dict[int, str]]:
    """摘出 <img_note> 并从文本里剥掉。返回 (清理后文本, {编号: 描述})。"""
    notes: dict[int, str] = {}
    for hit in IMG_NOTE_RE.finditer(text or ""):
        body = hit.group("desc").strip()
        try:
            pid = int(str(hit.group("id")).lstrip("#"))
        except ValueError:
            continue
        if body:
            notes[pid] = body[:200]
    return (IMG_NOTE_RE.sub("", text or "").strip(), notes)


def context_time(msg: dict) -> int:
    """从消息正文里的 Current datetime 锚点还原时间。"""
    content = msg.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts += [
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
    for t in texts:
        if m := CTX_TIME_RE.search(t or ""):
            try:
                return int(time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M")))
            except ValueError:
                continue
    return 0


def msg_own_text(msg: dict) -> str:
    """取消息自己的正文，排除注入的 system_reminder 与已有占位。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    bits = []
    for part in content:
        if not (isinstance(part, dict) and part.get("type") == "text"):
            continue
        t = (part.get("text") or "").strip()
        if not t or "<system_reminder>" in t or t.startswith("[图片 #"):
            continue
        bits.append(t)
    return " ".join(bits)
