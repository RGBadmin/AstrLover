"""导演 bot 启动路径。

之前 fixture 里 console_token 一直留空（为了不去连 Telegram），
于是 start() 里那段代码从来没被跑过，把 Settings 当字典下标用的
TypeError 一路活到线上。

这里给个假 token 让它真的走进去，但在 Application.initialize 处截断——
那正是 PTB 的网络边界，截在这里既跑到了"读配置 → 建 Application"
这段真代码，又一个包都不发出去。
"""

import asyncio

import pytest

# 假 token 得符合 PTB 的格式校验（数字:字符串），否则建 builder 就报错
FAKE_TOKEN = "1234567890:AAFakeTokenForTestsOnly_NotARealBotXYZ"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_telegram(monkeypatch):
    """把 PTB 的网络边界堵死，并记录被调用过几次。"""
    from telegram.ext import Application

    calls = []

    async def fake_initialize(self):
        calls.append(self)
        raise RuntimeError("测试环境不连 Telegram")

    monkeypatch.setattr(Application, "initialize", fake_initialize)
    return calls


@pytest.fixture
def bot_app(app_factory, no_telegram):
    async def build(overrides=None):
        app = app_factory({
            "console_token": FAKE_TOKEN,
            "console_admins": "111,222",
            **(overrides or {}),
        })
        await app.initialize()
        return app

    return build


def test_start_gets_past_config_read(bot_app, no_telegram):
    """app.initialize() 里那次 start() 必须能读完配置、建出 Application。

    走到 initialize 就说明配置读取和 builder 组装都没问题；
    读配置炸掉的话（比如把 Settings 当字典下标用）根本到不了这儿。
    """

    async def go():
        app = await bot_app()
        assert no_telegram, "没走到 PTB 的 initialize，说明前面就断了"
        assert app.booted, "导演 bot 起不来不该拖垮整个插件"
        # 连不上就把 application 收回 None，stop() 才不会去操作半成品
        assert app.director_bot.application is None
        await app.terminate()

    run(go())


def test_configured_and_admins(bot_app):
    async def go():
        app = await bot_app()
        assert app.director_bot.configured
        assert app.director_bot.admins() == ["111", "222"]
        await app.terminate()

    run(go())


def test_no_token_means_no_bot(app_factory, no_telegram):
    async def go():
        app = app_factory({"console_token": ""})
        await app.initialize()
        assert not app.director_bot.configured
        assert not no_telegram, "没配 token 就不该去碰 PTB"
        await app.terminate()

    run(go())


def test_admins_accepts_list_and_chinese_comma(bot_app):
    async def go():
        for raw, want in (
            (["1", " 2 ", ""], ["1", "2"]),
            ("1，2, 3", ["1", "2", "3"]),
            ("", []),
        ):
            app = await bot_app({"console_admins": raw})
            assert app.director_bot.admins() == want
            await app.terminate()

    run(go())
