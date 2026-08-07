"""相册存储 DAO：album_images 表的唯一 SQL 入口（单库 astrlover.db）。"""

import time

from ..store.db import Database


class AlbumStore:
    def __init__(self, db: Database):
        self.db = db

    async def register(self, path: str, folder: str, shot_ts: int) -> bool:
        """登记新文件；已存在返回 False。幂等，可反复扫。"""
        row = await self.db.fetchone("SELECT id FROM album_images WHERE path=?", (path,))
        if row:
            return False
        await self.db.execute(
            "INSERT INTO album_images(path, folder, shot_ts, created_ts) VALUES (?,?,?,?)",
            (path, folder, shot_ts, int(time.time())),
        )
        return True

    async def prune_missing(self, existing_paths: set[str]) -> int:
        rows = await self.db.fetchall("SELECT id, path FROM album_images")
        gone = [r["id"] for r in rows if r["path"] not in existing_paths]
        for iid in gone:
            await self.db.execute("DELETE FROM album_images WHERE id=?", (iid,))
        return len(gone)

    async def reset_all(self):
        await self.db.execute("DELETE FROM album_images")

    # ---- 索引流转 ----
    async def next_pending(self, max_fails: int, limit: int = 8) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM album_images WHERE status='pending' AND fails < ? "
            "ORDER BY id LIMIT ?",
            (max_fails, limit),
        )

    async def mark_ok(self, image_id: int, desc: str, rating: str, season: str):
        await self.db.execute(
            "UPDATE album_images SET status='ok', desc=?, rating=?, season=?, "
            "fails=0, embedded=0 WHERE id=?",
            (desc, rating, season, image_id),
        )

    async def mark_fail(self, image_id: int, own_fault: bool):
        """own_fault=True 才累计失败次数——上游的锅不该让图片背。"""
        if own_fault:
            await self.db.execute(
                "UPDATE album_images SET fails=fails+1 WHERE id=?", (image_id,)
            )

    async def reset_fails(self) -> int:
        return await self.db.execute("UPDATE album_images SET fails=0 WHERE status='pending'")

    async def requeue(self, image_id: int):
        await self.db.execute(
            "UPDATE album_images SET status='pending', embedded=0 WHERE id=?", (image_id,)
        )

    async def update_desc(self, image_id: int, desc: str, rating: str, season: str):
        """polish 用：改描述与标签，向量标脏重建。"""
        await self.db.execute(
            "UPDATE album_images SET desc=?, rating=?, season=?, embedded=0 WHERE id=?",
            (desc, rating, season, image_id),
        )

    # ---- 向量流转 ----
    async def next_unembedded(self, limit: int = 32) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM album_images WHERE status='ok' AND embedded=0 ORDER BY id LIMIT ?",
            (limit,),
        )

    async def mark_embedded(self, image_id: int):
        await self.db.execute("UPDATE album_images SET embedded=1 WHERE id=?", (image_id,))

    async def clear_embedded(self) -> int:
        return await self.db.execute("UPDATE album_images SET embedded=0 WHERE status='ok'")

    # ---- 查询 ----
    async def get(self, image_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM album_images WHERE id=?", (image_id,))

    async def by_ids(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = await self.db.fetchall(
            f"SELECT * FROM album_images WHERE id IN ({marks})", tuple(ids)
        )
        return {r["id"]: r for r in rows}

    async def all_ok(self) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM album_images WHERE status='ok'")

    async def random_ok(self) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM album_images WHERE status='ok' ORDER BY RANDOM() LIMIT 1"
        )

    async def folders(self) -> list[str]:
        rows = await self.db.fetchall(
            "SELECT DISTINCT folder FROM album_images WHERE folder != '' ORDER BY folder"
        )
        return [r["folder"] for r in rows]

    async def mark_sent(self, image_id: int):
        await self.db.execute(
            "UPDATE album_images SET sent_count=sent_count+1, last_sent_ts=? WHERE id=?",
            (int(time.time()), image_id),
        )


    async def ids_like(self, word: str) -> set[int]:
        rows = await self.db.fetchall(
            "SELECT id FROM album_images WHERE status='ok' AND desc LIKE ?",
            (f"%{word}%",),
        )
        return {r["id"] for r in rows}

    async def stats(self) -> dict:
        out: dict = {}
        for row in await self.db.fetchall(
            "SELECT status, COUNT(*) AS n FROM album_images GROUP BY status"
        ):
            out[row["status"]] = row["n"]
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM album_images WHERE status='ok' AND embedded=1"
        )
        out["embedded"] = int(row["n"]) if row else 0
        for row in await self.db.fetchall(
            "SELECT rating, COUNT(*) AS n FROM album_images "
            "WHERE status='ok' AND rating != '' GROUP BY rating ORDER BY n DESC"
        ):
            out.setdefault("ratings", {})[row["rating"]] = row["n"]
        return out
