"""主对话管线：主 bot 上与主人的私聊，被她完整接管。

节奏设计：
- 短时间内连发的多条消息合并为一轮（真人不会每条都单独回）；
- 回复分段发送，段间有"打字中"与拟真延迟；
- 语音/表情包/照片等能力缺席时自动降级，永不报错给对方。
"""

import asyncio
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain

import astrbot.api.message_components as Comp

from .composer import parse_reply
from .delivery import Deliverer

_DEBOUNCE_SECONDS = 2.2


class ChatPipeline:
    def __init__(self, app):
        self.app = app
        self._buffer: list[dict] = []      # 待处理的消息内容 {text, images, kind}
        self._worker: asyncio.Task | None = None
        self._last_event: AstrMessageEvent | None = None
        self._turn_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    async def on_owner_message(self, event: AstrMessageEvent):
        app = self.app
        app.llm.owner_umo = event.unified_msg_origin
        await app.dao.kv_set("owner_umo", event.unified_msg_origin)
        await app.dao.kv_set("last_user_ts", int(time.time()))
        await app.dao.kv_set("proactive_unanswered", 0)

        piece = await self._extract(event)
        if piece is None:
            return
        self._buffer.append(piece)
        self._last_event = event

        # 入库（工作记忆）+ 情绪响应（P1：他开口，负面情绪消散）
        await app.working.log_user(piece["text"], kind=piece["kind"])
        if app.mood:
            await app.mood.on_user_message(piece["text"])

        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._debounced_turn())

    async def on_stranger_private(self, event: AstrMessageEvent):
        """别人来搭话：她是专一的。每人每天最多礼貌回一句。"""
        app = self.app
        sender = str(event.get_sender_id())
        key = f"stranger_replied:{sender}:{time.strftime('%Y-%m-%d')}"
        if await app.dao.kv_get(key):
            return
        await app.dao.kv_set(key, 1)
        name = app.profile.nickname
        await event.send(MessageChain().message(f"你好呀，我是{name}。不过我只和一个人聊天哦，拜拜～"))

    # ------------------------------------------------------------------
    # 合并短时间内的连发消息，然后跑一轮对话
    # ------------------------------------------------------------------
    async def _debounced_turn(self):
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            while self._buffer:
                async with self._turn_lock:
                    pieces, self._buffer = self._buffer, []
                    try:
                        await self._run_turn(pieces)
                    except Exception:
                        logger.error("[AstrLover] 对话轮异常：", exc_info=True)
                        await self._safe_send_text("呜，我脑子突然卡了一下…你刚说什么？")
                # 若处理期间又来了新消息，小睡片刻继续合并
                if self._buffer:
                    await asyncio.sleep(_DEBOUNCE_SECONDS)
        finally:
            self._worker = None

    async def _run_turn(self, pieces: list[dict]):
        app = self.app
        images: list[str] = []
        for p in pieces:
            images.extend(p.get("images", []))

        # 回复节奏（A4）：睡着/忙碌时晚一点回，醒来/回来自有交代
        if app.life:
            extra = await app.life.pre_reply_delay()
            if extra > 0:
                await asyncio.sleep(extra)

        contexts = await app.working.contexts()
        # 最后一条 user 消息作为 prompt 交给模型（contexts 已含全部历史，故弹出末尾重复）
        prompt = None
        if contexts and contexts[-1]["role"] == "user":
            prompt = contexts.pop()["content"]
        if not prompt:
            prompt = "\n".join(p["text"] for p in pieces) or "（无内容）"

        system_prompt = await self.app.build_master_prompt(prompt)

        try:
            raw = await app.llm.chat(
                prompt=prompt,
                contexts=contexts,
                system_prompt=system_prompt,
                image_urls=images or None,
            )
        except Exception as e:
            if images:  # 多模态失败时退回纯文本再试一次
                logger.warning(f"[AstrLover] 带图对话失败，退回纯文本重试：{e}")
                raw = await app.llm.chat(
                    prompt=prompt + "\n（他刚发了图片，但你手机加载不出来）",
                    contexts=contexts,
                    system_prompt=system_prompt,
                )
            else:
                raise

        parsed = parse_reply(raw)
        deliverer = Deliverer(self.app, self._send_chain, self._typing)
        await deliverer.deliver(parsed)
        await self._post_turn(parsed)

    # ------------------------------------------------------------------
    # 轮后处理：编造固化、事件提及状态、沉淀标记
    # ------------------------------------------------------------------
    async def _post_turn(self, parsed):
        app = self.app
        for note in parsed.improvs:
            await app.fix_improvised(note)
        for eid in parsed.told_events:
            await app.dao.set_event_mention(eid, "told")
        for eid in parsed.found_events:
            await app.dao.set_event_mention(eid, "discovered")
        await app.dao.kv_set("memory_dirty", 1)  # 心跳空闲时做记忆沉淀（M2）

    # ------------------------------------------------------------------
    # 底层发送
    # ------------------------------------------------------------------
    async def _send_chain(self, chain: MessageChain):
        event = self._last_event
        if event is not None:
            await event.send(chain)
            return
        umo = await self.app.dao.kv_get("owner_umo")
        if umo:
            await self.app.context.send_message(umo, chain)

    async def _safe_send_text(self, text: str):
        try:
            await self._send_chain(MessageChain().message(text))
        except Exception:
            pass

    async def _typing(self):
        """尽力而为的 typing 指示，失败不影响主流程。"""
        event = self._last_event
        client = getattr(event, "client", None)
        if client is None:
            return
        try:
            await client.send_chat_action(chat_id=event.get_sender_id(), action="typing")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 消息内容提取
    # ------------------------------------------------------------------
    async def _extract(self, event: AstrMessageEvent) -> dict | None:
        texts: list[str] = []
        images: list[str] = []
        kind = "text"
        for comp in event.get_messages():
            if isinstance(comp, Comp.Plain):
                if comp.text and comp.text.strip():
                    texts.append(comp.text.strip())
            elif isinstance(comp, Comp.Record):
                kind = "voice"
                stt_text = None
                if self.app.voice:
                    stt_text = await self.app.voice.transcribe(comp)
                texts.append(stt_text if stt_text else "[发来一条语音，但你这边没加载出来，没听清]")
            elif isinstance(comp, Comp.Image):
                url = getattr(comp, "url", None) or getattr(comp, "file", None)
                if url:
                    images.append(url)
                if kind == "text":
                    kind = "photo"
        text = "\n".join(texts).strip()
        if not text and not images:
            return None
        if not text and images:
            text = "[发来一张图片]"
        return {"text": text, "images": images, "kind": kind}
