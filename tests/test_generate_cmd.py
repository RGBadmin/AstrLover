"""/generate：现场生成一张再发。

跟 /photo 分开：检索几乎免费，生图每张要钱要时间，混在一个指令里
容易误触发。所以不带参数时只给方案不动手，点了按钮才真花钱。
"""

import asyncio

import pytest

from astrlover.director.keyboard import Reply


def run(coro):
    return asyncio.run(coro)


class _Gen:
    """假生图后端：记下收到的描述，返回一个假路径。"""

    def __init__(self, ok=True, available=True):
        self.available = available
        self.seen = []
        self._ok = ok

    async def generate(self, situation):
        self.seen.append(situation)
        return "/tmp/fake.png" if self._ok else None


@pytest.fixture
def gen_app(app_factory):
    async def build(ok=True, available=True, linked=True):
        app = app_factory()
        await app.initialize()
        app.imagegen = _Gen(ok, available)
        if linked:
            await app.set_target("tg:FriendMessage:123")
        sent = []

        async def fake_send(umo, chain):
            sent.append((umo, chain))
            return True

        app.context.send_message = fake_send
        app._sent = sent
        return app

    return build


def test_generate_sends_and_records(gen_app):
    async def go():
        app = await gen_app()
        out = await app.director_bot.console.cmd_generate("阳台上的晚霞")
        assert "已生成并发出" in out
        assert app.imagegen.seen == ["阳台上的晚霞"]
        assert app._sent, "图没发出去"

        # 写进她的历史——不然下一轮她不知道自己发过图
        assert any("现拍" in str(m.get("content", ""))
                   for m in app.context.history if m.get("role") == "assistant")
        # 记成事件，她之后能提起
        rows = await app.dao.recent_events(5)
        assert any(r["kind"] == "photo_gen" for r in rows)
        await app.terminate()

    run(go())


def test_caption_after_pipe(gen_app):
    async def go():
        app = await gen_app()
        out = await app.director_bot.console.cmd_generate("阳台上的晚霞 | 今天天好好")
        assert "附言：今天天好好" in out
        assert app.imagegen.seen == ["阳台上的晚霞"], "附言不该混进画面描述"
        await app.terminate()

    run(go())


def test_no_backend_says_where_to_configure(gen_app):
    async def go():
        app = await gen_app(available=False)
        out = await app.director_bot.console.cmd_generate("随便")
        assert "生图" in out and "没配" in out
        await app.terminate()

    run(go())


def test_generation_failure_is_reported(gen_app):
    async def go():
        app = await gen_app(ok=False)
        out = await app.director_bot.console.cmd_generate("阳台上的晚霞")
        assert "没拍成" in out
        assert not app._sent, "没生成出来就不该发"
        await app.terminate()

    run(go())


def test_unlinked_does_not_burn_money(gen_app):
    """没绑会话时发不出去——但钱已经花了，所以要在日志/回执里说清楚。"""

    async def go():
        app = await gen_app(linked=False)
        out = await app.director_bot.console.cmd_generate("阳台上的晚霞")
        assert "绑定" in out
        await app.terminate()

    run(go())


def test_bare_command_asks_before_spending(gen_app, monkeypatch):
    """不带参数只给方案 + 确认按钮，不直接开销。"""

    async def go():
        app = await gen_app()

        async def fake_improvise(_self, key):
            return "站在阳台上，晚霞把整片天染成橘色"

        monkeypatch.setattr(type(app.director_bot.console), "_improvise", fake_improvise)
        r = await app.director_bot.console.cmd_generate()

        assert isinstance(r, Reply) and r.buttons
        assert not app.imagegen.seen, "还没确认就花钱了"
        cmds = [c for row in r.buttons for _, c in row]
        assert "/generate 站在阳台上，晚霞把整片天染成橘色" in cmds
        assert "/generate" in cmds, "要留一个「换一个」"
        await app.terminate()

    run(go())


def test_photo_points_to_generate_when_album_misses(gen_app):
    """相册里翻不到时，告诉他还能现拍。"""

    async def go():
        app = await gen_app()
        out = await app.director_bot.console.cmd_photo("根本不存在的画面")
        assert "/generate" in out
        await app.terminate()

    run(go())


def test_generate_is_in_menu_and_plan_intent():
    from astrlover.director.console import _PLAN_INTENT, MENU, DirectorConsole

    assert "generate" in {k for k, _ in MENU}
    assert hasattr(DirectorConsole, "cmd_generate")
    # 排期的意图解析也得知道有这条，否则「明早拍张照发我」排不出来
    assert "/generate" in _PLAN_INTENT
