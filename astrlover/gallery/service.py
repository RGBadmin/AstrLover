"""图库服务（R4/R5/A7/A9/A10）：语义检索、降级生图、表情包、头像挑选。

R5 的关键：进来的 query 已经是"情境需求描述"（下一幕的画面），
不是用户话语的字面——那一步由主模型在 <photo> 标记里完成。
"""

import random
import time

from astrbot.api import logger

from .ingest import GalleryIngest

_PROVIDE_THRESHOLD = 0.60   # 达标即用图库
_FALLBACK_THRESHOLD = 0.50  # 生图失败后的将就线


class Gallery:
    def __init__(self, app):
        self.app = app
        self.ingest = GalleryIngest(app)

    # ------------------------------------------------------------------
    # 语义检索
    # ------------------------------------------------------------------
    async def search(self, query: str, category: str | None = None, k: int = 5) -> list[dict]:
        """返回 [{row, score}]，按一致性修正后的分数排序。"""
        app = self.app
        filters = {"category": category} if category else None
        hits = await app.vectors.search_gallery(query, k=k * 2, filters=filters)
        out = []
        cur_hair = str(app.dynamic.appearance_state.get("hair") or "")
        for h in hits:
            gid = h["meta"].get("gallery_id")
            if not gid:
                continue
            row = await app.dao.get_image(int(gid))
            if row is None or row["status"] != "ok":
                continue
            if not (app.data_dir / row["file"]).exists():
                continue
            score = h["similarity"]
            # A9 外观一致性：发型演变后，旧发型的人物照降权
            row_hair = str((row.get("appearance") or {}).get("hair") or "")
            if cur_hair and row_hair and row["category"] in ("selfie", "life") and cur_hair not in row_hair:
                score -= 0.12
            # 近期用过的图轻微降权，避免翻来覆去发同一张
            if row["last_used_ts"] and time.time() - row["last_used_ts"] < 48 * 3600:
                score -= 0.05
            out.append({"row": row, "score": score})
        out.sort(key=lambda x: -x["score"])
        return out[:k]

    # ------------------------------------------------------------------
    # R5：情境需求 → 图库 → 降级生图
    # ------------------------------------------------------------------
    async def provide(self, situation: str) -> str | None:
        app = self.app
        candidates = [
            c for c in await self.search(situation, k=4)
            if c["row"]["category"] != "sticker"
        ]
        best = candidates[0] if candidates else None
        if best and best["score"] >= _PROVIDE_THRESHOLD:
            await app.dao.mark_image_used(best["row"]["id"])
            return str(app.data_dir / best["row"]["file"])

        # 图库没有足够贴切的 → 降级生图（R6），并回流入库
        if app.imagegen and app.imagegen.available:
            path = await app.imagegen.generate(situation)
            if path:
                try:
                    await self.ingest.ingest_generated(path, situation)
                except Exception:
                    logger.warning("[AstrLover] 生成图回流入库失败", exc_info=True)
                return path

        if best and best["score"] >= _FALLBACK_THRESHOLD:
            await app.dao.mark_image_used(best["row"]["id"])
            return str(app.data_dir / best["row"]["file"])
        return None

    # ------------------------------------------------------------------
    # A7 表情包：按语境语义选用
    # ------------------------------------------------------------------
    async def pick_sticker(self, mood_desc: str) -> str | None:
        app = self.app
        candidates = await self.search(mood_desc, category="sticker", k=3)
        candidates = [c for c in candidates if c["score"] >= 0.5]
        if not candidates:
            return None
        # 得分接近时带点随机，别每次都同一张
        weights = [max(0.01, c["score"]) for c in candidates]
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        await app.dao.mark_image_used(chosen["row"]["id"])
        return str(app.data_dir / chosen["row"]["file"])

    # ------------------------------------------------------------------
    # 头像挑选（R2）
    # ------------------------------------------------------------------
    async def pick_for_avatar(self, query: str) -> tuple[str, str] | None:
        app = self.app
        last_file = await app.dao.kv_get("last_avatar_file", "")
        candidates = [
            c for c in await self.search(query, category="selfie", k=6)
            if c["row"]["file"] != last_file
        ]
        if not candidates:
            return None
        chosen = candidates[0]
        await app.dao.mark_image_used(chosen["row"]["id"])
        await app.dao.kv_set("last_avatar_file", chosen["row"]["file"])
        return (str(app.data_dir / chosen["row"]["file"]), chosen["row"]["desc"] or "一张自拍")
