"""检索造词提示词。

它教的词必须真能被检索侧切出来——提示词和分词词典是两份东西，
悄悄改一边就会失效：她照着提示词给了词，分词却切不出来，
一分都加不上，而这件事没有任何报错。
"""

import asyncio
import re

import pytest

from astrlover.album.prompts import SEARCH_PROMPT
from astrlover.album.search import segment
from astrlover.settings import BY_KEY
from astrlover.tools import _JSON_CONTRACT
from astrlover.vision.tags import TAG_VOCAB


def run(coro):
    return asyncio.run(coro)


def _section(title: str) -> str:
    """取【标题】到下一个【】之间的正文。"""
    m = re.search(rf"【{title}】(.*?)(?=\n【|\n</)", SEARCH_PROMPT, re.S)
    assert m, f"提示词里没有【{title}】这一节"
    return m.group(1)


def _vocab_words() -> list[str]:
    """词汇表里所有的词。"""
    out = []
    for line in _section("词汇表").splitlines():
        line = line.strip()
        if not line or line.endswith("："):
            continue
        out.extend(line.split())
    return out


def _synonym_targets() -> list[tuple[str, list[str]]]:
    """同义词表右侧那些"库里的词"。"""
    out = []
    for line in _section("他的说法换成库里的词").splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) == 2 and parts[0] and not parts[0].startswith("他说"):
            out.append((parts[0], parts[1].split()))
    return out


# ---------------------------------------------------------------- 词表可用
def test_vocab_is_not_thin():
    words = _vocab_words()
    assert len(words) >= 120, f"词汇表只有 {len(words)} 个词，不够用"


def test_every_vocab_word_is_searchable():
    """词汇表里每个词都得切得出来，给了才有用。"""
    bad = [w for w in _vocab_words() if not segment(w)]
    assert not bad, f"这些词切出来是空的：{bad}"


def test_core_words_are_whole_in_vocabulary():
    """核心词要整体在分词词典里，否则会被切碎。

    「蝴蝶逼」不在词典就退成二元滑窗切成「蝴蝶」「蝶逼」，命中率差一大截。
    """
    must = ["蝴蝶逼", "一线天", "馒头逼", "黑丝", "细高跟", "开裆内裤",
            "镜面自拍", "淫水", "精液", "小阴唇", "全身镜"]
    missing = [w for w in must if w not in TAG_VOCAB]
    assert not missing, f"这些词不在标签词典里，会被切碎：{missing}"


# ---------------------------------------------------------------- 同义词表
def test_synonym_table_has_the_common_ones():
    said = {k for k, _ in _synonym_targets()}
    for word in ("今天穿的 / 穿搭", "腿", "丝袜", "高跟鞋", "屁股", "湿了 / 流水"):
        assert word in said, f"同义词表里没有「{word}」"


def test_synonym_targets_are_searchable():
    """右侧给出的词才是真去搜的，必须都切得出来。"""
    bad = [(said, w) for said, targets in _synonym_targets()
           for w in targets if not segment(w)]
    assert not bad, f"这些替换词搜不到：{bad}"


def test_euphemisms_map_to_index_words():
    """「下面」这类委婉说法必须被换掉——库里没有，搜了会拆字回退成噪声。"""
    table = dict(_synonym_targets())
    key = next(k for k in table if "下面" in k)
    assert "骚逼" in table[key]


# ---------------------------------------------------------------- 情境预测
def test_prediction_adds_words_instead_of_replacing():
    """预测是补充，不是替换。

    「她在家化妆，等下出门拍给你」——化妆是真实上下文，该留着；
    漏掉「出门以后」那组才是错。词面不取交集，两组一起给谁都不漏。
    """
    sec = _section("此刻的词，加上等下的词")
    assert "两组都给" in sec
    # 那个例子里两组词都在
    for now in ("卧室", "梳妆台", "化妆", "睡衣"):
        assert now in sec, f"此刻那组少了「{now}」"
    for later in ("室外", "马路", "街道", "全身镜", "站姿", "穿搭"):
        assert later in sec, f"等下那组少了「{later}」"


