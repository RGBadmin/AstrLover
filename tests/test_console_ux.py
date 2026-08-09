"""导演控制台的三处体感问题。

1. 回执不渲染——不带 parse_mode 时反引号原样显示成 `xxx`；
2. 长指令发出去石沉大海，分不清是在跑还是挂了；
3. 索引/向量的定时汇报只有一句"已完成 N 张"，看不出还剩多少、为什么失败。
"""

import asyncio
import time

import pytest

from astrlover.director.bot import DirectorBot


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 渲染
def test_html_renders_code_spans():
    h = DirectorBot._html
    assert h("跑 `/gallery scan` 试试") == "跑 <code>/gallery scan</code> 试试"
    assert h("a `b` c `d` e") == "a <code>b</code> c <code>d</code> e"


def test_html_escapes_and_survives_stray_backtick():
    h = DirectorBot._html
    # 尖括号必须转义，否则 Telegram 按标签解析后整条 400
    assert h("<b>不是标签</b>") == "&lt;b&gt;不是标签&lt;/b&gt;"
    assert h("a & b") == "a &amp; b"
    # 落单的反引号是正文自带的，不能被吞掉
    assert h("价格是 `100") == "价格是 `100"
    assert h("`") == "`"


def test_html_leaves_markdown_chars_alone():
    """UMO、路径、文件名里全是 _ * [，走 Markdown 会整条 400。"""
    raw = "tg:FriendMessage:123 → /data/plugin_data/my_plugin/a_b*c[1].jpg"
    assert DirectorBot._html(raw) == raw


# ---------------------------------------------------------------- 执行反馈
class _FakeBot:
    def __init__(self):
        self.sent = []
        self.actions = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((text, parse_mode))

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)


class _FakeApp:
    def __init__(self, bot):
        self.bot = bot


def _bot_with(handler, slow=False):
    b = DirectorBot.__new__(DirectorBot)
    b.application = _FakeApp(_FakeBot())
    b.app = None
    b.console = type("C", (), {"handle": staticmethod(handler)})()
    return b


def _update(text="/gallery index auto", uid="1"):
    msg = type("M", (), {"text": text, "caption": None, "chat_id": 42})()
    user = type("U", (), {"id": uid})()
    return type("Up", (), {"effective_message": msg, "effective_user": user})()


def test_fast_command_stays_one_message(monkeypatch):
    async def quick(_text, chat_id=None):
        return "好了"

    b = _bot_with(quick)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    run(b._on_update(_update(), None))
    texts = [t for t, _ in b.application.bot.sent]
    assert texts == ["好了"], "快指令不该多出一条「执行中」"
    assert b.application.bot.actions == ["typing"], "至少要有 typing 表示收到了"


def test_slow_command_gets_ack(monkeypatch):
    async def slow(_text, chat_id=None):
        await asyncio.sleep(0.3)
        return "跑完了"

    b = _bot_with(slow)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    monkeypatch.setattr("astrlover.director.bot._ACK_AFTER", 0.05)
    run(b._on_update(_update(), None))
    texts = [t for t, _ in b.application.bot.sent]
    assert len(texts) == 2
    assert "执行中" in texts[0] and "/gallery index auto" in texts[0]
    assert texts[1] == "跑完了"


def test_crash_is_reported_not_swallowed(monkeypatch):
    async def boom(_text, chat_id=None):
        await asyncio.sleep(0.2)
        raise RuntimeError("上游炸了")

    b = _bot_with(boom)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    monkeypatch.setattr("astrlover.director.bot._ACK_AFTER", 0.05)
    run(b._on_update(_update(), None))
    texts = [t for t, _ in b.application.bot.sent]
    assert "执行出错" in texts[-1] and "上游炸了" in texts[-1]


def test_say_uses_html_parse_mode():
    b = DirectorBot.__new__(DirectorBot)
    b.application = _FakeApp(_FakeBot())
    run(b.say(42, "跑 `/gallery scan`"))
    text, mode = b.application.bot.sent[0]
    assert mode == "HTML" and "<code>" in text


# ---------------------------------------------------------------- 进度汇报
def test_index_progress_answers_the_questions():
    """跑到哪了、还剩多少、多久跑完、出什么错——四个都得有。"""
    from astrlover.album.index import AlbumIndexer

    class _Album:
        async def stats(self):
            return {"ok": 1200, "pending": 800, "failed": 40}

    class _Stats:
        calls, blocked, hard, saved = 1580, 260, 60, 130

    idx = AlbumIndexer.__new__(AlbumIndexer)
    idx.app = type("A", (), {"album": _Album()})()
    idx.note = "被 max_tokens 掐断"
    vision = type("V", (), {"stats": _Stats})()

    text = run(idx._progress(300, 20, time.time() - 3600, (0, 0, 0, 0), vision))
    assert "1200/2040" in text and "还剩 800" in text      # 全库进度
    assert "小时" in text                                  # 预计
    assert "被内容策略拦掉 320 次" in text                  # 账
    assert "重试救回 130 张" in text
    assert "被 max_tokens 掐断" in text                     # 为什么失败
    assert "index stop" in text                             # 怎么停


def test_embed_progress_answers_the_questions():
    from astrlover.album.embed import AlbumEmbedder

    class _Album:
        async def stats(self):
            return {"ok": 1000, "embedded": 400}

    emb = AlbumEmbedder.__new__(AlbumEmbedder)
    emb.app = type("A", (), {
        "album": _Album(),
        "vectors": type("V", (), {"client": type("C", (), {"model": "bge-m3"})()})(),
    })()
    emb.note = ""

    text = run(emb._progress(400, time.time() - 3600))
    assert "400/1000" in text and "还剩 600" in text
    assert "bge-m3" in text and "小时" in text
    assert "embed stop" in text


def test_html_renders_bold():
    h = DirectorBot._html
    assert h("**指令一览**") == "<b>指令一览</b>"
    assert h("**全库** 1200/2040") == "<b>全库</b> 1200/2040"


def test_bold_does_not_eat_stray_stars():
    """乘方、通配符里的星号是正文，不是标记。"""
    h = DirectorBot._html
    assert h("2 ** 3 = 8") == "2 ** 3 = 8"
    assert h("a ** b") == "a ** b"
    assert h("**跨行\n不算**") == "**跨行\n不算**"
    # 代码段里的星号原样留着
    assert h("`ls **/*.py`") == "<code>ls **/*.py</code>"


def test_console_replies_actually_contain_markup():
    """渲染是好的，但内容里没有可渲染的东西一样看不出效果。

    参考插件的回执把每个指令引用都包了反引号；这里盯住不要退回裸文本。
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parent.parent / "astrlover" / "director" / "console.py"
    text = src.read_text(encoding="utf-8")
    assert text.count("`") >= 60, f"控制台回执里只有 {text.count('`')} 个反引号，基本没markup"

    # MENU 里每条带指令示例的说明都要包起来（反引号里的不算）
    from astrlover.director.console import MENU

    def outside_code(s):
        return "".join(s.split("`")[::2])

    bare = [k for k, d in MENU if re.search(r"(?<![\w/])/[a-z]+", outside_code(d))]
    assert not bare, f"这些菜单项的说明里还有裸指令：{bare}"
