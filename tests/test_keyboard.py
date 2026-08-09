"""可点击的回执。

按钮携带的就是一行控制台指令，点击后原样重放——所以这里要盯住三件事：
Reply 仍然是个正常的字符串（现有代码全靠这点没改）、
超 64 字节的指令能过令牌表、点击真的执行到了。
"""

import asyncio

import pytest

from astrlover.director.bot import DirectorBot
from astrlover.director.keyboard import CB_LIMIT, Callbacks, Reply, grid


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- Reply
def test_reply_is_a_plain_string():
    """现有代码把回执当字符串用（拼接、startswith、if reply），不能改坏。"""
    r = Reply("已排期 #3", [[("取消", "/plans cancel 3")]])
    assert isinstance(r, str)
    assert r == "已排期 #3"
    assert r.startswith("已排期") and len(r) == len("已排期 #3")
    assert "前缀 " + r == "前缀 已排期 #3"
    assert bool(r) and not bool(Reply(""))
    assert r.buttons == [[("取消", "/plans cancel 3")]]


def test_plain_string_has_no_buttons():
    assert getattr("普通回执", "buttons", None) is None


# ---------------------------------------------------------------- 令牌
def test_short_command_rides_along_verbatim():
    cb = Callbacks()
    assert cb.encode("/link tg:FriendMessage:123") == "/link tg:FriendMessage:123"


def test_long_command_goes_through_a_token():
    """callback_data 是 64 字节硬上限，UMO 长一点就装不下。"""
    cb = Callbacks()
    long_cmd = "/link " + "platform-with-a-very-long-name:FriendMessage:" + "9" * 40
    assert len(long_cmd.encode()) > CB_LIMIT
    token = cb.encode(long_cmd)
    assert len(token.encode()) <= CB_LIMIT
    assert cb.decode(token) == long_cmd


def test_chinese_counts_as_bytes_not_chars():
    """中文一个字三字节，按字符算会超限。"""
    cb = Callbacks()
    cmd = "/act " + "一" * 30                # 30 字 = 90 字节
    assert len(cmd) < CB_LIMIT < len(cmd.encode())
    assert len(cb.encode(cmd).encode()) <= CB_LIMIT


def test_unknown_token_decodes_to_nothing():
    """插件重载后旧按钮点了要能识别出来，而不是执行个空指令。"""
    assert Callbacks().decode("c:deadbeef") == ""


def test_token_table_is_bounded():
    cb = Callbacks()
    for i in range(600):
        cb.encode("/act " + "长" * 30 + str(i))
    assert len(cb._map) <= 400


def test_grid_wraps():
    assert grid([("a", "1"), ("b", "2"), ("c", "3")], per_row=2) == \
        [[("a", "1"), ("b", "2")], [("c", "3")]]
    assert grid([]) == []


# ---------------------------------------------------------------- 点击
class _Q:
    def __init__(self, data, uid="1", chat_id=42):
        self.data = data
        self.from_user = type("U", (), {"id": uid})()
        self.message = type("M", (), {"chat_id": chat_id})()
        self.answered = False

    async def answer(self):
        self.answered = True


class _FakeBot:
    def __init__(self):
        self.sent = []
        self.markups = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append(text)
        self.markups.append(reply_markup)


def _bot(handler):
    b = DirectorBot.__new__(DirectorBot)
    b.application = type("A", (), {"bot": _FakeBot()})()
    b.callbacks = Callbacks()
    b.console = type("C", (), {"handle": staticmethod(handler)})()
    return b


def test_click_replays_the_command(monkeypatch):
    seen = {}

    async def handle(text, chat_id=None):
        seen["cmd"] = text
        return "已绑定 tg:FriendMessage:123"

    b = _bot(handle)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    q = _Q("/link tg:FriendMessage:123")
    run(b._on_callback(type("U", (), {"callback_query": q})(), None))

    assert q.answered, "不应答按钮会一直转圈"
    assert seen["cmd"] == "/link tg:FriendMessage:123"
    assert any("已绑定" in t for t in b.application.bot.sent)
    assert any("▶️" in t for t in b.application.bot.sent), "该回显点了什么"


def test_click_from_a_stranger_is_ignored(monkeypatch):
    async def handle(text, chat_id=None):
        raise AssertionError("非管理员的点击不该执行")

    b = _bot(handle)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    q = _Q("/link x:y:z", uid="999")
    run(b._on_callback(type("U", (), {"callback_query": q})(), None))
    assert not b.application.bot.sent


def test_expired_button_says_so(monkeypatch):
    async def handle(text, chat_id=None):
        raise AssertionError("过期令牌不该执行")

    b = _bot(handle)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    q = _Q("c:notinthetable")
    run(b._on_callback(type("U", (), {"callback_query": q})(), None))
    assert "过期" in b.application.bot.sent[0]


def test_click_crash_is_reported(monkeypatch):
    async def handle(text, chat_id=None):
        raise RuntimeError("上游炸了")

    b = _bot(handle)
    monkeypatch.setattr(DirectorBot, "admins", lambda self: ["1"])
    q = _Q("/status")
    run(b._on_callback(type("U", (), {"callback_query": q})(), None))
    assert "执行出错" in b.application.bot.sent[-1] and "上游炸了" in b.application.bot.sent[-1]


# ---------------------------------------------------------------- 命令带按钮
@pytest.fixture
def console(app_factory):
    async def build():
        app = app_factory()
        await app.initialize()
        return app

    return build


def test_umo_lists_conversations_as_buttons(console, monkeypatch):
    async def go():
        app = await console()

        class _CM:
            async def get_conversations(self, *_a, **_k):
                return [type("C", (), {"user_id": f"tg:FriendMessage:{i}", "updated_at": i})()
                        for i in (100, 200)]

        app.context.conversation_manager = _CM()
        await app.set_target("tg:FriendMessage:200")

        r = await app.director_bot.console.cmd_umo()
        assert isinstance(r, Reply) and r.buttons
        cmds = [cmd for row in r.buttons for _, cmd in row]
        assert "/link tg:FriendMessage:100" in cmds
        assert any("✅" in label for row in r.buttons for label, _ in row), "当前绑定要标出来"
        await app.terminate()

    run(go())


def test_bare_commands_offer_buttons(console):
    """不带参数时给可点的入口，带参数时照常执行不加按钮。"""

    async def go():
        app = await console()
        c = app.director_bot.console
        for bare in (await c.cmd_help(), await c.cmd_gallery(), await c.cmd_rec()):
            assert isinstance(bare, Reply) and bare.buttons, bare[:40]
        # 带参数就不该再挂按钮
        assert not isinstance(await c.cmd_gallery("scan"), Reply)
        await app.terminate()

    run(go())


def test_every_button_command_is_a_real_command():
    """按钮点下去要真能执行——指令名拼错了不会有任何报错，只是没反应。"""
    from astrlover.director.console import MENU, DirectorConsole

    names = {k for k, _ in MENU}
    fixed = ["/umo", "/status", "/rec", "/gallery", "/plans", "/proactive",
             "/presence", "/vision", "/gallery scan", "/gallery index auto",
             "/gallery embed test", "/vision backfill", "/proactive now"]
    for cmd in fixed:
        head = cmd[1:].split()[0]
        assert head in names, f"{cmd} 不在 MENU 里"
        assert hasattr(DirectorConsole, f"cmd_{head}"), f"{cmd} 没有对应的处理函数"
