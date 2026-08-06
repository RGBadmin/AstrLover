"""端到端 smoke：用假的 AstrBot 环境真启动 App，跑通装配、钩子与相册流程。

不碰网络、不碰模型：视觉/Embedding/生图全部未配置，走降级路径——
这恰好验证"缺什么降级什么"的承诺。
"""

import asyncio
import sys
import types
from pathlib import Path

import pytest


class _FakeContext:
    def __init__(self):
        self.web_apis = []

    def register_web_api(self, route, handler, methods, desc):
        self.web_apis.append(route)

    def get_provider_by_id(self, _pid):
        return None

    def get_all_embedding_providers(self):
        return []

    def get_using_provider(self, **_kw):
        return None

    def get_platform_inst(self, _pid):
        return None


class _FakeStar:
    def __init__(self, conf):
        self.conf = conf


class _FakeReq:
    def __init__(self, contexts=None):
        self.contexts = contexts or []
        self.system_prompt = ""
        self.image_urls = []
        self.extra_user_content_parts = []


class _FakeEvent:
    def __init__(self, text="", sender="123"):
        self.message_str = text
        self.unified_msg_origin = "tg:FriendMessage:123"
        self._sender = sender
        self.sent = []

    def get_sender_id(self):
        return self._sender

    async def send(self, chain):
        self.sent.append(chain)


class _FakeResp:
    def __init__(self, text):
        self.completion_text = text


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    from astrlover import app as app_mod

    star_tools = types.SimpleNamespace(get_data_dir=lambda _n: str(tmp_path / "data"))
    monkeypatch.setattr(app_mod, "StarTools", star_tools)

    def make(conf_overrides=None):
        conf = {
            "life_enabled": True,
            "life_partner_id": "123",
            "life_timezone": "Asia/Shanghai",
            "console_token": "",            # 不起导演 bot
            "gallery_dir": str(tmp_path / "album"),
            "max_context_images": 1,
        }
        conf.update(conf_overrides or {})
        star = _FakeStar(conf)
        return app_mod.App(star=star, context=_FakeContext(), flat_conf=conf)

    return make


def run(coro):
    return asyncio.run(coro)


def test_boot_and_teardown(app_factory):
    async def go():
        app = app_factory()
        await app.initialize()
        assert app.booted and app.ready
        assert app.profile.name          # 档案模板已生成并加载
        assert (app.persona_dir / "profile.yaml").exists()
        assert app.db.conn is not None
        # 面板路由已注册
        assert any("overview" in r for r in app.context.web_apis)
        await app.terminate()
        assert not app.booted
    run(go())


def test_life_disabled_still_boots(app_factory):
    async def go():
        app = app_factory({"life_enabled": False})
        await app.initialize()
        assert app.booted and not app.ready
        assert app.album is not None     # presence 能力照常
        await app.terminate()
    run(go())


def test_hooks_survive_without_providers(app_factory):
    """没有视觉/向量/模型时，钩子必须静默降级而不是抛异常。"""
    async def go():
        app = app_factory()
        await app.initialize()
        event = _FakeEvent("在干嘛")
        req = _FakeReq([{"role": "user", "content": "Current datetime: 2026-08-06 14:30\n在干嘛"}])
        await app.on_llm_request(event, req)
        injected = req.system_prompt + "".join(
            getattr(p, "text", "") for p in req.extra_user_content_parts
        )
        assert app.profile.name in injected     # 她的人格进去了
        assert "铁律" in injected                # 硬约束在场

        resp = _FakeResp('好呀<improv>我妈是老师</improv><img_note id="1">测试</img_note>')
        await app.on_llm_response(event, resp)
        assert "<improv>" not in resp.completion_text
        assert "<img_note" not in resp.completion_text
        facts = await app.dao.list_facts(subject="self")
        assert any("老师" in f["content"] for f in facts)   # 编造已固化
        await app.terminate()
    run(go())


