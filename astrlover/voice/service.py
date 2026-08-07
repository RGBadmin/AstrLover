"""R3 语音：TTS 出去是原生语音条，STT 进来能听懂。

- TTS 经 AstrBot Provider 体系（GPT-SoVITS/CosyVoice/Fish Audio…可替换不锁定）；
- Telegram 语音条要求 ogg/opus：用 ffmpeg 转码（官方镜像自带）；
  没有 ffmpeg 时退回原始音频文件并告警（会显示为文件而非波形条）；
- 听你的语音由 AstrBot 主管线负责（配 STT Provider 即可），插件不掺和。
"""

import asyncio
import shutil
import time
import uuid
from pathlib import Path

from astrbot.api import logger

import astrbot.api.message_components as Comp


class VoiceService:
    def __init__(self, app):
        self.app = app
        self.ffmpeg = shutil.which("ffmpeg")
        if not self.ffmpeg:
            logger.warning("[AstrLover] 未找到 ffmpeg：语音将以音频文件形式发送（非语音条）。")
        self._cleanup()

    # ------------------------------------------------------------------
    def _tts_provider(self):
        cfg_id = self.app.cfg.tts_provider_id
        if not cfg_id:
            return None
        try:
            return self.app.context.get_provider_by_id(cfg_id)
        except Exception:
            return None

    @property
    def tts_ready(self) -> bool:
        return self._tts_provider() is not None

    # ------------------------------------------------------------------
    # TTS → 语音条
    # ------------------------------------------------------------------
    async def tts_record(self, text: str) -> "Comp.Record | None":
        provider = self._tts_provider()
        if provider is None or not text.strip():
            return None
        try:
            audio_path = await provider.get_audio(text.strip())
            if not audio_path or not Path(audio_path).exists():
                raise RuntimeError("TTS 未产出音频文件")
            ogg = await self._to_ogg(audio_path)
            return Comp.Record(file=str(ogg), text=text)
        except Exception as e:
            logger.warning(f"[AstrLover] TTS 失败：{e}")
            return None

    async def _to_ogg(self, src: str) -> str:
        if not self.ffmpeg:
            return src
        if src.lower().endswith(".ogg"):
            return src
        out = self.app.voice_dir / f"{uuid.uuid4().hex}.ogg"
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-y", "-i", src,
            "-c:a", "libopus", "-b:a", "40k", "-ar", "48000", "-ac", "1",
            str(out),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=60)
        if proc.returncode != 0 or not out.exists():
            logger.warning("[AstrLover] ffmpeg 转码失败，退回原始音频。")
            return src
        return str(out)


    # ------------------------------------------------------------------
    def _cleanup(self, keep_days: int = 7):
        try:
            cutoff = time.time() - keep_days * 86400
            if self.app.voice_dir.exists():
                for p in self.app.voice_dir.iterdir():
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
        except Exception:
            pass
