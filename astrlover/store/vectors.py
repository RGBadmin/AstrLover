"""向量检索封装：memory（记忆召回）与 gallery（图库检索）两个 FAISS 库。

复用 AstrBot 内置的 FaissVecDB 与 Embedding Provider（供应商中立）。
Embedding 未配置或初始化失败时优雅降级：available=False，
上层改用"最近优先/标签匹配"等非语义策略，功能不崩。
"""

import uuid
from pathlib import Path

from astrbot.api import logger


class Vectors:
    def __init__(self, vec_dir: Path, context, embedding_provider_id: str):
        self.vec_dir = vec_dir
        self.context = context
        self.embedding_provider_id = embedding_provider_id
        self.memory = None   # FaissVecDB
        self.gallery = None  # FaissVecDB
        self.available = False
        self._init_failed = False

    async def ensure(self) -> bool:
        """惰性初始化；失败只报一次错，之后静默降级。"""
        if self.available:
            return True
        if self._init_failed:
            return False
        try:
            provider = None
            if self.embedding_provider_id:
                provider = self.context.get_provider_by_id(self.embedding_provider_id)
            if provider is None:
                providers = self.context.get_all_embedding_providers()
                provider = providers[0] if providers else None
            if provider is None:
                raise RuntimeError("未找到可用的 Embedding Provider")

            from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

            self.vec_dir.mkdir(parents=True, exist_ok=True)
            self.memory = FaissVecDB(
                str(self.vec_dir / "memory_docs.db"),
                str(self.vec_dir / "memory.index"),
                provider,
            )
            await self.memory.initialize()
            self.gallery = FaissVecDB(
                str(self.vec_dir / "gallery_docs.db"),
                str(self.vec_dir / "gallery.index"),
                provider,
            )
            await self.gallery.initialize()
            self.available = True
            logger.info("[AstrLover] 向量库就绪（memory + gallery）。")
            return True
        except Exception as e:
            self._init_failed = True
            logger.warning(
                f"[AstrLover] 向量库初始化失败，将降级为非语义检索：{e}"
            )
            return False

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
        """返回 [{text, meta, similarity}]，失败返回 []。"""
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

    # ---- gallery 库 ----
    async def add_gallery(self, text: str, meta: dict) -> str:
        if not await self.ensure():
            return ""
        vid = str(uuid.uuid4())
        try:
            await self.gallery.insert(content=text, metadata=meta, id=vid)
            return vid
        except Exception as e:
            logger.warning(f"[AstrLover] 图库向量写入失败：{e}")
            return ""

    async def search_gallery(self, query: str, k: int = 5, filters: dict | None = None) -> list[dict]:
        if not await self.ensure():
            return []
        try:
            results = await self.gallery.retrieve(
                query=query, k=k, fetch_k=max(20, k * 4), metadata_filters=filters
            )
            return [self._unpack(r) for r in results]
        except Exception as e:
            logger.warning(f"[AstrLover] 图库向量检索失败：{e}")
            return []

    async def delete_gallery_doc(self, vec_id: str):
        if self.available and vec_id:
            try:
                await self.gallery.delete(vec_id)
            except Exception as e:
                logger.warning(f"[AstrLover] 图库向量删除失败：{e}")

    @staticmethod
    def _unpack(result) -> dict:
        data = result.data or {}
        return {
            "text": data.get("text", ""),
            "meta": data.get("metadata", {}) or {},
            "similarity": float(result.similarity),
        }

    async def close(self):
        for db in (self.memory, self.gallery):
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    pass
