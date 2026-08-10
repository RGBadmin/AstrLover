"""NanoBanana（Gemini 系生图）：支持参考图，人物一致性最稳的后端。

两种协议都支持，默认 auto——先试 OpenAI 兼容的 chat 接口（多数中转站
只开这条，key 是 `sk-` 开头的基本都是），失败再试 Gemini 原生。

几个踩过的坑，都写在这儿免得再犯：
- **画幅只能用 aspectRatio**，而且只有顶层 `generationConfig.imageConfig`
  生效。把 "832x1216" 写进提示词文字是没用的——网关会静默忽略，
  不报错，图照出，就是尺寸不对。
- **图在 `message.images`，不在 `message.content`**；content 恒为 null，
  去 content 里找图永远找不到。
- 原生格式那边是 `candidates[].content.parts[].inlineData.data`，
  纯 base64 没有 `data:` 前缀。
- 输出固定 JPEG，不支持 n>1，`/v1/images/generations` 这类端点不支持。
- 4K 响应体能到 6.5 MB，超时要给够。
"""

import base64
from pathlib import Path

import aiohttp

from .base import ImageBackend
from .prompt_builder import PromptSpec

# 生图接口只认宽高比，不认像素；挑最接近插件那三个规格的
_ASPECT = {"portrait": "3:4", "landscape": "4:3", "square": "1:1"}
_SIZES = ("1K", "2K", "4K")


class NanoBananaBackend(ImageBackend):
    name = "nanobanana"

    def configured(self) -> bool:
        return bool(self.conf.get("api_key"))

    # ------------------------------------------------------------------ 配置
    @property
    def base(self) -> str:
        return str(self.conf.get("base_url")
                   or "https://generativelanguage.googleapis.com").strip().rstrip("/")

    @property
    def model(self) -> str:
        return str(self.conf.get("model") or "gemini-2.5-flash-image").strip()

    @property
    def fmt(self) -> str:
        f = str(self.conf.get("format") or "auto").strip().lower()
        return f if f in ("auto", "openai", "gemini") else "auto"

    @property
    def image_size(self) -> str:
        s = str(self.conf.get("image_size") or "1K").strip().upper()
        return s if s in _SIZES else "1K"

    def _image_config(self, spec: PromptSpec) -> dict:
        return {"aspectRatio": _ASPECT.get(spec.orientation, "3:4"),
                "imageSize": self.image_size}

    # ------------------------------------------------------------------ 地址
    def _chat_url(self) -> str:
        b = self.base
        if b.endswith("/chat/completions"):
            return b
        return b + ("/chat/completions" if b.rsplit("/", 1)[-1].startswith("v1")
                    else "/v1/chat/completions")

    def _native_url(self) -> str:
        b = self.base
        if ":generateContent" in b:
            return b
        head = b if b.rsplit("/", 1)[-1].startswith("v1") else b + "/v1beta"
        return f"{head}/models/{self.model}:generateContent"

    # ------------------------------------------------------------------ 素材
    @staticmethod
    def _data_uri(path: str) -> str | None:
        p = Path(path)
        if not p.exists():
            return None
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"

    def _text(self, spec: PromptSpec) -> str:
        text = spec.positive
        if spec.reference_images:
            text = "以附带图片中的人物为同一人（保持长相一致），生成新照片。\n" + text
        if spec.negative:
            text += f"\n\n避免出现：{spec.negative}"
        return text

    # ------------------------------------------------------------------ 请求
    async def _post(self, session, url: str, payload: dict, headers: dict) -> dict:
        async with session.post(url, json=payload, headers=headers) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}（POST {url}）：{body[:200]}")
            import json

            return json.loads(body)

    async def _chat(self, session, spec: PromptSpec) -> bytes:
        content: list[dict] = [{"type": "text", "text": self._text(spec)}]
        for ref in spec.reference_images:
            if uri := self._data_uri(ref):
                content.append({"type": "image_url", "image_url": {"url": uri}})
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            # 只有顶层 generationConfig 生效，别挪进 extra_body 或写成 size
            "generationConfig": {"imageConfig": self._image_config(spec)},
        }
        headers = {"Authorization": f"Bearer {self.conf.get('api_key')}",
                   "Content-Type": "application/json"}
        data = await self._post(session, self._chat_url(), payload, headers)
        return self._pick_from_chat(data)

    async def _native(self, session, spec: PromptSpec) -> bytes:
        parts: list[dict] = []
        for ref in spec.reference_images:
            p = Path(ref)
            if not p.exists():
                continue
            parts.append({"inline_data": {
                "mime_type": "image/png" if p.suffix.lower() == ".png" else "image/jpeg",
                "data": base64.b64encode(p.read_bytes()).decode(),
            }})
        parts.append({"text": self._text(spec)})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": self._image_config(spec),
            },
        }
        headers = {"x-goog-api-key": str(self.conf.get("api_key")),
                   "Content-Type": "application/json"}
        data = await self._post(session, self._native_url(), payload, headers)
        return self._pick_from_native(data)

    # ------------------------------------------------------------------ 取图
    @staticmethod
    def _decode(url: str) -> bytes | None:
        if not isinstance(url, str) or not url:
            return None
        if url.startswith("data:"):
            _, _, b64 = url.partition(",")
            return base64.b64decode(b64) if b64 else None
        return None

    @classmethod
    def _pick_from_chat(cls, data: dict) -> bytes:
        for choice in data.get("choices") or []:
            msg = choice.get("message") or choice.get("delta") or {}
            # 图在 images 里，content 恒为 null——别去 content 找
            for img in msg.get("images") or []:
                url = (img.get("image_url") or {}).get("url") if isinstance(img, dict) else None
                if raw := cls._decode(url or ""):
                    return raw
            # 别的中转把图塞在 content 里，顺手也认一下
            body = msg.get("content")
            if isinstance(body, list):
                for part in body:
                    if isinstance(part, dict):
                        url = (part.get("image_url") or {}).get("url", "")
                        if raw := cls._decode(url):
                            return raw
            elif isinstance(body, str) and "data:image" in body:
                start = body.index("data:image")
                end = len(body)
                for stop in (")", " ", "\n", '"'):
                    at = body.find(stop, start)
                    if at != -1:
                        end = min(end, at)
                if raw := cls._decode(body[start:end]):
                    return raw
        raise RuntimeError(f"chat 响应里没有图片：{str(data)[:200]}")

    @staticmethod
    def _pick_from_native(data: dict) -> bytes:
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if b64 := inline.get("data"):
                    return base64.b64decode(b64)   # 纯 base64，没有 data: 前缀
        raise RuntimeError(f"原生响应里没有图片：{str(data)[:200]}")

    # ------------------------------------------------------------------
    async def generate(self, spec: PromptSpec) -> bytes:
        # 4K 响应体能到 6.5MB，超时给够
        timeout = aiohttp.ClientTimeout(total=300 if self.image_size == "4K" else 180)
        # 别拿 `fn is self._chat` 判断——绑定方法每次访问都是新对象，永远不相等
        chain = {
            "openai": (("chat", self._chat),),
            "gemini": (("native", self._native),),
        }.get(self.fmt, (("chat", self._chat), ("native", self._native)))
        errors = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for label, fn in chain:
                try:
                    return await fn(session, spec)
                except Exception as e:
                    errors.append(f"{label}: {e}")
        raise RuntimeError("；".join(errors))
