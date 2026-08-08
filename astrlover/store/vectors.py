"""向量检索：memory（她的记忆）与 album（相册三段）两个 FAISS 库。

存储用 AstrBot 内置的 FaissVecDB，向量则来自插件自管的 EmbedClient——
地址/Key/模型都在插件设置里，不碰 AstrBot 的 Embedding Provider。
没配或连不上时优雅降级：available=False，上层改用词面检索/最近优先。

换了模型或维度必须重建：老向量是另一个空间里的坐标，混在一起检索
不会报错，只会安静地给出错的结果。所以把模型标识记在 vec/embed_meta.json，
对不上就整个清掉重来（相册会自动重跑索引，记忆随对话慢慢重新沉淀）。
分段方式改了同理，但那只作废相册——记忆不分段，不受影响。
"""

import json
import shutil
import uuid
from pathlib import Path

from astrbot.api import logger

from ..album.embed import SEGMENTS
from ..embed.client import EmbedClient

_META = "embed_meta.json"


class Vectors:
    def __init__(self, vec_dir: Path, conf):
        self.vec_dir = vec_dir
        self.client = EmbedClient(conf)
        self.memory = None   # FaissVecDB
        self.album = None    # FaissVecDB
        self.available = False
        self._init_failed = False
        self.last_error = ""     # 面板「测一下」要摊开真正的原因
        self.album_wiped = False  # 相册库被清过，DB 里的 embedded 标记要跟着清

    # ------------------------------------------------------------------
    def _read_meta(self) -> dict:
        try:
            return json.loads((self.vec_dir / _META).read_text("utf-8"))
        except Exception:
            return {}

    def _write_meta(self, signature: str):
        try:
            (self.vec_dir / _META).write_text(
                json.dumps({"signature": signature, "segments": list(SEGMENTS)},
                           ensure_ascii=False),
                "utf-8",
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 向量库标识写入失败：{e}")

    def _wipe(self, *names: str):
        for name in names:
            p = self.vec_dir / name
            if not p.exists():
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    async def ensure(self) -> bool:
        """惰性初始化；失败只报一次错，之后静默降级。"""
        if self.available:
            return True
        if self._init_failed:
            return False
        try:
            if not self.client.configured:
                raise RuntimeError("向量模型没配（面板「向量模型」组：地址 / Key / 模型）")
            self.vec_dir.mkdir(parents=True, exist_ok=True)
            await self.client.resolve_dim()      # 顺便验证配置是不是真能用

            signature = self.client.signature()
            meta = self._read_meta()
            if (old := meta.get("signature")) and old != signature:
                # 换了模型/维度：两个库都作废，旧向量是另一个空间的坐标
                logger.warning(
                    f"[AstrLover] 向量模型换了（{old} → {signature}），已清空重建。"
                )
                self._wipe("memory_docs.db", "memory.index", "album_docs.db", "album.index")
                self.album_wiped = True
            elif (segs := meta.get("segments")) and list(segs) != list(SEGMENTS):
                # 只改了分段方式：记忆库不受影响，只重建相册
                logger.warning(
                    f"[AstrLover] 相册分段方式换了（{segs} → {list(SEGMENTS)}），"
                    "相册向量已清空，会自动重转。"
                )
                self._wipe("album_docs.db", "album.index")
                self.album_wiped = True

            from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

            self.memory = FaissVecDB(
                str(self.vec_dir / "memory_docs.db"),
                str(self.vec_dir / "memory.index"),
                self.client,
            )
            await self.memory.initialize()
            self.album = FaissVecDB(
                str(self.vec_dir / "album_docs.db"),
                str(self.vec_dir / "album.index"),
                self.client,
            )
            await self.album.initialize()
            self._write_meta(signature)
            self.available = True
            logger.info("[AstrLover] 向量库就绪（memory + album）。")
            return True
        except Exception as e:
            self._init_failed = True
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"[AstrLover] 向量库初始化失败，将降级为非语义检索：{e}")
            return False

    def reset(self):
        """配置改过之后重新试一次——不然改对了也要等到重启才生效。"""
        self.available = False
        self._init_failed = False
        self.last_error = ""
        self.client = EmbedClient(self.client.conf)

    async def embed_text(self, text: str) -> list[float]:
        """裸向量（探区分度用）。"""
        if not await self.ensure():
            raise RuntimeError("向量模型不可用")
        return await self.client.get_embedding(text)

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

    # ---- album 库（一张图三段，meta={img, seg}）----
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
        """只清相册向量库（/gallery reindex 用），记忆库不动。"""
        try:
            if self.album is not None:
                await self.album.close()
        except Exception:
            pass
        self.album = None
        self._wipe("album_docs.db", "album.index")
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
