"""模型路由：主对话模型 / 轻量决策模型 / 视觉模型 的统一入口。

成本意识：对话走强模型；心跳决策、意图解析等高频调用走轻模型；
图库打标默认主模型读图、可切换独立 VLM。全部经 AstrBot Provider 体系（供应商中立）。
"""

import asyncio
import json
import re

from astrbot.api import logger

_JSON_RE = re.compile(r"[\[{].*[\]}]", re.S)


class LLM:
    def __init__(self, context, cfg):
        self.context = context
        self.cfg = cfg
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
        return self._by_id(self.cfg.chat_provider_id) or self._using()

    def light_provider(self):
        return self._by_id(self.cfg.light_provider_id) or self.chat_provider()

    def vlm_provider(self):
        return self._by_id(self.cfg.vlm_provider_id) or self.chat_provider()

    # ---- 调用 ----
    async def _call(
        self,
        provider,
        *,
        prompt: str | None = None,
        contexts: list[dict] | None = None,
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
        retries: int = 1,
    ) -> str:
        if provider is None:
            raise RuntimeError("没有可用的 LLM Provider（请检查模型配置）")
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await provider.text_chat(
                    prompt=prompt,
                    contexts=contexts,
                    system_prompt=system_prompt,
                    image_urls=image_urls,
                )
                text = getattr(resp, "completion_text", None) or ""
                if text.strip():
                    return text.strip()
                raise RuntimeError("模型返回了空内容")
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败：{last_err}")

    async def chat(
        self,
        *,
        prompt: str | None = None,
        contexts: list[dict] | None = None,
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
    ) -> str:
        return await self._call(
            self.chat_provider(),
            prompt=prompt,
            contexts=contexts,
            system_prompt=system_prompt,
            image_urls=image_urls,
        )

    async def light(self, prompt: str, system_prompt: str | None = None) -> str:
        return await self._call(self.light_provider(), prompt=prompt, system_prompt=system_prompt)

    async def light_json(self, prompt: str, system_prompt: str | None = None):
        """要求轻模型输出 JSON；解析失败自动补救一次，仍失败返回 None。"""
        sp = (system_prompt or "") + "\n只输出合法 JSON，不要解释，不要代码块围栏。"
        for attempt in range(2):
            try:
                raw = await self._call(self.light_provider(), prompt=prompt, system_prompt=sp)
                parsed = self.extract_json(raw)
                if parsed is not None:
                    return parsed
            except Exception as e:
                logger.warning(f"[AstrLover] light_json 调用失败（第{attempt + 1}次）：{e}")
            prompt = "你上次的输出不是合法 JSON。重新只输出 JSON。\n" + prompt
        return None

    async def vlm(self, prompt: str, image_path: str) -> str:
        return await self._call(self.vlm_provider(), prompt=prompt, image_urls=[image_path])

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
