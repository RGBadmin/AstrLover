"""图库入库与结构化打标（R4）。

打标维度与检索维度对齐：入库怎么描述，检索就怎么问——
两边共用"场景/人物状态/情绪/着装/构图"这一套语言。
打标由 VLM（默认主模型读图）完成，心跳每 tick 消化少量 pending，
面板可触发全量打标。
"""

import asyncio
from pathlib import Path

from astrbot.api import logger

_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

_TAG_SYSTEM = (
    "你是图片整理助手。为一张图片输出结构化打标 JSON："
    '{"category": "selfie|life|scene|sticker", "desc": "一句连贯的中文描述，包含场景、人物状态、'
    '情绪氛围、着装、构图，用于将来按语义检索这张图", "scene": "场景", "person_state": "人物在做什么/状态，'
    '无人物则空", "emotion": "情绪氛围", "outfit": "着装，无人物则空", "composition": "构图（自拍视角/半身/全身/俯拍等）", '
    '"hair": "人物发型发色简述，无人物则空"}。\n'
    "category 判定：自拍或以同一女生为主体的照片=selfie；有她出现的生活照=life；"
    "无人物的风景/物件/食物=scene；表情包/梗图/夸张表情/带梗文字的图=sticker。"
    "sticker 的 desc 要写它表达的情绪和梗（例如「笑到打鸣的夸张大笑，适合回应好笑的事」）。"
    "只输出 JSON。"
)


class GalleryIngest:
    def __init__(self, app):
        self.app = app
        self._tagging = False

    # ------------------------------------------------------------------
    # 入库
    # ------------------------------------------------------------------
    async def scan_dir(self) -> int:
        """扫描 gallery 文件目录，把新文件登记为 pending。"""
        app = self.app
        known = {
            row["file"]
            for row in await app.dao.list_images(limit=100000)
        }
        added = 0
        for p in sorted(app.gallery_dir.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _EXTS:
                continue
            rel = str(p.relative_to(app.data_dir)).replace("\\", "/")
            if rel in known:
                continue
            await app.dao.add_image(rel, category="life", source="user", status="pending")
            added += 1
        if added:
            logger.info(f"[AstrLover] 图库扫描：新增 {added} 张待打标。")
        return added

    async def ingest_generated(self, path: str, situation: str) -> int:
        """生成图回流入库（R6）：情境描述直接作为打标。"""
        app = self.app
        rel = str(Path(path).relative_to(app.data_dir)).replace("\\", "/")
        image_id = await app.dao.add_image(rel, category="selfie", source="gen", status="pending")
        vec_id = await app.vectors.add_gallery(
            situation, {"gallery_id": image_id, "category": "selfie"}
        )
        await app.dao.tag_image(
            image_id, "selfie", situation,
            tags={"generated": True}, appearance=dict(app.dynamic.appearance_state), vec_id=vec_id,
        )
        return image_id

    # ------------------------------------------------------------------
    # 打标
    # ------------------------------------------------------------------
    async def tag_pending(self, limit: int = 2) -> int:
        """消化 pending：每次少量，摊平成本；返回本次处理数。"""
        if self._tagging:
            return 0
        self._tagging = True
        try:
            rows = await self.app.dao.list_images(status="pending", limit=limit)
            done = 0
            for row in rows:
                if await self.tag_one(row):
                    done += 1
                await asyncio.sleep(0.5)
            return done
        finally:
            self._tagging = False

    async def tag_all(self, progress_cb=None) -> int:
        """全量打标（面板触发）。"""
        total = 0
        while True:
            rows = await self.app.dao.list_images(status="pending", limit=8)
            if not rows:
                break
            for row in rows:
                await self.tag_one(row)
                total += 1
                if progress_cb:
                    await progress_cb(total)
        return total

    async def tag_one(self, row: dict) -> bool:
        app = self.app
        path = app.data_dir / row["file"]
        if not path.exists():
            await app.dao.set_image_status(row["id"], "failed")
            return False
        try:
            raw = await app.llm.vlm(_TAG_SYSTEM, str(path))
            tags = app.llm.extract_json(raw)
            if not isinstance(tags, dict) or not tags.get("desc"):
                raise RuntimeError(f"打标输出无效：{str(raw)[:120]}")
            category = str(tags.get("category") or "life")
            if category not in ("selfie", "life", "scene", "sticker"):
                category = "life"
            desc = str(tags.get("desc"))
            embed_text = "。".join(
                x for x in [
                    desc,
                    f"场景：{tags.get('scene', '')}",
                    f"人物状态：{tags.get('person_state', '')}",
                    f"情绪：{tags.get('emotion', '')}",
                    f"着装：{tags.get('outfit', '')}",
                    f"构图：{tags.get('composition', '')}",
                ] if x.split("：", 1)[-1]
            )
            vec_id = await app.vectors.add_gallery(
                embed_text, {"gallery_id": row["id"], "category": category}
            )
            appearance = {"hair": str(tags.get("hair", ""))} if tags.get("hair") else {}
            await app.dao.tag_image(row["id"], category, desc, tags, appearance, vec_id)
            return True
        except Exception as e:
            logger.warning(f"[AstrLover] 打标失败（#{row['id']} {row['file']}）：{e}")
            await app.dao.set_image_status(row["id"], "failed")
            return False
