"""自管的轻量文本模型客户端。

跟视觉、向量一样：地址、Key、模型在插件自己的设置里，报错也落在插件
自己的日志和面板上，不去 AstrBot 的供应商注册表里按 id 找。

留空则回退到**会话当前模型**——那是对话管线本来就在用的那个，
不是另一套依赖；不配也能跑，只是这些杂活也走大模型、贵一点。

三种格式的请求体、鉴权头、取正文方式完全不同，跟视觉那边同一套道理：
  openai     POST {base}/chat/completions        Authorization: Bearer
  anthropic  POST {base}/v1/messages             x-api-key + anthropic-version
  gemini     POST {base}/v1beta/models/{m}:generateContent   x-goog-api-key
"""

import json

import aiohttp

ANTHROPIC_VERSION = "2023-06-01"


class LightError(Exception):
    """配置或上游出了问题。调用方自己决定要不要回退。"""


class LightClient:
    def __init__(self, conf):
        self.conf = conf

    # ------------------------------------------------------------------ 配置
    @property
    def fmt(self) -> str:
        return str(self.conf.get("light_api_format") or "openai").lower()

    @property
    def base_url(self) -> str:
        return str(self.conf.get("light_base_url") or "").strip().rstrip("/")

    @property
    def api_key(self) -> str:
        return str(self.conf.get("light_api_key") or "").strip()

    @property
    def model(self) -> str:
        return str(self.conf.get("light_model") or "").strip()

    @property
    def max_tokens(self) -> int:
        try:
            return max(64, int(self.conf.get("light_max_tokens", 1024) or 1024))
        except (TypeError, ValueError):
            return 1024

    @property
    def timeout(self) -> int:
        try:
            return max(5, int(self.conf.get("light_timeout", 60) or 60))
        except (TypeError, ValueError):
            return 60

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def describe(self) -> str:
        return f"{self.fmt} · {self.model}" if self.configured else "未配置"

    # ------------------------------------------------------------------ 传输
    def _url(self) -> str:
        if self.fmt == "anthropic":
            return f"{self.base_url}/v1/messages"
        if self.fmt == "gemini":
            return f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.fmt == "anthropic":
            h["x-api-key"] = self.api_key
            h["anthropic-version"] = ANTHROPIC_VERSION
        elif self.fmt == "gemini":
            h["x-goog-api-key"] = self.api_key
        else:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _payload(self, prompt: str, system_prompt: str) -> dict:
        if self.fmt == "anthropic":
            body = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                body["system"] = system_prompt
            return body
        if self.fmt == "gemini":
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": self.max_tokens},
            }
            if system_prompt:
                body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
            return body
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}

    @staticmethod
    def _text(fmt: str, data: dict) -> str:
        if fmt == "anthropic":
            return "".join(
                b.get("text", "") for b in (data.get("content") or [])
                if b.get("type", "text") == "text"
            ).strip()
        if fmt == "gemini":
            cands = data.get("candidates") or []
            if not cands:
                # 输入侧被判死：HTTP 200、正文空着，照常计费
                reason = (data.get("promptFeedback") or {}).get("blockReason")
                raise LightError(f"没有候选内容{f'（blockReason={reason}）' if reason else ''}")
            parts = ((cands[0].get("content") or {}).get("parts") or [])
            text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
            if not text and (fr := cands[0].get("finishReason")):
                raise LightError(f"正文为空（finishReason={fr}）")
            return text
        choices = data.get("choices") or []
        if not choices:
            raise LightError("没有 choices")
        return str((choices[0].get("message") or {}).get("content") or "").strip()

    async def chat(self, prompt: str, system_prompt: str = "") -> str:
        if not self.configured:
            raise LightError("轻量模型没配全（地址 / Key / 模型三项都要填）")
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(
                    self._url(), headers=self._headers(),
                    json=self._payload(prompt, system_prompt or ""),
                ) as r:
                    raw = await r.text()
                    if r.status != 200:
                        raise LightError(f"HTTP {r.status}：{raw[:200]}")
                    data = json.loads(raw)
        except LightError:
            raise
        except Exception as e:
            raise LightError(f"{type(e).__name__}: {e}") from e
        text = self._text(self.fmt, data)
        if not text:
            raise LightError("模型返回了空内容")
        return text
