"""检索造词提示词。

它教的词必须真能被检索侧切出来——提示词和分词词典是两份东西，
悄悄改一边就会失效：她照着提示词给了词，分词却切不出来，
一分都加不上，而这件事没有任何报错。
"""

import asyncio

import pytest

from astrlover.album.prompts import SEARCH_PROMPT
from astrlover.album.search import segment
from astrlover.settings import BY_KEY
from astrlover.tools import _JSON_CONTRACT
from astrlover.vision.tags import TAG_VOCAB


def run(coro):
    return asyncio.run(coro)


# 提示词【常用词，按段分】里列的词，抽样验证能被切出来
SAMPLES = [
    ("环境", "酒店 卧室 浴室 试衣间 厕所隔间 车内 落地窗 镜子前"),
    ("衣着", "黑丝 白丝 网袜 吊带袜 开裆丝袜 细高跟 过膝靴 开裆内裤 丁字裤"),
    ("部位", "乳头 乳晕 乳沟 侧乳 翘臀 骚逼 小阴唇 阴蒂 蝴蝶逼 一线天 馒头逼 肛门"),
    ("动作", "M腿大开 掰开 手指插入 自慰 口交 骑乘 后入 跳蛋 假鸡巴 肛塞"),
    ("体液", "淫水 白浆 精液 内射 颜射 口水"),
    ("拍法", "自拍 镜面自拍 他拍 特写 全身 俯拍 仰拍"),
]


@pytest.mark.parametrize("seg,line", SAMPLES)
def test_prompt_words_are_searchable(seg, line):
    """提示词教的词，分词得切得出来（整词或它的组成部分）。"""
    for word in line.split():
        got = segment(word)
        assert got, f"{seg}：「{word}」切出来是空的，给了也白给"


def test_prompt_words_appear_in_prompt():
    """上面抽样的词确实来自提示词，不是我另编的一套。"""
    for _seg, line in SAMPLES:
        for word in line.split():
            assert word in SEARCH_PROMPT, f"「{word}」不在提示词里，测试和提示词脱节了"


def test_key_vocabulary_aligns_with_index():
    """核心检索词必须在标签词典里——那是索引侧候选值的同一份。

    在词典里的词会被正向最大匹配整体切出来；不在的退成二元滑窗，
    「蝴蝶逼」会被切成「蝴蝶」「蝶逼」，命中率差一大截。
    """
    must = ["蝴蝶逼", "一线天", "馒头逼", "黑丝", "细高跟", "M腿",
            "开裆内裤", "镜面自拍", "淫水", "精液"]
    missing = [w for w in must if w not in TAG_VOCAB]
    assert not missing, f"这些词不在标签词典里，会被切碎：{missing}"


def test_prompt_teaches_the_real_mechanics():
    """三条反直觉的机制必须讲到，否则模型会本能地做反。"""
    # 加分制不是筛选制——不讲，模型会"少给几个词免得搜不到"
    assert "加分制" in SEARCH_PROMPT and "不是筛选制" in SEARCH_PROMPT
    # 四段向量——不讲，词会全堆在一类里
    for seg in ("全文段", "环境段", "身体段", "动作段"):
        assert seg in SEARCH_PROMPT
    # 分级走参数不走检索词
    assert "rating" in SEARCH_PROMPT and "参数，不是词" in SEARCH_PROMPT
    # 画面描述写陈述句
    assert "陈述句" in SEARCH_PROMPT


def test_output_contract_is_not_in_editable_prompt():
    """输出格式由代码追加，用户改提示词改不坏解析。"""
    assert "JSON" not in SEARCH_PROMPT, "格式约定不该混进可编辑的提示词"
    for key in ("keywords", "want", "rating", "season"):
        assert f'"{key}"' in _JSON_CONTRACT


def test_prompt_is_editable_setting():
    """它是面板里可改的设置项，不是写死在代码里的常量。"""
    spec = BY_KEY["gallery_search_prompt"]
    assert spec.group == "相册" and spec.type == "text"
    assert spec.default == SEARCH_PROMPT


def test_want_photo_feeds_conversation_to_the_model(app_factory, monkeypatch):
    """提示词教的是"从他的话里抠词"——不把对话喂进去就没素材。"""

    async def go():
        app = app_factory()
        await app.initialize()
        await app.set_target("tg:FriendMessage:123")
        app.context.history = [
            {"role": "user", "content": "去厕所拍一张"},
            {"role": "assistant", "content": "[08-08 10:44] 这就去"},
        ]

        seen = {}

        async def fake_light_json(prompt, system_prompt=None):
            seen["prompt"] = prompt
            seen["system"] = system_prompt
            return {"keywords": "厕所 隔间 镜子", "want": "厕所隔间里对着镜子自拍"}

        monkeypatch.setattr(app.llm, "light_json", fake_light_json)
        await app.tools.want_photo(None, "他让我去厕所拍一张")

        assert "去厕所拍一张" in seen["prompt"], "最近对话没喂进去"
        assert "他让我去厕所拍一张" in seen["prompt"]
        assert SEARCH_PROMPT in seen["system"], "用的不是面板里那份提示词"
        assert _JSON_CONTRACT in seen["system"], "输出约定没追加上"
        await app.terminate()

    run(go())


def test_want_photo_survives_without_conversation(app_factory, monkeypatch):
    """没绑会话/取不到历史时，只按理由造词，不能炸。"""

    async def go():
        app = app_factory()
        await app.initialize()

        async def fake_light_json(prompt, system_prompt=None):
            return {"keywords": "穿搭 全身镜"}

        monkeypatch.setattr(app.llm, "light_json", fake_light_json)
        out = await app.tools.want_photo(None, "想给他看今天的穿搭")
        assert isinstance(out, str)
        await app.terminate()

    run(go())
