"""面板端点冒烟：把每个注册的 Web API 真调一遍。

面板报 Internal Server Error 时，错误在 AstrBot 那层被吞成 500，
日志里未必看得出是哪一行——所以这里直接按注册表逐个调用，
让异常原样抛出来。
"""

import asyncio
import types

import pytest

from conftest import _FakeContext, _FakeStar



class _Query:
    def __init__(self, data: dict):
        self._d = data

    def get(self, key, default=None, type=None):
        v = self._d.get(key, default)
        if type is not None and v is not None:
            try:
                return type(v)
            except (TypeError, ValueError):
                return default
        return v

    def getlist(self, key):
        v = self._d.get(key)
        return v if isinstance(v, list) else ([v] if v is not None else [])


def _install_request_stub(query: dict, body: dict):
    """设定这一次请求的 query 与 body。

    注意必须**原地改属性**：handler 是 `from astrbot.api.web import request`
    在导入时绑定的对象，重新赋值 web.request 影响不到它——真实的 AstrBot
    也是同一个代理对象按请求解析属性。
    """
    import astrbot.api.web as web

    async def _json(default=None):
        return body if body is not None else (default or {})

    web.request.query = _Query(query)
    web.request.json = _json
    return web


@pytest.fixture
def panel_app(tmp_path, monkeypatch):
    from astrlover import app as app_mod

    monkeypatch.setattr(
        app_mod, "StarTools",
        types.SimpleNamespace(get_data_dir=lambda _n: str(tmp_path / "data")),
    )
    conf = {
        "life_enabled": True,
        "life_partner_id": "123",
        "console_token": "",
        "gallery_dir": str(tmp_path / "album"),
    }
    return app_mod.App(star=_FakeStar(conf), context=_FakeContext(), flat_conf=conf)


def run(coro):
    return asyncio.run(coro)


def _routes(app) -> dict:
    """从注册表里取出 路由 → handler。"""
    return dict(app.context.registered_web_apis)


def test_every_get_endpoint_responds(panel_app):
    """所有 GET 端点都要能返回，不能抛异常（抛了在 AstrBot 那边就是 500）。"""
    async def go():
        app = panel_app
        await app.initialize()
        routes = _routes(app)
        _install_request_stub({"kind": "f", "limit": 20}, {})

        failures = []
        for route in ("overview", "records", "records/kinds", "settings"):
            handler = routes[f"/astrlover/{route}"]
            try:
                out = await handler()
                assert out is not None, route
            except Exception as e:  # noqa: BLE001 — 就是要抓住任何异常
                failures.append(f"{route}: {type(e).__name__}: {e}")
        assert not failures, "端点报错：\n" + "\n".join(failures)
        await app.terminate()
    run(go())


def test_records_every_kind_renders(panel_app):
    """记录页每个类型都要能打开——包括一条都没有的时候。"""
    async def go():
        app = panel_app
        await app.initialize()
        handler = _routes(app)["/astrlover/records"]

        failures = []
        for kind, _label in app.records.KINDS:
            _install_request_stub({"kind": kind, "limit": 20}, {})
            try:
                out = await handler()
                assert isinstance(out["rows"], list), kind
            except Exception as e:  # noqa: BLE001
                failures.append(f"{kind}: {type(e).__name__}: {e}")
        assert not failures, "记录类型报错：\n" + "\n".join(failures)
        await app.terminate()
    run(go())


def test_records_kinds_with_data(panel_app):
    """有数据时每类也要能渲染（空表和非空表走的分支不同）。"""
    async def go():
        app = panel_app
        await app.initialize()
        r = app.records
        await r.add("f", "user 他不吃香菜")
        await r.add("m", "2026-04-20 认识的日子 since")
        await r.add("e", "去了咖啡店")
        await r.add("s", "14:00-16:00 和小雅逛街")
        await app.dao.save_diary(app.clock.today_str(), "今天挺好的")
        await app.dao.add_action("console_cmd", {"cmd": "/say 到家了"}, due_ts=1)
        await app.dao.add_mood("happy", 0.8, "他夸我了")
        await app.dao.save_cheatsheet("他怕冷")

        handler = _routes(app)["/astrlover/records"]
        failures = []
        for kind, _label in r.KINDS:
            _install_request_stub({"kind": kind, "limit": 20}, {})
            try:
                rows = (await handler())["rows"]
                assert rows, f"{kind} 应该有数据"
                for row in rows:
                    assert row["rid"] and isinstance(row["body"], str), f"{kind} 行结构不对"
            except Exception as e:  # noqa: BLE001
                failures.append(f"{kind}: {type(e).__name__}: {e}")
        assert not failures, "记录类型报错：\n" + "\n".join(failures)
        await app.terminate()
    run(go())


def test_post_endpoints(panel_app):
    """POST 端点：改设置、改记录、就地测试。"""
    async def go():
        app = panel_app
        await app.initialize()
        routes = _routes(app)

        _install_request_stub({}, {"values": {"vision_concurrency": "6"}})
        out = await routes["/astrlover/settings/save"]()
        assert "已保存" in out["message"]
        assert app.conf.get("vision_concurrency") == 6

        _install_request_stub({}, {"reset": "vision_concurrency"})
        assert "默认" in (await routes["/astrlover/settings/save"]())["message"]

        _install_request_stub({}, {"op": "add", "kind": "f", "text": "user 他怕冷"})
        assert "记下了" in (await routes["/astrlover/records/mutate"]())["message"]

        _install_request_stub({}, {"op": "edit", "rid": "f1", "text": "他很怕冷"})
        assert "改成" in (await routes["/astrlover/records/mutate"]())["message"]

        _install_request_stub({}, {"op": "del", "rid": "f1"})
        assert "已删除" in (await routes["/astrlover/records/mutate"]())["message"]

        # 未配置视觉/向量时也只能返回说明，不能抛
        for what in ("vision", "embed"):
            _install_request_stub({}, {"what": what})
            assert (await routes["/astrlover/probe"]())["message"]
        await app.terminate()
    run(go())


def test_export_endpoint(panel_app):
    """导出：VACUUM INTO 在事务里会炸，这个端点最容易 500。"""
    async def go():
        app = panel_app
        await app.initialize()
        _install_request_stub({}, {})
        out = await _routes(app)["/astrlover/export"]()
        assert out["file"].endswith(".zip")
        await app.terminate()
    run(go())


def test_overview_when_life_disabled(tmp_path, monkeypatch):
    """生命层关掉时总览也要能开（分支不同）。"""
    from astrlover import app as app_mod

    monkeypatch.setattr(
        app_mod, "StarTools",
        types.SimpleNamespace(get_data_dir=lambda _n: str(tmp_path / "data2")),
    )
    conf = {"life_enabled": False, "console_token": ""}
    app = app_mod.App(star=_FakeStar(conf), context=_FakeContext(), flat_conf=conf)

    async def go():
        await app.initialize()
        _install_request_stub({}, {})
        out = await _routes(app)["/astrlover/overview"]()
        assert out["ready"] is False and out["booted"] is True
        await app.terminate()
    run(go())
