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


def test_health_explains_every_failure(linked_app):
    """模块健康：每项都得说清楚为什么，光一个 ❌ 没法排查。"""

    async def go():
        app = await linked_app()
        from astrlover.panel.api import PanelApi

        rows = await PanelApi(app)._health()
        names = [h["name"] for h in rows]
        assert names == ["向量库", "视觉解析", "轻量模型", "生图", "语音"]
        for h in rows:
            assert h["why"], f"{h['name']} 没给理由"
            assert isinstance(h["ok"], bool)
        # 没配的那几项要指到具体去哪配
        vec = next(h for h in rows if h["name"] == "向量库")
        assert not vec["ok"] and "向量模型" in vec["why"]
        await app.terminate()

    run(go())


def test_vector_health_is_a_real_probe(linked_app):
    """向量库这项必须真去试一次。

    以前读的是 vectors.available——那是"初始化成功过没有"，而初始化是
    惰性的：配好了但还没人检索过就一直 False，看起来像没配好。
    """

    async def go():
        app = await linked_app()
        from astrlover.panel.api import PanelApi

        assert not app.vectors.available and not app.vectors._init_failed
        await PanelApi(app)._health()
        assert app.vectors._init_failed, "_health 没有真的去试"
        await app.terminate()

    run(go())
