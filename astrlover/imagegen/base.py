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
        """主用一个、备用一个。主的挂了才走备的，没有第三层。

        以前是一串优先顺序 + 每个后端各一套配置，配起来要在好几处对照；
        实际用起来永远只关心"主用哪个、坏了退到哪个"，两槽就够。
        """
        from .api import ApiBackend
        from .comfyui import ComfyUIBackend
        from .novelai import NovelAIBackend

        registry = {"api": ApiBackend, "comfyui": ComfyUIBackend, "novelai": NovelAIBackend}
        for slot, conf in self.app.cfg.imagegen_slots():
            cls = registry.get(str(conf.get("type", "")).strip().lower())
            if cls is None:
                continue
            backend = cls(conf)
            backend.slot = slot
            if cls is ComfyUIBackend:
                backend.data_dir = self.app.data_dir  # workflow 文件在数据目录
            if backend.configured():
                self.backends.append(backend)
        if self.backends:
            logger.info("[AstrLover] 生图后端就绪：" + " → ".join(
                f"{b.slot}={b.name}" for b in self.backends))

    @property
    def available(self) -> bool:
        return bool(self.backends)

    async def generate(self, situation: str) -> str | None:
        """按情境需求生成"照片"，保存后返回文件路径。

        外观锚点图放 data/plugin_data/astrlover/persona/anchors/ 下
        （挑几张最能代表她长相的照片，支持参考图的后端以此保证同一个人）。
        产物落到 presence 相册目录（若已配置）的 aiimages/ 子目录，
        之后 /gallery scan + index 即回流成她相册的一部分。
        """
        if not self.backends:
            return None
        app = self.app
        anchors_dir = app.data_dir / "anchors"
        anchor_paths = [
            str(p) for p in sorted(anchors_dir.glob("*"))
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ][:2] if anchors_dir.exists() else []
        spec = await build_spec(app, situation, anchor_paths)

        # 优先落到 presence 相册目录，回流后可被她自己检索到
        album_dir = str(app.conf.get("gallery_dir") or "").strip()
        if album_dir and Path(album_dir).is_dir():
            out_root = Path(album_dir) / "aiimages"  # 回流：scan+index 后成为她相册的一部分
        else:
            out_root = app.gallery_dir

        for backend in self.backends:
            try:
                data = await backend.generate(spec)
                if not data:
                    continue
                out_dir = out_root / time.strftime("%Y%m")
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / f"{uuid.uuid4().hex}.png"
                out.write_bytes(data)
                logger.info(f"[AstrLover] {backend.name} 生图成功：{out.name}")
                return str(out)
            except Exception as e:
                logger.warning(f"[AstrLover] {backend.name} 生图失败，尝试下一后端：{e}")
        return None
