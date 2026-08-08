"""相册描述的分段。

这里出错不会报任何错——切不出层就退化成整篇一段，检索照跑，
只是环境词永远撞不上。所以必须拿视觉提示词的**真实输出格式**来测，
而不是拿测试里自己编的格式。
"""

import asyncio
import json
import sys
import types

import pytest

from astrlover.album.embed import SEGMENTS, split_layers

# 视觉提示词实际产出的样子：标签行 + 「第N层：标题」
REAL = """淫荡---无水印---无遮挡---车内---黑丝---细高跟---M腿大开---淫水拉丝

第一层：环境与背景
车内后排，深色皮质座椅，车窗外夜色，顶灯暖光。

第二层：人物整体
桃桃单人，坐姿，黑色连裤袜配红底细高跟。

第三层：身体细节
桃桃的小阴唇明显外翻蝴蝶逼形态，阴道口微张，内壁浅粉色，表面湿润。

第四层：互动与动作
单人无互动。桃桃的右手中指插在骚穴里，左手食指按在阴蒂上。

第五层：物品与道具
座椅安全带垂在一侧，方向盘。

第六层：体液与痕迹
淫水在阴道口周围，有拉丝，未流到大腿。"""

# 没有「第N层」前缀、层名不带「与」的写法也得认
BARE = """诱惑---无水印---无遮挡---酒店

环境与背景
酒店房间，落地窗，夜景。

人物整体
桃桃站在窗边。

身体细节
白色长筒袜配过膝靴。

互动动作
单人无互动。

物品道具
窗帘。

体液痕迹
无。"""


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 切分
def test_real_format_splits_into_three():
    segs = split_layers(REAL)
    assert set(segs) == set(SEGMENTS), f"切出来的是 {sorted(segs)}，不是三段"


def test_bare_titles_also_split():
    segs = split_layers(BARE)
    assert set(segs) == set(SEGMENTS)


def test_each_segment_gets_the_right_layers():
    segs = split_layers(REAL)
    assert "皮质座椅" in segs["env"] and "夜色" in segs["env"]
    assert "小阴唇" in segs["body"] and "连裤袜" in segs["body"]
    assert "右手中指" in segs["act"] and "淫水" in segs["act"]
    # 道具跟动作一段——检索侧「跳蛋 绳子」也归在动作那类
    assert "安全带" in segs["act"]


def test_environment_is_not_swallowed_by_body():
    """env 段要短、要纯——这就是分段的全部意义。"""
    segs = split_layers(REAL)
    assert len(segs["env"]) < len(segs["body"])
    for word in ("小阴唇", "淫水", "连裤袜"):
        assert word not in segs["env"], f"env 段混进了「{word}」"


def test_tag_line_goes_into_act():
    """标签行关键词密度最高，要进动作段。"""
    segs = split_layers(REAL)
    assert "M腿大开" in segs["act"] and "细高跟" in segs["act"]


def test_no_layers_falls_back_to_whole_text():
    """老式描述（没有层级标题）整篇一段兜底，不能一段都不产。"""
    old = "酒店房间落地窗前，黑色丝袜配红底细高跟，倚在窗边回头看镜头。"
    segs = split_layers(old)
    assert segs and old in "".join(segs.values())


def test_partial_layers_still_keep_everything():
    """只切出一层时，剩下的内容不能凭空消失。"""
    half = "生活---无水印---无遮挡---卧室\n\n第一层：环境与背景\n卧室。\n\n" + "其余内容没写层级标题。" * 12
    segs = split_layers(half)
    joined = "".join(segs.values())
    assert "其余内容没写层级标题" in joined, "没打上标题的那大段被丢了"


def test_empty_description():
    assert split_layers("") == {}


# ---------------------------------------------------------------- 自动重建
class _FakeFaiss:
    def __init__(self, docs, index, provider):
        pass

    async def initialize(self):
        pass

    async def close(self):
        pass


@pytest.fixture(autouse=True)
def stub_faiss(monkeypatch):
    mod = types.ModuleType("astrbot.core.db.vec_db.faiss_impl.vec_db")
    mod.FaissVecDB = _FakeFaiss
    for name in ("astrbot.core", "astrbot.core.db", "astrbot.core.db.vec_db",
                 "astrbot.core.db.vec_db.faiss_impl"):
        sys.modules.setdefault(name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "astrbot.core.db.vec_db.faiss_impl.vec_db", mod)


class _StubClient:
    def __init__(self):
        self._dim = 0

    @property
    def configured(self):
        return True

    async def resolve_dim(self):
        self._dim = 8
        return 8

    def get_dim(self):
        return self._dim

    def signature(self):
        return "openai|host|m|8"


def _vectors(tmp_path):
    from astrlover.store.vectors import Vectors

    v = Vectors(tmp_path, conf=None)
    v.client = _StubClient()
    return v


def test_segmentation_change_rebuilds_album_only(tmp_path):
    """分段方式改了：相册作废，记忆库不动——记忆本来就不分段。"""
    v = _vectors(tmp_path)
    assert run(v.ensure())
    for name in ("memory_docs.db", "memory.index", "album_docs.db", "album.index"):
        (tmp_path / name).write_bytes(b"old")

    # 伪造一份"上一版是四段"的标识
    meta = json.loads((tmp_path / "embed_meta.json").read_text("utf-8"))
    meta["segments"] = ["full", "env", "body", "act"]
    (tmp_path / "embed_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    v2 = _vectors(tmp_path)
    assert run(v2.ensure())
    assert v2.album_wiped, "没标记出相册要重转"
    assert not (tmp_path / "album_docs.db").exists()
    assert not (tmp_path / "album.index").exists()
    assert (tmp_path / "memory_docs.db").read_bytes() == b"old", "记忆库被误伤"


def test_same_segmentation_keeps_album(tmp_path):
    v = _vectors(tmp_path)
    assert run(v.ensure())
    (tmp_path / "album.index").write_bytes(b"old")

    v2 = _vectors(tmp_path)
    assert run(v2.ensure())
    assert not v2.album_wiped
    assert (tmp_path / "album.index").read_bytes() == b"old"


def test_meta_records_current_segments(tmp_path):
    v = _vectors(tmp_path)
    run(v.ensure())
    meta = json.loads((tmp_path / "embed_meta.json").read_text("utf-8"))
    assert meta["segments"] == list(SEGMENTS)