def test_album_scan_and_search_degrades(app_factory, tmp_path):
    async def go():
        album_dir = tmp_path / "album"
        (album_dir / "twitter" / "1@someone").mkdir(parents=True)
        (album_dir / "twitter" / "1@someone" / "state.archive").write_text("x")
        # 一张 snowflake 命名的图 + 一张普通图
        (album_dir / "twitter" / "1@someone" / "1813912299390087601-2.jpg").write_bytes(b"\xff\xd8\xff")
        (album_dir / "plain.png").write_bytes(b"\x89PNG")

        app = app_factory()
        await app.initialize()
        res = await app.album.scanner.scan()
        assert res["added"] == 2 and res["total"] == 2
        assert "someone" in res["folders"]

        rows = await app.album.next_pending(3, limit=10)
        assert len(rows) == 2
        snow = [r for r in rows if "1813912" in r["path"]][0]
        assert snow["shot_ts"] > 1600000000        # 从文件名还原了真实时间
        assert snow["folder"] == "someone"

        # 索引未跑 → 检索为空但不崩（向量库不可用时的降级）
        found, report = await app.album.search(keywords="黑丝")
        assert found == []
        assert "切词" in report.text()

        # 模拟索引完成后可被检索到
        await app.album.mark_ok(snow["id"], "露点---夏---无---无---黑丝,酒店\n她坐在窗边", "露点", "夏")
        found, _ = await app.album.search(keywords="黑丝")
        assert [r["id"] for r in found] == [snow["id"]]

        stats = await app.album.stats()
        assert stats["ok"] == 1 and stats["pending"] == 1
        await app.terminate()
    run(go())


def test_photo_archive_roundtrip(app_factory):
    async def go():
        import base64

        app = app_factory()
        await app.initialize()
        data_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" * 40).decode()
        pid = await app.photos.register_data_url(data_url, seen_ts=1754400000)
        assert pid == 1
        # 同一张图不会分到第二个编号
        assert await app.photos.register_data_url(data_url) == 1
        row = await app.photos.get(pid)
        assert app.photos.abs_path(row).exists()

        await app.photos.set_catalog(pid, "阿泽加班时拍的")
        assert (await app.photos.search("加班"))[0]["id"] == pid
        assert "阿泽加班" in await app.tools.inspect_photo(f"#{pid}")

        # 折叠：保留最近 1 张，更早的换成占位
        req = _FakeReq([
            {"role": "user", "content": [
                {"type": "text", "text": "Current datetime: 2026-08-06 10:00"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url.replace("/9", "/A")}},
            ]},
        ])
        await app.photo_memory.prune(req)
        first = req.contexts[0]["content"][1]
        assert first["type"] == "text" and "[图片 #1" in first["text"]
        assert "阿泽加班时拍的" in first["text"]     # 占位带着她的描述
        await app.terminate()
    run(go())


def test_console_commands_without_target(app_factory):
    async def go():
        from astrlover.director.console import DirectorConsole

        app = app_factory()
        await app.initialize()
        console = DirectorConsole(app)
        assert "指令一览" in await console.handle("/help")
        assert "还没绑定" in await console.handle("/link")
        out = await console.handle("/status")
        assert "AstrLover 状态" in out
        assert "未绑定" in out
        # 静默开关
        assert "先不回话" in await console.handle("/noreply")
        assert await app.silent_now() is True
        assert "可以开口" in await console.handle("/reply")
        assert await app.silent_now() is False
        # 排期
        out = await console.handle("/plan +30m /say 我到家啦")
        assert out.startswith("⏰")
        assert "我到家啦" in await console.handle("/plans")
        rows = await app.dao.pending_list(5)
        assert rows and rows[0]["payload"]["cmd"] == "/say 我到家啦"
        assert "已取消" in await console.handle(f"/plans cancel {rows[0]['id']}")
        await app.terminate()
    run(go())


def test_gallery_command_help_and_stats(app_factory):
    async def go():
        app = app_factory()
        await app.initialize()
        out = await app.gallery_command("")
        assert "相册目录" in out
        assert "登记 0 张" in out
        assert "未配置" in await app.vision_command("test")
        await app.terminate()
    run(go())