def test_prediction_covers_several_shapes():
    sec = _section("此刻的词，加上等下的词")
    for shape in ("此刻：", "等下："):
        assert sec.count(shape) >= 3, f"「{shape}」的例子太少"
    assert "找旧图" in sec, "他指名要旧图时不该再预测"


def test_prediction_examples_give_real_words():
    """例子里给的词也得是能搜的，不能只是说说。"""
    sec = _section("此刻的词，加上等下的词")
    for word in ("室外", "马路", "全身镜", "自拍", "玄关", "沙发", "侧躺",
                 "副驾", "梳妆台", "化妆", "雾气", "湿发"):
        assert word in sec, f"「{word}」不在例子里"
        assert segment(word), f"「{word}」搜不到"


# ---------------------------------------------------------------- 形态
def test_prompt_only_says_what_to_do():
    """只写要怎么做，不写怎么做是错的——没有反例、没有禁止清单。"""
    for marker in ("✗", "不要", "禁止", "反例", "别写成"):
        assert marker not in SEARCH_PROMPT, f"提示词里还留着否定式：{marker}"


def test_prompt_stays_short():
    """是给模型看的词表，不是教材。"""
    assert len(SEARCH_PROMPT) < 3000, f"{len(SEARCH_PROMPT)} 字，太长了"


def test_output_contract_is_not_in_editable_prompt():
    """输出格式由代码追加，用户改提示词改不坏解析。"""
    assert "JSON" not in SEARCH_PROMPT
    for key in ("keywords", "want", "rating", "season"):
        assert f'"{key}"' in _JSON_CONTRACT


def test_prompt_is_editable_setting():
    spec = BY_KEY["gallery_search_prompt"]
    assert spec.group == "相册" and spec.type == "text"
    assert spec.default == SEARCH_PROMPT


# ---------------------------------------------------------------- 接线
def test_want_photo_feeds_conversation_to_the_model(app_factory, monkeypatch):
    """要顺着情境想，就得先看得到情境——对话必须喂进去。"""

    async def go():
        app = app_factory()
        await app.initialize()
        await app.set_target("tg:FriendMessage:123")
        app.context.history = [
            {"role": "assistant", "content": "[08-08 07:30] 在化妆，等下要去上班"},
            {"role": "user", "content": "今天穿的什么"},
            {"role": "assistant", "content": "[08-08 07:32] 等下出门了拍给你"},
        ]

        seen = {}

        async def fake_light_json(prompt, system_prompt=None):
            seen["prompt"] = prompt
            seen["system"] = system_prompt
            return {"keywords": "室外 马路 全身镜 自拍 站姿 穿搭"}

        monkeypatch.setattr(app.llm, "light_json", fake_light_json)
        await app.tools.want_photo(None, "答应了出门拍给他")

        assert "今天穿的什么" in seen["prompt"], "对话没喂进去，情境无从判断"
        assert "等下出门了拍给你" in seen["prompt"]
        assert SEARCH_PROMPT in seen["system"]
        assert _JSON_CONTRACT in seen["system"]
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
        assert isinstance(await app.tools.want_photo(None, "想给他看今天的穿搭"), str)
        await app.terminate()

    run(go())


def test_mixed_ascii_cjk_words_survive_segmentation():
    """字母+汉字的混排词整体切出来。

    分词器把字母和汉字分开扫，「M腿大开」按那套永远匹配不上——
    字母那半太短被丢掉，剩下的「腿大开」又不成词，最后只剩个「腿」。
    """
    for word in ("M腿大开", "M腿", "T恤", "69"):
        assert segment(word) == [word], f"「{word}」被切碎成 {segment(word)}"
    # 混在一串词里也一样
    assert segment("酒店 黑丝 M腿大开") == ["酒店", "黑丝", "M腿大开"]
