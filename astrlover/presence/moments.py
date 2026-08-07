"""频道动态：她的朋友圈。

为什么用频道而不是 bot 简介：简介只在你还没跟 bot 对话过时显示一次，
聊过之后永远看不到；频道是真时间线——能往回翻、有推送、能累积。

历史动态按时间戳插进对话时间线（而不是附在末尾当清单），所以她知道
"那条动态是前天下午发的，当时我们正聊着……"。插入项带 _no_save，
只发给模型不写进对话历史，每轮重新编排，不重复堆积也不怕 /new。
"""

import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger

MEDIA_GROUP_MAX = 10   # Telegram 一组媒体最多 10 张
CAPTION_MAX = 1024     # 图片 caption 上限；纯文字消息上限 4096
STAMP_FMT = "[%m-%d %H:%M]"

_MOMENT_NOTE = """<your_own_moments>
你发过的动态以【我发了条动态】的形式出现在对话时间线里，按实际时间插在当时的位置。
那些都是你自己发的，你记得内容、配图和当时为什么发，也知道前后正在聊什么。
对方提起时不要表现得像第一次看到，也不要重复发内容相近的动态。

标着「当时没跟他提」的，是你发的时候故意没在聊天里说的。
他要是自己刷到来问，说不说、什么时候说，看你当时心情——
但你一直是知情的那个。
</your_own_moments>"""


class Moments:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    def channel(self) -> str:
        return str(self.app.conf.get("channel_id") or "").strip()

    async def _list(self) -> list[dict]:
        return await self.app.dao.kv_get("moments", []) or []

    async def _save(self, moments: list[dict]):
        await self.app.dao.kv_set("moments", moments[-200:])

    # ------------------------------------------------------------------
    async def post(self, client, text: str, photos: list[Path] | None = None,
                   *, quiet: bool = False, enforce_limits: bool = True) -> str:
        """发布一条动态。enforce_limits=False 用于手动指令（冷却与每日上限
        只约束她的自主行为，你的手动指令随时能发、不消耗配额）。"""
        app = self.app
        channel = self.channel()
        if not channel:
            return "发动态失败：还没有配置频道 ID，去插件配置里填上。"
        if client is None:
            return "发动态失败：这个功能只能在 Telegram 上用。"

        if enforce_limits:
            if wait := await app.limits.cooldown_left(
                "post", int(app.conf.get("post_cooldown_minutes", 180) or 180)
            ):
                return f"现在还发不了动态，距离上一条还差 {wait} 分钟。等会儿再说。"
            if await app.limits.daily_left(
                "post", int(app.conf.get("post_daily_limit", 5) or 5)
            ) == 0:
                return "今天的动态已经发够了，明天再发。"

        photos = [p for p in (photos or []) if p and Path(p).exists()][:MEDIA_GROUP_MAX]
        try:
            await self._send(client, channel, text, photos)
        except Exception as e:
            logger.error(f"[AstrLover] 发动态失败：{e}")
            return f"发动态失败了：{e}"

        moments = await self._list()
        moments.append({
            "ts": time.time(), "text": text,
            "photos": [str(p) for p in photos], "quiet": quiet,
        })
        await self._save(moments)
        if enforce_limits:
            await app.limits.mark_done("post")
            await app.limits.bump_daily("post")
        if app.dao:
            await app.dao.add_event(
                "post",
                f"发了条动态：「{text[:60]}」" + (f"，配图 {len(photos)} 张" if photos else ""),
                motivation="", meta={"quiet": quiet},
            )
        # 配图登记进相册，之后能被翻到、也能重新发给他
        for p in photos:
            try:
                rel = str(Path(p).name)
                await app.album.register(f"__moment__/{rel}", "动态配图", int(time.time()))
            except Exception:
                pass

        detail = f"，带了 {len(photos)} 张图" if photos else ""
        if quiet:
            return (f"动态发出去了{detail}。这条你没打算主动说——"
                    "接下来正常聊你的，别提这件事。他要是自己看到来问你，再决定说不说。")
        return f"动态发出去了{detail}。可以顺口跟他说一声。"

    async def _send(self, client, channel: str, text: str, photos: list[Path]):
        """按张数选发送方式；caption 超长时图文分两条发。"""
        if not photos:
            await client.send_message(chat_id=channel, text=text)
            return
        caption = text if len(text) <= CAPTION_MAX else None
        handles = []
        try:
            if len(photos) == 1:
                f = open(photos[0], "rb")
                handles.append(f)
                await client.send_photo(chat_id=channel, photo=f, caption=caption)
            else:
                from telegram import InputMediaPhoto

                media = []
                for i, p in enumerate(photos):
                    f = open(p, "rb")
                    handles.append(f)
                    media.append(InputMediaPhoto(media=f, caption=caption if i == 0 else None))
                await client.send_media_group(chat_id=channel, media=media)
        finally:
            for f in handles:
                f.close()
        if caption is None:  # 正文太长塞不进 caption，补发一条纯文字
            await client.send_message(chat_id=channel, text=text)

    # ------------------------------------------------------------------
    async def inject(self, req) -> tuple[int, int]:
        """把历史动态按时间插进 req.contexts。返回 (锚点数, 堆在末尾的条数)。"""
        app = self.app
        if not app.conf.get("inject_history", True):
            return 0, 0
        moments = await self._list()
        if not moments:
            return 0, 0
        limit = int(app.conf.get("inject_history_limit", 0) or 0)
        selected = moments[-limit:] if limit > 0 else moments

        from ..photos.archive import context_time

        pending = sorted(selected, key=lambda m: m["ts"])
        merged: list[dict] = []
        idx = anchors = 0
        for msg in req.contexts or []:
            if msg.get("role") == "user" and (when := context_time(msg)):
                anchors += 1
                while idx < len(pending) and pending[idx]["ts"] < when:
                    merged.append(self._entry(pending[idx]))
                    idx += 1
            merged.append(msg)
        tail = len(pending) - idx
        while idx < len(pending):
            merged.append(self._entry(pending[idx]))
            idx += 1
        req.contexts = merged

        app.inject_text(req, _MOMENT_NOTE)
        logger.info(
            f"[AstrLover] {len(selected)} 条动态插入时间线"
            f"（时间锚点 {anchors} 个，其中 {tail} 条排在全部历史之后）"
        )
        if req.contexts and anchors == 0 and len(req.contexts) > len(selected):
            logger.warning(
                "[AstrLover] 历史里一个时间锚点都没有，动态只能堆在末尾，顺序不可信"
                "（datetime_system_prompt 被关、或历史已被压缩）"
            )
        return anchors, tail

    def _entry(self, moment: dict) -> dict:
        tz = self.app.clock.tz if self.app.clock else None
        stamp = datetime.fromtimestamp(moment["ts"], tz).strftime(STAMP_FMT)
        bits = [f"{stamp}【我发了条动态】{moment['text']}"]
        if photos := moment.get("photos"):
            bits.append(f"配图 {len(photos)} 张")
        if moment.get("quiet"):
            bits.append("当时没跟他提")
        # _no_save：只发给模型，不写进对话历史
        return {"role": "assistant", "content": " · ".join(bits), "_no_save": True}

    async def count(self) -> int:
        return len(await self._list())
