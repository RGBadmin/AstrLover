"""NanoBanana（Gemini 系生图）：默认首选后端。

支持参考图输入——把外观锚点图随提示词一起送入，
是三个后端里人物一致性（A9/R6）最稳的路径。
兼容官方 API 与 OpenAI 风格中转的 Gemini 原生转发。
"""

import base64
from pathlib import Path

import aiohttp

from .base import ImageBackend
from .prompt_builder import PromptSpec


class NanoBananaBackend(ImageBackend):
    name = "nanobanana"

    def configured(self) -> bool:
        return bool(self.conf.get("api_key"))

    async def generate(self, spec: PromptSpec) -> bytes:
        base_url = str(self.conf.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
        model = str(self.conf.get("model") or "gemini-2.5-flash-image")
        key = str(self.conf.get("api_key"))

        parts: list[dict] = []
        for ref in spec.reference_images:
            p = Path(ref)
            if not p.exists():
                continue
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            parts.append({
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(p.read_bytes()).decode(),
                }
            })
        # 这个接口没有宽高参数，只能把画幅写进提示词——不保证严格遵守，
        # 但比什么都不说强
        prompt = f"{spec.positive}\n\n画幅：{spec.width}x{spec.height}"
        if spec.reference_images:
            prompt = "以附带图片中的人物为同一人（保持长相一致），生成新照片。" + prompt
        prompt += f"。避免：{spec.negative}"
        parts.append({"text": prompt})

        url = f"{base_url}/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                data = await resp.json()

        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
        raise RuntimeError("响应中没有图片数据")
