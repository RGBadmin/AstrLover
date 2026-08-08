"""换了向量模型要自动重建向量库。

老向量是另一个空间里的坐标，混进新库检索不会报错，只会安静地
给出错的结果——这种 bug 没人能靠"用着不对劲"发现，必须挡在初始化。

走的是真的 ensure()：只把 FaissVecDB 和网络这两头打桩，
中间"要不要清库"的判断是真代码。
"""

import asyncio
import json
import sys
import types

import pytest

from astrlover.store.vectors import Vectors

INDEX_FILES = ("memory_docs.db", "memory.index", "album_docs.db", "album.index")


def run(coro):
    return asyncio.run(coro)


class _FakeFaiss:
    def __init__(self, docs, index, provider):
        self.docs, self.index, self.provider = docs, index, provider

    async def initialize(self):
        pass

    async def close(self):
        pass


@pytest.fixture(autouse=True)
def stub_faiss(monkeypatch):
    """FaissVecDB 在 ensure() 里按需 import，塞进 sys.modules 即可。"""
    mod = types.ModuleType("astrbot.core.db.vec_db.faiss_impl.vec_db")
    mod.FaissVecDB = _FakeFaiss
    for name in ("astrbot.core", "astrbot.core.db", "astrbot.core.db.vec_db",
                 "astrbot.core.db.vec_db.faiss_impl"):
        sys.modules.setdefault(name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "astrbot.core.db.vec_db.faiss_impl.vec_db", mod)


class _StubClient:
    """跳过网络：签名和维度说了算。"""

    def __init__(self, model="m-a", dim=8, ok=True):
        self.model, self._want, self.ok = model, dim, ok
        self._dim = 0
        self.conf = None

    @property
    def configured(self):
        return self.ok

    async def resolve_dim(self):
        self._dim = self._want
        return self._dim

    def get_dim(self):
        return self._dim

    def signature(self):
        return f"openai|host|{self.model}|{self._dim}"


def _vectors(tmp_path, client):
    v = Vectors(tmp_path, conf=None)
    v.client = client
    return v


def _touch_indexes(d):
    for name in INDEX_FILES:
        (d / name).write_bytes(b"old-vectors")


def _boot(tmp_path, client):
    """跑一次 ensure()，返回 (Vectors, 成功与否)。"""
    v = _vectors(tmp_path, client)
    ok = run(v.ensure())
    return v, ok


def test_first_boot_writes_signature(tmp_path):
    v, ok = _boot(tmp_path, _StubClient())
    assert ok and v.available
    assert json.loads((tmp_path / "embed_meta.json").read_text("utf-8"))["signature"] \
        == "openai|host|m-a|8"


def test_same_model_keeps_indexes(tmp_path):
    _boot(tmp_path, _StubClient())
    _touch_indexes(tmp_path)
    _boot(tmp_path, _StubClient())            # 同模型再开一次
    for name in INDEX_FILES:
        assert (tmp_path / name).read_bytes() == b"old-vectors", f"{name} 不该被清"


def test_changed_model_wipes_everything(tmp_path):
    _boot(tmp_path, _StubClient(model="m-a"))
    _touch_indexes(tmp_path)
    v, ok = _boot(tmp_path, _StubClient(model="m-b"))
    assert ok
    for name in INDEX_FILES:
        assert not (tmp_path / name).exists(), f"换了模型，{name} 必须清掉"
    assert json.loads((tmp_path / "embed_meta.json").read_text("utf-8"))["signature"] \
        == "openai|host|m-b|8"


def test_changed_dimension_wipes_everything(tmp_path):
    """同一个模型换维度同样致命。"""
    _boot(tmp_path, _StubClient(dim=8))
    _touch_indexes(tmp_path)
    _boot(tmp_path, _StubClient(dim=1536))
    for name in INDEX_FILES:
        assert not (tmp_path / name).exists()


def test_not_configured_degrades_with_reason(tmp_path):
    v, ok = _boot(tmp_path, _StubClient(ok=False))
    assert not ok and not v.available
    assert "没配" in v.last_error
    assert not (tmp_path / "embed_meta.json").exists(), "没起来就不该写签名"


def test_failure_is_only_reported_once(tmp_path):
    v, _ = _boot(tmp_path, _StubClient(ok=False))
    assert v._init_failed
    assert run(v.ensure()) is False           # 第二次直接短路，不再试


def test_reset_lets_it_try_again(tmp_path):
    """配错过之后改对了，不重启也要能再连上。"""
    v, _ = _boot(tmp_path, _StubClient(ok=False))
    assert v._init_failed
    v.reset()
    assert not v._init_failed and not v.last_error
    v.client = _StubClient()                  # 换成配好的
    assert run(v.ensure()) is True


def test_rebuild_album_leaves_memory_alone(tmp_path):
    """/gallery reindex 只该动相册那两个文件。"""
    v, _ = _boot(tmp_path, _StubClient())
    _touch_indexes(tmp_path)
    run(v.rebuild_album())
    assert (tmp_path / "memory_docs.db").exists() and (tmp_path / "memory.index").exists()
    assert not (tmp_path / "album_docs.db").exists()
    assert not (tmp_path / "album.index").exists()
