"""向量检索：memory（她的记忆）与 album（相册四段）两个 FAISS 库。

复用 AstrBot 内置 FaissVecDB 与 Embedding Provider（供应商中立）。
Embedding 未配置或初始化失败时优雅降级：available=False，
上层改用词面检索/最近优先，功能不崩。
"""

import shutil
import uuid
from pathlib import Path

from astrbot.api import logger


class Vectors:
    def __init__(self, vec_dir: Path, context, embedding_provider_id: str):
        self.vec_dir = vec_dir
        self.context = context
        self.embedding_provider_id = embedding_provider_id
        self.memory = None   # FaissVecDB
        self.album = None    # FaissVecDB
        self.provider = None
        self.available = False
        self._init_failed = False

    # ------------------------------------------------------------------
    def _resolve_provider(self):
        provider = None
        if self.embedding_provider_id:
            try:
                provider = self.context.get_provider_by_id(self.embedding_provider_id)
            except Exception:
                provider = None
        if provider is None:
            try:
                providers = self.context.get_all_embedding_providers()
                provider = providers[0] if providers else None
            except Exception:
                provider = None
        return provider

    async def ensure(self) -> bool:
        """惰性初始化；失败只报一次错，之后静默降级。"""
        if self.available:
            return True
        if self._init_failed:
            return False
        try:
            provider = self._resolve_provider()
            if provider is None:
                raise RuntimeError("未找到可用的 Embedding Provider")
            self.provider = provider

            from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

            self.vec_dir.mkdir(parents=True, exist_ok=True)
            self.memory = FaissVecDB(
                str(self.vec_dir / "memory_docs.db"),
                str(self.vec_dir / "memory.index"),
                provider,
            )
            await self.memory.initialize()
            self.album = FaissVecDB(
                str(self.vec_dir / "album_docs.db"),
                str(self.vec_dir / "album.index"),
                provider,
            )
            await self.album.initialize()
            self.available = True
            logger.info("[AstrLover] 向量库就绪（memory + album）。")
            return True
        except Exception as e:
            self._init_failed = True
            logger.warning(f"[AstrLover] 向量库初始化失败，将降级为非语义检索：{e}")
            return False

    async def embed_text(self, text: str) -> list[float]:
        """裸向量（探区分度用）。"""
        if not await self.ensure():
            raise RuntimeError("Embedding 不可用")
        return await self.provider.get_embedding(text)

    # ---- memory 库 ----
    async def add_memory(self, text: str, meta: dict) -> str:
        if not await self.ensure():
            return ""
        vid = str(uuid.uuid4())
        try:
            await self.memory.insert(content=text, metadata=meta, id=vid)
            return vid
        except Exception as e:
            logger.warning(f"[AstrLover] 记忆向量写入失败：{e}")
            return ""

    async def search_memory(self, query: str, k: int = 5, filters: dict | None = None) -> list[dict]:
        if not await self.ensure():
            return []
        try:
            results = await self.memory.retrieve(
                query=query, k=k, fetch_k=max(20, k * 4), metadata_filters=filters
            )
            return [self._unpack(r) for r in results]
        except Exception as e:
            logger.warning(f"[AstrLover] 记忆向量检索失败：{e}")
            return []

    # ---- album 库（一张图四段，meta={img, seg}）----
    async def add_album_segment(self, text: str, meta: dict) -> str:
        if not await self.ensure():
            raise RuntimeError("Embedding 不可用")
        vid = str(uuid.uuid4())
        await self.album.insert(content=text, metadata=meta, id=vid)
        return vid

    async def search_album(self, query: str, k: int = 60) -> list[dict]:
        if not await self.ensure():
            return []
        try:
            results = await self.album.retrieve(query=query, k=k, fetch_k=max(80, k * 2))
            return [self._unpack(r) for r in results]
        except Exception as e:
            logger.warning(f"[AstrLover] 相册向量检索失败：{e}")
            return []

    async def rebuild_album(self):
        """清空相册向量库（换模型/换维度后重建）。"""
        try:
            if self.album is not None:
                await self.album.close()
        except Exception:
            pass
        self.album = None
        for name in ("album_docs.db", "album.index"):
            p = self.vec_dir / name
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
        self.available = False
        self._init_failed = False
        await self.ensure()

    @staticmethod
    def _unpack(result) -> dict:
        data = result.data or {}
        return {
            "text": data.get("text", ""),
            "meta": data.get("metadata", {}) or {},
            "similarity": float(result.similarity),
        }

    async def close(self):
        for db in (self.memory, self.album):
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    pass
