"""图片记忆的三个管线钩子：登记落盘 → 请求目录层描述 → 上下文瘦身。

优先级顺序（数字越小越晚跑）：
  -35 登记：图片一进视野就落盘分配编号；压缩/清空上下文之后档案还在
  -40 请求描述：把还没描述的编号列出来，请她在这轮回复末尾顺带写
  -50 折叠：只留最近 N 张真图，更早的换成 [图片 #N · 时间 · 描述 · 已折叠]
"""

import asyncio
import time
from datetime import datetime

from astrbot.api import logger

from .archive import NOTES_PER_TURN, context_time, msg_own_text

_DESCRIBE_BLOCK = """<describe_images>
上下文里这几张图还没有存过描述：{ids}
请在这次回复的最末尾，为每张各写一条：
  <img_note id="编号">这张图是什么</img_note>
按图片在对话里出现的先后顺序对应编号。
{howto}这几行会被系统抽走存档，对方看不到，也不算进你的回复。
正常说你的话，把这些附在最后即可。

{recall}</describe_images>"""

_HOWTO_WITH_VISION = (
    "一句话就行，抓最能认出这张图的那个点——谁拍的、什么场合、\n"
    "画面里最显眼的是什么。用你自己的说法。画面里的琐碎细节\n"
    "系统会另外记一份，你不用写。\n"
)
_HOWTO_PLAIN = (
    "描述要具体到能靠它认出这张图——画面内容、谁拍的、什么场合，\n"
    "用你自己的说法就行。\n"
)
_RECALL = (
    "另外：对方提起某张旧图时（「昨天那张」「黑丝那张」），\n"
    "先看对话里的 [图片 #N ...] 占位——描述就在里面，直接认出来即可。\n"
    "上下文里找不到再用 find_photo 查存档。\n"
    "**符合的不止一张时，问他是哪张，不要自己挑一张然后当成就是那张。**\n"
)
_RECALL_VISION = (
    "他要是问起某张图里的具体东西（画面里有什么、什么颜色、写了什么字），\n"
    "用 inspect_photo 查那张图的细节记录，别硬猜也别急着 recall_photo——\n"
    "recall_photo 是把原图整个搬回来，只有真的需要亲眼再看一遍才用。\n"
)


