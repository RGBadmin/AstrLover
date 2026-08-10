"""API 类型的生图后端：协议由**你填的地址**决定，不猜。

地址要写完整的端点，写什么就发什么。三种协议按地址里的特征识别：

    …/v1/chat/completions                      openai   图在 message.images[]
    …/v1beta/models/{模型}:generateContent      gemini   图在 candidates[].parts[].inlineData
    …/v1/images/generations                    grok     图在 data[].b64_json / url

为什么不做自动补路径：中转站各开各的，同一个域名下三种端点可能都在、
也可能只开一个，补错了就是一个看不懂的 404。填全了反而最省事。

几个各家的坑，都在下面的实现里：
- Gemini 的画幅只能用顶层 `generationConfig.imageConfig.aspectRatio`，
  写进提示词文字会被静默忽略（不报错、图照出、尺寸不对）。
- Gemini chat 接口的 `message.content` 恒为 null，图只在 `images` 里。
- 原生格式的 `inlineData.data` 是纯 base64，没有 `data:` 前缀。
"""

import base64
import json
from pathlib import Path

import aiohttp

from .base import ImageBackend
from .prompt_builder import PromptSpec

# 生图接口只认宽高比，不认像素
_ASPECT = {"portrait": "3:4", "landscape": "4:3", "square": "1:1"}
_SIZES = ("1K", "2K", "4K")


class ProtocolError(RuntimeError):
    """地址认不出是哪种协议。"""


def detect(url: str) -> str:
    """从地址认协议。认不出就报错，不猜。"""
    u = (url or "").lower()
    if ":generatecontent" in u or "/v1beta/" in u:
        return "gemini"
    if "/images/generations" in u or "/images/edits" in u:
        return "grok"
    if "/chat/completions" in u:
        return "openai"
    raise ProtocolError(
        f"认不出这个地址是哪种协议：{url}\n"
        "要写完整端点，三选一：\n"
        "  https://xxx/v1/chat/completions                          （openai 兼容）\n"
        "  https://xxx/v1beta/models/你的模型:generateContent        （gemini 原生）\n"
        "  https://xxx/v1/images/generations                        （grok / DALL·E 风格）"
    )


class ApiBackend(ImageBackend):
    name = "API"

    def configured(self) -> bool:
        return bool(self.conf.get("api_key") and self.conf.get("url"))

    # ------------------------------------------------------------------ 配置
    @property
    def url(self) -> str:
        return str(self.conf.get("url") or "").strip()

    @property
    def model(self) -> str:
        return str(self.conf.get("model") or "").strip()

    @property
    def image_size(self) -> str:
        s = str(self.conf.get("image_size") or "1K").strip().upper()
        return s if s in _SIZES else "1K"

    def _aspect(self, spec: PromptSpec) -> str:
        return _ASPECT.get(spec.orientation, "3:4")

    def _text(self, spec: PromptSpec) -> str:
        text = spec.positive
        if spec.reference_images:
            text = "以附带图片中的人物为同一人（保持长相一致），生成新照片。\n" + text
        if spec.negative:
            text += f"\n\n避免出现：{spec.negative}"
        return text

    @staticmethod
    def _data_uri(path: str) -> str | None:
        p = Path(path)
        if not p.exists():
            return None
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"

    # ------------------------------------------------------------------ 三种协议
    def _payload_openai(self, spec: PromptSpec) -> tuple[dict, dict]:
        content: list[dict] = [{"type": "text", "text": self._text(spec)}]
        for ref in spec.reference_images:
            if uri := self._data_uri(ref):
                content.append({"type": "image_url", "image_url": {"url": uri}})
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            # 只有顶层 generationConfig 生效；挪进 extra_body 或写成 size 都会被忽略
            "generationConfig": {"imageConfig": {
                "aspectRatio": self._aspect(spec), "imageSize": self.image_size,
            }},
        }
        return payload, {"Authorization": f"Bearer {self.conf.get('api_key')}"}

    def _payload_gemini(self, spec: PromptSpec) -> tuple[dict, dict]:
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
                "imageConfig": {"aspectRatio": self._aspect(spec),
                                "imageSize": self.image_size},
            },
        }
        return payload, {"x-goog-api-key": str(self.conf.get("api_key"))}

    def _payload_grok(self, spec: PromptSpec) -> tuple[dict, dict]:
        payload = {
            "model": self.model,
            "prompt": self._text(spec),
            "n": 1,
            "response_format": "b64_json",
        }
        return payload, {"Authorization": f"Bearer {self.conf.get('api_key')}"}

    # ------------------------------------------------------------------ 取图
    @staticmethod
    def _from_uri(url: str) -> bytes | None:
        if isinstance(url, str) and url.startswith("data:"):
            _, _, b64 = url.partition(",")
            if b64:
                return base64.b64decode(b64)
        return None

    @classmethod
    def _pick_openai(cls, data: dict) -> bytes:
        for choice in data.get("choices") or []:
            msg = choice.get("message") or choice.get("delta") or {}
            # 图在 images 里；content 恒为 null，去那儿找永远找不到
            for img in msg.get("images") or []:
                if isinstance(img, dict):
                    if raw := cls._from_uri((img.get("image_url") or {}).get("url", "")):
                        return raw
            body = msg.get("content")
            if isinstance(body, list):
                for part in body:
                    if isinstance(part, dict):
                        if raw := cls._from_uri((part.get("image_url") or {}).get("url", "")):
                            return raw
            elif isinstance(body, str) and "data:image" in body:
                start = body.index("data:image")
                end = min([x for x in
                           (body.find(c, start) for c in (")", " ", "\n", '"'))
                           if x != -1] or [len(body)])
                if raw := cls._from_uri(body[start:end]):
                    return raw
        raise RuntimeError(f"响应里没有图片：{str(data)[:200]}")

    @staticmethod
    def _pick_gemini(data: dict) -> bytes:
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if b64 := inline.get("data"):
                    return base64.b64decode(b64)   # 纯 base64，没有 data: 前缀
        raise RuntimeError(f"响应里没有图片：{str(data)[:200]}")

    @classmethod
    def _pick_grok(cls, data: dict) -> bytes:
        for item in data.get("data") or []:
            if b64 := item.get("b64_json"):
                return base64.b64decode(b64)
            if raw := cls._from_uri(item.get("url") or ""):
                return raw
        raise RuntimeError(f"响应里没有图片：{str(data)[:200]}")

    # ------------------------------------------------------------------
    async def generate(self, spec: PromptSpec) -> bytes:
        proto = detect(self.url)          # 认不出直接抛，不去试
        payload, headers = {
            "openai": self._payload_openai,
            "gemini": self._payload_gemini,
            "grok": self._payload_grok,
        }[proto](spec)
        headers["Content-Type"] = "application/json"

        # 4K 响应体能到 6.5MB，超时给够
        timeout = aiohttp.ClientTimeout(total=300 if self.image_size == "4K" else 180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url, json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"HTTP {resp.status}（{proto} · POST {self.url}）：{body[:200]}"
                    )
                data = json.loads(body)
        return {"openai": self._pick_openai, "gemini": self._pick_gemini,
                "grok": self._pick_grok}[proto](data)
