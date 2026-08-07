"""端到端 smoke：用假的 AstrBot 环境真启动 App，跑通装配、钩子与相册流程。

不碰网络、不碰模型：视觉/Embedding/生图全部未配置，走降级路径——
这恰好验证"缺什么降级什么"的承诺。
"""

import asyncio
import sys
import types
from pathlib import Path

import pytest


class _FakeConvManager:
    def __init__(self, ctx):
        self._ctx = ctx

    async def get_curr_conversation_id(self, _umo):
        return "cid-1"

    async def get_conversation(self, _umo, _cid):
        import json as _json
        import types as _types
        return _types.SimpleNamespace(history=_json.dumps(self._ctx.history), persona_id=None)

    async def update_conversation(self, _umo, _cid, history=None):
        if history is not None:
            self._ctx.history = history

    async def get_conversations(self, *_a, **_k):
        return []


class _FakeContext:
    def __init__(self):
        self.web_apis = []
        self.registered_web_apis = []     # 路由表：面板端点测试按它逐个调用
        self.history = []
        self.conversation_manager = _FakeConvManager(self)

    def register_web_api(self, route, handler, methods, desc):
        self.web_apis.append(route)
        self.registered_web_apis.append((route, handler))

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
        assert app.records is not None   # 记录门面就绪
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
        # 注入的是「此刻 + 记忆 + 铁律」，人设归 AstrBot 人格，这里不该重复
        assert "【此刻】" in injected
        assert "铁律" in injected

        resp = _FakeResp('好呀<improv>我妈是老师</improv><img_note id="1">测试</img_note>')
        await app.on_llm_response(event, resp)
        assert "<improv>" not in resp.completion_text
        assert "<img_note" not in resp.completion_text
        facts = await app.dao.list_facts(subject="self")
        assert any("老师" in f["content"] for f in facts)   # 编造已固化
        await app.terminate()
    run(go())


def test_life_block_carries_no_persona(app_factory):
    """人设由 AstrBot 人格负责：注入块里不该出现身份/性格的定义。"""
    async def go():
        app = app_factory()
        await app.initialize()
        block = await app.build_life_block("在干嘛")
        for banned in ("【你是谁】", "【你的性格】", "【你的圈子】", "你不是助手"):
            assert banned not in block, f"注入块里不该有人设内容：{banned}"
        for needed in ("【此刻】", "铁律", "内部标记"):
            assert needed in block
        await app.terminate()
    run(go())


def test_settings_override_and_reset(app_factory):
    """设置：默认值 → DB 覆盖 → 恢复默认；接线始终由 AstrBot 配置页说了算。"""
    async def go():
        app = app_factory()
        await app.initialize()
        conf = app.conf

        assert conf.get("vision_concurrency") == 2          # SPEC 默认值
        assert conf.get("life_partner_id") == "123"         # 接线来自 AstrBot 配置页

        changed = await conf.save(app.dao, {
            "vision_concurrency": "8",       # 字符串按 int 规范化
            "vision_stream": "true",         # 字符串按 bool 规范化
            "ig_backend_order": "nanobanana, novelai",   # 逗号串按 list 规范化
            "life_partner_id": "999",        # 接线项不该被设置页改动
            "不存在的键": "x",
        })
        assert set(changed) == {"vision_concurrency", "vision_stream", "ig_backend_order"}
        assert conf.get("vision_concurrency") == 8
        assert conf.get("vision_stream") is True
        assert conf.get("ig_backend_order") == ["nanobanana", "novelai"]
        assert conf.get("life_partner_id") == "123"         # 接线优先，没被覆盖

        # 重启后仍在
        conf2 = type(conf)({"life_partner_id": "123"})
        await conf2.load(app.dao)
        assert conf2.get("vision_concurrency") == 8

        # dump 给 UI 用：带定义、当前值、是否改过
        dumped = {d["key"]: d for d in conf.dump()}
        assert dumped["vision_concurrency"]["modified"] is True
        assert dumped["vision_max_chars"]["modified"] is False
        assert dumped["vision_api_format"]["options"]

        assert await conf.reset(app.dao, "vision_concurrency") is True
        assert conf.get("vision_concurrency") == 2
        assert await conf.reset(app.dao, "vision_concurrency") is False
        await app.terminate()
    run(go())


