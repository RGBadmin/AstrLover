"""统一执行通道（D4/R7）：她的自主行为与导演编排走同一批方法。

pending_actions 表里的到期任务由心跳送到这里执行；
导演 bot 的"说/做"即时指令也直接调用这里。
效果上，"她恰好做了你想让她做的事"，毫无违和。
"""

import time

from astrbot.api import logger
from astrbot.api.event import MessageChain

from .chat.composer import parse_reply
from .chat.delivery import Deliverer

_SAY_NOTE = (
    "【此刻的特殊情况】你心里一直惦记着一件事，现在想跟他说：{instruction}\n"
    "把它说成完全是你自己想说的话——带着你的关心和语气，自然地开口。"
    "绝不能提到「有人让你说」「收到指令」之类的话，这本来就是你自己的心意。"
    "一两条短消息即可。"
    "（对话记录末尾括号里的引导语是你自己的内心活动，不是他发的消息。）"
)

_VOICE_NOTE = (
    "【此刻的特殊情况】你想给他发一条语音说：{instruction}\n"
    "写出你要说出口的话（口语、自然、80字以内，就像对着手机说话），只输出这段话。"
)


class ActionExecutor:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    # 心跳调用：到期任务
    # ------------------------------------------------------------------
    async def execute(self, row: dict):
        kind = row["kind"]
        payload = row.get("payload") or {}
        try:
            ok = await self.run(kind, payload)
            await self.app.dao.finish_action(row["id"], "done" if ok else "failed")
            logger.info(f"[AstrLover] 待办动作 {kind}#{row['id']} 执行{'成功' if ok else '失败'}。")
        except Exception:
            await self.app.dao.finish_action(row["id"], "failed")
            logger.error(f"[AstrLover] 待办动作 {kind}#{row['id']} 异常：", exc_info=True)

    # ------------------------------------------------------------------
    # 即时执行（导演 bot 直接调用）
    # ------------------------------------------------------------------
    async def run(self, kind: str, payload: dict) -> bool:
        app = self.app
        if kind == "say":
            return await self._do_say(str(payload.get("instruction", "")))
        if kind == "post":
            return await app.impulses.compose_and_post(
                topic=str(payload.get("topic", "")),
                forced_text=str(payload.get("text", "")),
            )
        if kind == "avatar":
            return await app.impulses.change_avatar(hint=str(payload.get("hint", "")))
        if kind == "signature":
            return await app.impulses.change_signature(hint=str(payload.get("hint", "")))
        if kind == "voice":
            return await self._do_voice(str(payload.get("instruction", "")))
        logger.warning(f"[AstrLover] 未知动作类型：{kind}")
        return False

    # ------------------------------------------------------------------
    async def _do_say(self, instruction: str) -> bool:
        app = self.app
        if not instruction:
            return False
        umo = await app.linked_umo()
        if not umo:
            return False
        system_prompt = await app.build_master_prompt(
            query_text=instruction,
            extra_note=_SAY_NOTE.format(instruction=instruction),
        )
        raw = await app.llm.chat(
            prompt="（你想起了心里惦记的那件事，拿起手机跟他说。）",
            contexts=await app.working.contexts(),
            system_prompt=system_prompt,
        )
        parsed = parse_reply(raw, max_segments=3)
        if not parsed.segments:
            return False

        async def send(chain: MessageChain):
            await app.context.send_message(umo, chain)

        await Deliverer(app, send).deliver(parsed)
        await app.dao.add_event(
            "proactive",
            f"主动跟他说了：{parsed.plain_text()[:60]}",
            motivation="心里一直惦记着这件事",
        )
        unanswered = (await app.dao.kv_get("proactive_unanswered", 0) or 0) + 1
        await app.dao.kv_set("proactive_unanswered", unanswered)
        return True

    async def _do_voice(self, instruction: str) -> bool:
        app = self.app
        if not instruction:
            return False
        umo = await app.linked_umo()
        if not umo:
            return False
        system_prompt = await app.build_master_prompt(
            query_text=instruction,
            extra_note=_VOICE_NOTE.format(instruction=instruction),
        )
        text = (await app.llm.chat(prompt="（你按下了语音键。）", system_prompt=system_prompt)).strip()
        if not text:
            return False
        record = await app.voice.tts_record(text) if app.voice else None
        if record is not None:
            await app.context.send_message(umo, MessageChain(chain=[record]))
            await app.working.log_her(text, kind="voice")
        else:
            await app.context.send_message(umo, MessageChain().message(text))
            await app.working.log_her(text)
        await app.dao.add_event(
            "proactive", f"给他发了条语音：{text[:40]}", motivation="想让他听到你的声音"
        )
        return True

    # ------------------------------------------------------------------
    # 供导演 bot 使用：定时排程
    # ------------------------------------------------------------------
    async def schedule(self, kind: str, payload: dict, due_ts: int, source: str = "director") -> int:
        return await self.app.dao.add_action(kind, payload, due_ts=due_ts, source=source)

    @staticmethod
    def now_ts() -> int:
        return int(time.time())
