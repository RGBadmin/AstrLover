"""NovelAI 后端。响应为 ZIP 包，取第一张图。

v4 系模型自动附加 v4_prompt 结构；一致性弱于前两个后端，
建议在 backend_order 中作为兜底。
"""

import io
import random
import zipfile

import aiohttp

from .base import ImageBackend
from .prompt_builder import PromptSpec

_API = "https://image.novelai.net/ai/generate-image"

# NAI 官方 UC（undesired content）那一档的常用集合。中文负面词它同样读不懂，
# 用标签模式时就该换成这套。
_UC = (
    "lowres, worst quality, bad quality, jpeg artifacts, watermark, signature, "
    "text, logo, bad anatomy, bad hands, extra digits, fewer digits, "
    "missing fingers, artistic error, scan artifacts, sketch, lineart, "
    "monochrome, greyscale, unfinished"
)


class NovelAIBackend(ImageBackend):
    name = "novelai"

    def configured(self) -> bool:
        return bool(self.conf.get("api_key"))

    def _steps(self) -> int:
        """步数越多越细也越慢越贵；NovelAI 超过 28 收益已经很小。"""
        try:
            return max(1, min(50, int(self.conf.get("steps", 24) or 24)))
        except (TypeError, ValueError):
            return 24

    async def generate(self, spec: PromptSpec) -> bytes:
        model = str(self.conf.get("model") or "nai-diffusion-4-5-full")
        seed = random.randint(0, 2**32 - 1)
        # 这个模型只认 danbooru 英文标签。喂中文摄影稿等于喂噪声——
        # 它读不懂就退回自己的先验：一个站着的动漫女孩、纯白背景、线稿感。
        positive = spec.tags.strip() or spec.positive
        negative = _UC if spec.tags.strip() else spec.negative
        params: dict = {
            "negative_prompt": negative,
            "width": spec.width,
            "height": spec.height,
            "steps": self._steps(),
            "scale": 5.5,
            "sampler": "k_euler_ancestral",
            "seed": seed,
            "n_samples": 1,
            "qualityToggle": True,
            "ucPreset": 0,
        }
        if model.startswith("nai-diffusion-4"):
            params["v4_prompt"] = {
                "caption": {"base_caption": positive, "char_captions": []},
                "use_coords": False,
                "use_order": True,
            }
            params["v4_negative_prompt"] = {
                "caption": {"base_caption": negative, "char_captions": []},
            }
        payload = {
            "input": positive,
            "model": model,
            "action": "generate",
            "parameters": params,
        }
        headers = {"Authorization": f"Bearer {self.conf.get('api_key')}"}
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(_API, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                data = await resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError("返回的 ZIP 为空")
            return zf.read(names[0])