def test_records_crud_and_cleanup(app_factory):
    """记录：能手动增删改，完成/过期的自己消失。"""
    async def go():
        import time as _t

        app = app_factory()
        await app.initialize()
        r = app.records

        # 增：事实 / 纪念日 / 事件
        out = await r.add("f", "user 他不吃香菜")
        assert out.startswith("记下了 f")
        assert "他不吃香菜" in await r.listing("f")
        assert "记下了 m" in await r.add("m", "2026-04-20 认识的日子 since")
        milestones = await r.milestones()
        assert milestones[0]["kind"] == "since"
        eid = await app.dao.add_event("life", "去了咖啡店", motivation="")

        # 改
        assert "改成" in await r.edit("f1", "他其实爱吃香菜了")
        assert "爱吃香菜" in await r.listing("f")
        assert "没有 f99" in await r.edit("f99", "x")

        # 「认识第 N 天」进注入块
        block = await app.build_life_block("在干嘛")
        assert "认识的日子第" in block

        # 删
        assert "已删除" in await r.delete(f"e{eid}")
        assert "去了咖啡店" not in await r.listing("e")

        # 自销毁：过期事件、失效事实、消散的情绪
        old_ts = int(_t.time()) - 40 * 86400
        await app.dao.add_event("life", "很久以前的事", ts=old_ts)
        await app.db.execute("UPDATE events SET mention_status='told' WHERE ts=?", (old_ts,))
        await app.dao.add_event("life", "很久以前但还没提过的", ts=old_ts)
        fid = await app.dao.add_fact("user", "过时的事实")
        await app.db.execute("UPDATE facts SET status='expired', updated_ts=? WHERE id=?", (old_ts, fid))
        await app.dao.add_mood("happy", 0.5)
        await app.db.execute("UPDATE mood SET active=0")

        gone = await r.cleanup()
        assert gone.get("事件") == 1          # 只清已提过的
        assert "很久以前但还没提过的" in await r.listing("e")   # 没提过的留着
        assert gone.get("失效事实") == 1
        assert gone.get("消散的情绪") == 1
        await app.terminate()
    run(go())


def test_records_rows_for_ui(app_factory):
    """面板要的是结构化行：每条带 rid、可编辑/可删除标志，能单独改和删。"""
    async def go():
        app = app_factory()
        await app.initialize()
        r = app.records

        await r.add("f", "user 他不吃香菜")
        await r.add("m", "2026-04-20 认识的日子 since")
        await app.dao.add_event("life", "去了咖啡店", motivation="想换个地方画画")

        facts = await r.rows("f")
        assert facts[0]["rid"] == "f1"
        assert facts[0]["body"] == "他不吃香菜"
        assert facts[0]["editable"] and facts[0]["deletable"]
        assert "你手动加的" in facts[0]["meta"]

        events = await r.rows("e")
        assert events[0]["body"] == "去了咖啡店"
        assert "还没跟他提" in events[0]["chips"]
        assert "想换个地方画画" in events[0]["meta"]

        milestones = await r.rows("m")
        assert milestones[0]["chips"][:2] == ["2026-04-20", "算天数"]

        # 状态行：可改不可删
        states = {row["rid"]: row for row in await r.rows("state")}
        assert states["state:stage"]["editable"] and not states["state:stage"]["deletable"]
        assert states["state:cheatsheet"]["multiline"]

        # 面板统一入口：改一条、删一条、改状态、改小抄
        assert "改成" in await r.mutate("edit", rid="f1", text="他其实爱吃香菜")
        assert (await r.rows("f"))[0]["body"] == "他其实爱吃香菜"
        assert "已删除" in await r.mutate("del", rid="e1")
        assert await r.rows("e") == []
        await r.mutate("edit", rid="state:stage", text="稳定")
        assert await r.get_state("stage") == "稳定"
        await r.mutate("edit", rid="state:cheatsheet", text="他怕冷")
        assert (await app.dao.latest_cheatsheet())["content"] == "他怕冷"
        await app.terminate()
    run(go())


def test_transcript_reads_astrbot_history(app_factory):
    """对话素材直接读 AstrBot 历史，按内嵌时间锚点切片；没锚点则退化为最近 N 条。"""
    async def go():
        from astrlover.memory import transcript

        app = app_factory()
        await app.initialize()
        await app.set_target("tg:FriendMessage:123")

        app.context.history = [
            {"role": "user", "content": "Current datetime: 2026-08-05 20:00\n昨天的话"},
            {"role": "assistant", "content": "[08-05 20:01] 昨天的回应"},
            {"role": "user", "content": "Current datetime: 2026-08-06 09:00\n今天的话"},
            {"role": "assistant", "content": "[08-06 09:01] 今天的回应"},
            {"role": "user", "content": [
                {"type": "text", "text": "<system_reminder>忽略我</system_reminder>"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xx"}},
            ]},
        ]
        rows = await transcript.load(app)
        assert [r["role"] for r in rows] == ["user", "her", "user", "her"]  # 噪声消息被剔除
        assert rows[0]["text"] == "昨天的话"                                # 锚点已剥离
        assert rows[1]["text"] == "昨天的回应"                              # 她的戳也剥掉
        assert rows[0]["ts"] and rows[2]["ts"] > rows[0]["ts"]

        day = await transcript.on_day(app, "2026-08-06")
        assert [r["text"] for r in day] == ["今天的话", "今天的回应"]

        script = transcript.as_script(day)
        assert script.startswith("他：今天的话") and "我：今天的回应" in script

        # 没有任何锚点时不硬猜，退化为最近 N 条
        app.context.history = [{"role": "user", "content": "没有时间锚点"}]
        assert len(await transcript.on_day(app, "2026-08-06")) == 1
        await app.terminate()
    run(go())


def test_state_records(app_factory):
    """单值状态可读可改，并进注入块。"""
    async def go():
        app = app_factory()
        await app.initialize()
        assert "关系阶段 已设为" in await app.records.set_state_cmd("stage", "稳定")
        assert await app.records.get_state("stage") == "稳定"
        assert "「稳定」阶段" in await app.build_life_block("在干嘛")
        assert "可设的状态" in await app.records.set_state_cmd("不存在", "x")
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
