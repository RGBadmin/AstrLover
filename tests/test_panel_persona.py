"""面板总览里的「人设读到了没有」。

之前这里写成 app.director_bot.bridge——桥其实挂在 app 上，
getattr(..., None) 把这个笔误吞了，于是绑好会话也永远显示"没读到"。
所以这里必须断言它真的为 True，而不只是"不炸"。
"""

import asyncio

import pytest

from conftest import _FakeContext


def run(coro):
    return asyncio.run(coro)


async def _overview(app):
    from astrlover.panel.api import PanelApi

    return await PanelApi(app)._persona_ok()


@pytest.fixture
def linked_app(app_factory):
    async def build(persona="我叫桃桃，在郑州做前台。", link=True):
        app = app_factory()
        app.context = _FakeContext(persona)
        await app.initialize()
        if link:
            await app.set_target("tg:FriendMessage:123")
        return app

    return build


def test_persona_ok_when_linked(linked_app):
    async def go():
        app = await linked_app()
        assert await _overview(app) is True
        await app.terminate()

    run(go())


def test_persona_not_ok_without_link(linked_app):
    """没绑会话就无从谈起——人格是按会话解析的。"""

    async def go():
        app = await linked_app(link=False)
        assert await _overview(app) is False
        await app.terminate()

    run(go())


def test_persona_not_ok_when_empty(linked_app):
    """绑了会话但那个会话没设人格。"""

    async def go():
        app = await linked_app(persona="")
        assert await _overview(app) is False
        await app.terminate()

    run(go())