class PhotoMemory:
    def __init__(self, app):
        self.app = app
        self._detail_tasks: set[asyncio.Task] = set()
        self._recall_queue: list[int] = []

    # ------------------------------------------------------------------
    @staticmethod
    def _image_parts(req):
        """遍历上下文里的图片部件，产出 (msg, index, url)。"""
        for msg in req.contexts or []:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for i, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "image_url":
                    yield msg, i, (part.get("image_url") or {}).get("url") or ""

    # ------------------------------------------------------------------
    # -35 登记落盘 + 派发细节层解析
    # ------------------------------------------------------------------
    async def register(self, req):
        app = self.app
        keep = int(app.star_conf.get("max_context_images", 0) or 0)
        if not app.vision.ready() and keep <= 0:
            return  # 存盘只为"折叠后能取回"和"给视觉模型读"，都不需要就别占磁盘

        pids: list[int] = []
        for msg, _i, url in self._image_parts(req):
            pid = await app.photos.register_data_url(url, seen_ts=context_time(msg))
            if pid and pid not in pids:
                pids.append(pid)
        if pids:
            await self._dispatch_detail(pids)

    async def _dispatch_detail(self, pids: list[int]):
        app = self.app
        if not app.vision.ready():
            return
        rows = await app.photos.needs_detail(pids)
        for row in rows:
            if any(t.get_name() == f"detail:{row['id']}" for t in self._detail_tasks):
                continue
            task = asyncio.create_task(self._detail_one(row), name=f"detail:{row['id']}")
            self._detail_tasks.add(task)
            task.add_done_callback(self._detail_tasks.discard)

    async def _detail_one(self, row: dict):
        """异步解析一张图的细节层，不阻塞回复。"""
        app = self.app
        path = app.photos.abs_path(row)
        if path is None:
            return
        try:
            async with app.vision.gate():
                text, _ = await app.vision.describe(str(path))
            max_chars = max(100, int(app.star_conf.get("vision_max_chars", 600) or 600))
            await app.photos.set_detail(int(row["id"]), text[:max_chars])
            logger.info(f"[AstrLover] 图片 #{row['id']} 细节记录已生成（{len(text)} 字）")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await app.photos.bump_detail_fail(int(row["id"]))
            logger.warning(f"[AstrLover] 图片 #{row['id']} 细节解析失败：{e}")

    # ------------------------------------------------------------------
    # -40 请求目录层描述
    # ------------------------------------------------------------------
    async def ask_descriptions(self, req):
        app = self.app
        if not app.star_conf.get("describe_images", True):
            return
        pids: list[int] = []
        for _msg, _i, url in self._image_parts(req):
            sha = app.photos.sha_of(url)
            if sha is None:
                continue
            row = await app.db.fetchone("SELECT id FROM photo_archive WHERE sha=?", (sha,))
            if row and int(row["id"]) not in pids:
                pids.append(int(row["id"]))
        pending = await app.photos.missing_catalog(pids)
        if not pending:
            return
        # 折叠从最旧开始，所以最旧的几张最急着要描述；新图下轮再排
        pending = pending[:NOTES_PER_TURN]

        has_vision = app.vision.ready()
        block = _DESCRIBE_BLOCK.format(
            ids="、".join(f"#{p}" for p in pending),
            howto=_HOWTO_WITH_VISION if has_vision else _HOWTO_PLAIN,
            recall=_RECALL + (_RECALL_VISION if has_vision else ""),
        )
        req.system_prompt = (req.system_prompt or "") + "\n\n" + block

    # ------------------------------------------------------------------
    # -50 上下文瘦身
    # ------------------------------------------------------------------
    async def prune(self, req):
        app = self.app
        keep = int(app.star_conf.get("max_context_images", 0) or 0)
        if keep <= 0:
            return
        slots = [(msg, i, url) for msg, i, url in self._image_parts(req)]
        if len(slots) <= keep:
            return

        tz = app.clock.tz if app.clock else None
        pruned = 0
        for msg, i, url in slots[:-keep]:
            pid = await app.photos.register_data_url(url, seen_ts=context_time(msg))
            row = await app.photos.get(pid) if pid else None
            when = context_time(msg) or (row["seen_ts"] if row else 0)
            stamp = (
                datetime.fromtimestamp(when, tz).strftime("%m-%d %H:%M")
                if when else "时间不详"
            )
            bits = [f"图片 #{pid or '?'}", stamp]
            if row and row["catalog"]:
                bits.append(row["catalog"])   # 她自己的描述是靠内容检索的唯一依据
            elif said := msg_own_text(msg):
                who = "他" if msg.get("role") == "user" else "你"
                bits.append(f"{who}当时说「{said[:60]}」")
            bits.append("已折叠")
            msg["content"][i] = {"type": "text", "text": "[" + " · ".join(bits) + "]"}
            pruned += 1
        if pruned:
            logger.info(f"[AstrLover] 上下文图片瘦身：折叠 {pruned} 张，保留最近 {keep} 张")

    # ------------------------------------------------------------------
    # recall：把原图塞回下一轮上下文
    # ------------------------------------------------------------------
    def queue_recall(self, pid: int):
        if pid not in self._recall_queue:
            self._recall_queue.append(pid)

    async def serve_recalled(self, req):
        """把 recall_photo 点名的图放回这一轮的上下文。"""
        if not self._recall_queue:
            return
        app = self.app
        pids, self._recall_queue = self._recall_queue, []
        parts = []
        for pid in pids:
            row = await app.photos.get(pid)
            path = app.photos.abs_path(row) if row else None
            if path is None:
                continue
            import base64
            import mimetypes

            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            b64 = base64.b64encode(path.read_bytes()).decode()
            parts.append({"type": "text", "text": f"[取回的图片 #{pid}]"})
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        if parts:
            req.contexts = list(req.contexts or []) + [{"role": "user", "content": parts}]
            logger.info(f"[AstrLover] 取回图片进上下文：{pids}")

    # ------------------------------------------------------------------
    async def capture(self, text: str) -> tuple[str, int]:
        """从回复里摘走目录层描述并存档。返回 (清理后文本, 条数)。"""
        from .archive import harvest_notes

        clean, notes = harvest_notes(text)
        for pid, body in notes.items():
            await self.app.photos.set_catalog(pid, body)
        if notes:
            logger.info(f"[AstrLover] 收到 {len(notes)} 条图片描述")
        return clean, len(notes)

    # ------------------------------------------------------------------
    async def backfill_details(self, limit: int = 20) -> str:
        """/vision 给存量图片补细节记录。"""
        app = self.app
        if not app.vision.ready():
            return "视觉 API 未配置。"
        rows = await app.db.fetchall(
            "SELECT * FROM photo_archive WHERE detail='' AND detail_fail < 3 "
            "ORDER BY id DESC LIMIT ?", (limit,)
        )
        if not rows:
            return "没有需要补的图片。"
        done = 0
        for row in rows:
            path = app.photos.abs_path(row)
            if path is None:
                continue
            try:
                async with app.vision.gate():
                    text, _ = await app.vision.describe(str(path))
                await app.photos.set_detail(int(row["id"]), text)
                done += 1
            except Exception as e:
                await app.photos.bump_detail_fail(int(row["id"]))
                logger.warning(f"[AstrLover] 补细节失败 #{row['id']}：{e}")
        return f"补做完成：{done}/{len(rows)} 张。"

    def cancel_all(self):
        for t in list(self._detail_tasks):
            if not t.done():
                t.cancel()
        self._detail_tasks.clear()


def now_ts() -> int:
    return int(time.time())
