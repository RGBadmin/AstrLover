"""R3 语音：TTS 出去是原生语音条，STT 进来能听懂。

- TTS 经 AstrBot Provider 体系（GPT-SoVITS/CosyVoice/Fish Audio…可替换不锁定）；
- Telegram 语音条要求 ogg/opus：用 ffmpeg 转码（官方镜像自带）；
  没有 ffmpeg 时退回原始音频文件并告警（会显示为文件而非波形条）；
- STT 同理走 Provider；失败返回 None，由对话层自然圆场（"没听清"）。
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

    def _stt_provider(self):
        cfg_id = self.app.cfg.stt_provider_id
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
    # STT
    # ------------------------------------------------------------------
    async def transcribe(self, record: "Comp.Record") -> str | None:
        provider = self._stt_provider()
        if provider is None:
            return None
        source = (
            getattr(record, "path", None)
            or getattr(record, "file", None)
            or getattr(record, "url", None)
        )
        if not source:
            return None
        try:
            # 本地 ogg 先转 wav，提高各家 STT 兼容性
            if self.ffmpeg and isinstance(source, str) and Path(source).exists() and not source.lower().endswith(".wav"):
                wav = self.app.voice_dir / f"{uuid.uuid4().hex}.wav"
                proc = await asyncio.create_subprocess_exec(
                    self.ffmpeg, "-y", "-i", source, "-ar", "16000", "-ac", "1", str(wav),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=60)
                if proc.returncode == 0 and wav.exists():
                    source = str(wav)
            text = await provider.get_text(source)
            return text.strip() if text else None
        except Exception as e:
            logger.warning(f"[AstrLover] STT 失败：{e}")
            return None

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
