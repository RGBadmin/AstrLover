"""统一投递器：被动回复与主动消息共用的"她说话的样子"。

无论消息由对话管线还是心跳/上帝编排发起，分段节奏、语音/表情包/照片
的降级策略都一致——她只有一种说话方式。
"""

import asyncio
from collections.abc import Awaitable, Callable

from astrbot.api import logger
from astrbot.api.event import MessageChain

from .composer import ParsedReply, typing_delay

SendFn = Callable[[MessageChain], Awaitable[None]]
TypingFn = Callable[[], Awaitable[None]]


class Deliverer:
    def __init__(self, app, send_chain: SendFn, typing: TypingFn | None = None):
        self.app = app
        self.send_chain = send_chain
        self.typing = typing

    async def deliver(self, parsed: ParsedReply):
        app = self.app
        for seg in parsed.segments:
            if self.typing:
                try:
                    await self.typing()
                except Exception:
                    pass
            await asyncio.sleep(typing_delay(seg.text))
            try:
                if seg.type == "voice":
                    await self._voice(seg.text)
                elif seg.type == "sticker":
                    await self._sticker(seg.text)
                elif seg.type == "photo":
                    await self._photo(seg.text)
                else:
                    await self.send_chain(MessageChain().message(seg.text))
                    await app.working.log_her(seg.text)
            except Exception:
                logger.error(f"[AstrLover] 段投递失败（{seg.type}）：", exc_info=True)

    async def _voice(self, text: str):
        app = self.app
        record = await app.voice.tts_record(text) if app.voice else None
        if record is not None:
            await self.send_chain(MessageChain(chain=[record]))
            await app.working.log_her(text, kind="voice")
        else:  # 语音不可用 → 文字照发
            await self.send_chain(MessageChain().message(text))
            await app.working.log_her(text)

    async def _sticker(self, desc: str):
        app = self.app
        path = await app.pick_sticker(desc)
        if path:
            await self.send_chain(MessageChain().file_image(path))
            await app.working.log_her(desc, kind="sticker")
        # 没有合适的就不发——没有比发错表情包更穿帮的事

    async def _photo(self, desc: str):
        app = self.app
        path = await app.provide_picture(desc)
        if path:
            await self.send_chain(MessageChain().file_image(path))
            await app.working.log_her(desc, kind="photo")
        else:
            fallback = "拍好的照片居然发不出去，气。回头再给你看！"
            await self.send_chain(MessageChain().message(fallback))
            await app.working.log_her(fallback)
