"""模型路由：主对话模型 / 轻量决策模型 的统一入口。

对话走**会话当前模型**——那是 AstrBot 管线本来就在用的那个，插件不另配。
记忆沉淀、意图解析这些高频杂活走**轻量模型**：地址/Key/模型在插件自己的
设置里（跟视觉、向量一个路子），报错也落在插件自己的日志上。
留空则回退到会话当前模型——不配也能跑，只是这些杂活也走大模型、贵一点。
"""

import asyncio
import json
import re

from astrbot.api import logger

from .light.client import LightClient, LightError

_JSON_RE = re.compile(r"[\[{].*[\]}]", re.S)


class LLM:
    def __init__(self, context, cfg, conf=None):
        self.context = context
        self.cfg = cfg
        self.light_client = LightClient(conf) if conf is not None else None
        self.owner_umo: str | None = None  # 主人会话，用于 get_using_provider 兜底

    # ---- Provider 解析 ----
    def _by_id(self, provider_id: str):
        if not provider_id:
            return None
        try:
            return self.context.get_provider_by_id(provider_id)
        except Exception:
            return None

    def _using(self):
        try:
            if self.owner_umo:
                return self.context.get_using_provider(umo=self.owner_umo)
            return self.context.get_using_provider()
        except Exception:
            return None

    def chat_provider(self):
        """对话主模型 = 会话当前模型（管线模式下由 AstrBot 决定）。"""
        return self._using()

    @property
    def light_ready(self) -> bool:
        return bool(self.light_client and self.light_client.configured)

    # ---- 调用 ----
    async def _call(self, provider, prompt: str, system_prompt: str | None,
                    retries: int = 1) -> str:
        if provider is None:
            raise RuntimeError("没有可用的模型：轻量模型没配，会话也没有当前模型")
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await provider.text_chat(prompt=prompt, system_prompt=system_prompt)
                text = getattr(resp, "completion_text", None) or ""
                if text.strip():
                    return text.strip()
                raise RuntimeError("模型返回了空内容")
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"模型调用失败：{last_err}")

    async def light(self, prompt: str, system_prompt: str | None = None) -> str:
        """配了就走自管的轻量模型，没配就用会话当前模型。"""
        if self.light_ready:
            try:
                return await self.light_client.chat(prompt, system_prompt or "")
            except LightError as e:
                # 配了却用不了要说出来——静默回退会让人以为省着钱呢
                logger.warning(f"[AstrLover] 轻量模型不可用，这次退回会话模型：{e}")
        return await self._call(self.chat_provider(), prompt, system_prompt)

    async def light_json(self, prompt: str, system_prompt: str | None = None):
        """要求轻模型输出 JSON；解析失败自动补救一次，仍失败返回 None。"""
        sp = (system_prompt or "") + "\n只输出合法 JSON，不要解释，不要代码块围栏。"
        for attempt in range(2):
            try:
                parsed = self.extract_json(await self.light(prompt, sp))
                if parsed is not None:
                    return parsed
            except Exception as e:
                logger.warning(f"[AstrLover] light_json 调用失败（第{attempt + 1}次）：{e}")
            prompt = "你上次的输出不是合法 JSON。重新只输出 JSON。\n" + prompt
        return None

    @staticmethod
    def extract_json(raw: str):
        raw = raw.strip()
        # 剥掉可能的 ```json 围栏
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw, flags=re.S).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = _JSON_RE.search(raw)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
        return None
