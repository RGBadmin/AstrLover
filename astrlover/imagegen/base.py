"""生图后端体系（R6）：可替换、可并存、按优先级降级。"""

import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from astrbot.api import logger

from .prompt_builder import PromptSpec, build_spec


class ImageBackend(ABC):
    name = "base"

    def __init__(self, conf: dict):
        self.conf = conf or {}

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def generate(self, spec: PromptSpec) -> bytes:
        """返回图片字节；失败抛异常。"""
        ...


class ImageGen:
    def __init__(self, app):
        self.app = app
        self.backends: list[ImageBackend] = []
        self._build()

    def _build(self):
        from .comfyui import ComfyUIBackend
        from .nanobanana import NanoBananaBackend
        from .novelai import NovelAIBackend

        registry = {
            "nanobanana": NanoBananaBackend,
            "comfyui": ComfyUIBackend,
            "novelai": NovelAIBackend,
        }
        for name in self.app.cfg.imagegen_order:
            cls = registry.get(name)
            if cls is None:
                continue
            backend = cls(self.app.cfg.imagegen_backend(name))
            if name == "comfyui":
                backend.data_dir = self.app.data_dir  # workflow 文件在数据目录
            if backend.configured():
                self.backends.append(backend)
        if self.backends:
            logger.info(
                "[AstrLover] 生图后端就绪："
                + " → ".join(b.name for b in self.backends)
            )

    @property
    def available(self) -> bool:
        return bool(self.backends)

    async def generate(self, situation: str) -> str | None:
        """按情境需求生成"照片"，保存到图库目录，返回文件路径。"""
        if not self.backends:
            return None
        app = self.app
        anchor_paths = []
        for row in await app.dao.anchors():
            p = app.data_dir / row["file"]
            if p.exists():
                anchor_paths.append(str(p))
        spec = build_spec(app.profile, app.dynamic, situation, anchor_paths)

        for backend in self.backends:
            try:
                data = await backend.generate(spec)
                if not data:
                    continue
                out_dir = app.gallery_dir / "gen" / time.strftime("%Y%m")
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / f"{uuid.uuid4().hex}.png"
                out.write_bytes(data)
                logger.info(f"[AstrLover] {backend.name} 生图成功：{out.name}")
                return str(out)
            except Exception as e:
                logger.warning(f"[AstrLover] {backend.name} 生图失败，尝试下一后端：{e}")
        return None
