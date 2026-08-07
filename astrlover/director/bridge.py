"""导演桥：以角色的身份说话、写回她的对话历史、按她的人格生成内容。

为什么必须写回历史：否则她下一轮不知道自己说过这句——你让她说
「今天加班到很晚」，五分钟后她可能问你「你今天忙吗」。
"""

import json
from datetime import datetime

from astrbot.api import logger

STAMP_FMT = "[%m-%d %H:%M]"

DEFAULT_ACT = (
    "以你的身份、你的语气，把下面这件事说给他听。只输出你要说的话，"
    "不要加引号、不要解释、不要加旁白。"
)
DEFAULT_HEAD = ""
DEFAULT_TAIL = (
    "注意：这一轮只说话，不要调用任何工具。"
)


class DirectorBridge:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    def target(self) -> str:
        return str(self.app.state_target or "")

    @staticmethod
    def umo_platform(umo: str) -> str:
        return (umo or "").split(":", 1)[0]

    def platform_client(self, umo: str):
        """目标会话那个 bot 的 PTB 客户端（发图/频道/头像要用她的身份）。"""
        try:
            inst = self.app.context.get_platform_inst(self.umo_platform(umo))
            return getattr(inst, "client", None)
        except Exception:
            return None

    # ------------------------------------------------------------------
    async def append_assistant(self, umo: str, text: str) -> bool:
        """把她说的话写进对话历史。"""
        app = self.app
        try:
            cm = app.context.conversation_manager
            cid = await cm.get_curr_conversation_id(umo)
            if not cid:
                logger.warning(
                    f"[AstrLover] 写历史失败：{umo} 还没有对话。"
                    "先在那个会话里正常聊一句，让 AstrBot 把对话建起来"
                )
                return False
            conv = await cm.get_conversation(umo, cid)
            if not conv:
                logger.warning(f"[AstrLover] 写历史失败：取不到对话 {cid}")
                return False
            history = json.loads(conv.history or "[]")
            if not isinstance(history, list):
                history = []
            body = text
            if app.conf.get("stamp_own_messages", True):
                tz = app.clock.tz if app.clock else None
                body = f"{datetime.now(tz).strftime(STAMP_FMT)} {text}"
            history.append({"role": "assistant", "content": body})
            await cm.update_conversation(umo, cid, history=history)
            return True
        except Exception as e:
            logger.error(f"[AstrLover] 写历史失败：{e}")
            return False

    async def deliver(self, text: str) -> str:
        """以角色身份把一段话发到目标会话，并记进她的历史。"""
        from astrbot.api.event import MessageChain

        target = self.target()
        text = (text or "").strip()
        if not text:
            return "内容是空的，没发。"
        if not target:
            return "还没绑定目标会话。`/umo` 看有哪些，`/link` 绑一个。"
        try:
            found = await self.app.context.send_message(target, MessageChain().message(text))
        except Exception as e:
            logger.error(f"[AstrLover] 导演发送失败：{e}")
            return f"没发出去：{e}"
        if not found:
            return f"找不到目标平台 {self.umo_platform(target)}，那个 bot 还连着吗？"

        wrote = await self.append_assistant(target, text)
        # 说出去的话也进她的记忆素材（日记会用到）
        if self.app.working:
            await self.app.working.log_her(text)
        head = f"已发到 {self.umo_platform(target)}：\n{text}"
        if wrote:
            return head
        return head + "\n\n⚠️ 但没能写进对话历史，她之后不记得说过这句。日志里搜「写历史失败」。"

    # ------------------------------------------------------------------
    @staticmethod
    def _persona_text(p) -> str:
        """人格正文：字段名两套（system_prompt / prompt），容器两种（对象 / dict）。"""
        if not p:
            return ""
        if isinstance(p, dict):
            return str(p.get("prompt") or p.get("system_prompt") or "").strip()
        for attr in ("prompt", "system_prompt"):
            if v := getattr(p, attr, None):
                return str(v).strip()
        return ""

    async def persona_of(self, umo: str, conv=None) -> str:
        app = self.app
        pm = getattr(app.context, "persona_manager", None)
        if pm is None:
            return ""
        pid = getattr(conv, "persona_id", None) if conv else None
        # 官方解析入口还会考虑会话级强制人格（/persona 设的那个）
        if resolve := getattr(pm, "resolve_selected_persona", None):
            try:
                cfg = app.context.astrbot_config_mgr.get_conf(umo)
                _, persona, _, _ = await resolve(
                    umo=umo,
                    conversation_persona_id=pid,
                    platform_name=self.umo_platform(umo),
                    provider_settings=(cfg or {}).get("provider_settings", {}),
                )
                if text := self._persona_text(persona):
                    return text
            except Exception as e:
                logger.debug(f"[AstrLover] resolve_selected_persona 用不了：{e}")
        try:
            p = pm.get_persona_v3_by_id(pid) if pid else None
            if p is None:
                p = await pm.get_default_persona_v3(umo=umo)
            return self._persona_text(p)
        except Exception as e:
            logger.warning(f"[AstrLover] 取人格失败，这次不带人格生成：{e}")
            return ""

    # ------------------------------------------------------------------
    async def generate(self, brief: str, instruct: str | None = None) -> str:
        """按导演提示，用她的人格 + 最近对话生成一段文本。

        instruct=None 用默认那句（/act 走这条）；空串表示 brief 已写全要求。
        生命层开启时，把"她的此刻"一并带上——主动消息才知道自己在干嘛。
        """
        app = self.app
        target = self.target()
        if not target:
            raise RuntimeError("还没绑定目标会话（/link）")

        cm = app.context.conversation_manager
        cid = await cm.get_curr_conversation_id(target)
        conv = await cm.get_conversation(target, cid) if cid else None
        history = []
        if conv:
            try:
                history = json.loads(conv.history or "[]")
            except json.JSONDecodeError:
                history = []
        limit = max(2, int(app.conf.get("director_context_turns", 40) or 40))
        ctx = history[-limit:] if isinstance(history, list) else []

        system_prompt = await self.persona_of(target, conv)
        if not system_prompt:
            logger.warning("[AstrLover] 没拿到人格，这次只靠历史模仿语气")
        if app.ready:  # 人格是 system_prompt 的主体，这里只补她的此刻与记忆
            try:
                system_prompt = (system_prompt + "\n\n" +
                                 await app.build_life_block(brief[:60] or "主动开口")).strip()
            except Exception as e:
                logger.debug(f"[AstrLover] 生命层上下文拼接失败：{e}")

        act = DEFAULT_ACT if instruct is None else instruct
        tail = DEFAULT_TAIL
        prompt = "\n".join(x for x in (act, brief, tail) if x)

        provider_id = await app.context.get_current_chat_provider_id(target)
        resp = await app.context.llm_generate(
            chat_provider_id=provider_id,
            system_prompt=system_prompt,
            contexts=ctx,
            prompt=prompt,
        )
        text = (getattr(resp, "completion_text", "") or "").strip()
        if not text:
            raise RuntimeError("模型返回空内容（thinking 模型可能把配额烧在思考上了）")
        # 她照着上下文模仿的时间戳剥掉——真正的戳由 append_assistant 打
        import re

        return re.sub(r"^[ \t]*\[\d{2}-\d{2} \d{2}:\d{2}\][ \t]*", "", text, flags=re.M).strip()
