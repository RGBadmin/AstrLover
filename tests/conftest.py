"""测试桩：让纯逻辑模块在没有安装 AstrBot 的环境下可测。"""

import logging
import sys
import types
from pathlib import Path

# 仓库根目录进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


try:
    from fastapi.encoders import jsonable_encoder as _jsonable
except ImportError:  # 没装 fastapi 就退回更严格的 json.dumps
    import json as _json

    def _jsonable(data):
        return _json.dumps(data, ensure_ascii=False)


def _install_astrbot_stub():
    if "astrbot" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []  # 声明为包，允许 astrbot.api.event 这类子模块

    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = logging.getLogger("astrlover-test")
    api.AstrBotConfig = dict

    event = types.ModuleType("astrbot.api.event")

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

        def message(self, text):
            self.chain.append(("text", text))
            return self

        def file_image(self, path):
            self.chain.append(("image", path))
            return self

    class AstrMessageEvent:  # 仅用于类型引用
        pass

    class _Filter:
        def __getattr__(self, _name):
            def deco(*_a, **_k):
                return lambda fn: fn
            return deco

    event.MessageChain = MessageChain
    event.AstrMessageEvent = AstrMessageEvent
    event.filter = _Filter()

    provider = types.ModuleType("astrbot.api.provider")
    provider.ProviderRequest = object

    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = object
    star.StarTools = object

    comps = types.ModuleType("astrbot.api.message_components")

    class Record:
        def __init__(self, file=None, text=None, **_kw):
            self.file, self.text = file, text

    class Image:
        def __init__(self, file=None, **_kw):
            self.file = file

    class Plain:
        def __init__(self, text="", **_kw):
            self.text = text

    comps.Record, comps.Image, comps.Plain = Record, Image, Plain

    web = types.ModuleType("astrbot.api.web")
    web.request = types.SimpleNamespace(json=None, query=None)

    def _json_response(data=None, **_k):
        # 真实的 json_response 会过 fastapi 的 jsonable_encoder，
        # 塞不进 JSON 的对象在那里炸 → 面板只显示一句 Internal server error。
        # 桩必须同样严格，否则这类 bug 测不出来。
        _jsonable(data)
        return data

    web.json_response = _json_response
    web.error_response = lambda msg, **_k: {"status": "error", "message": msg}
    web.file_response = lambda path, **_k: {"file": str(path)}
    web.PluginUploadFile = object

    api.event = event
    api.provider = provider
    api.star = star
    api.web = web
    api.message_components = comps
    astrbot.api = api

    sys.modules.update({
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.provider": provider,
        "astrbot.api.star": star,
        "astrbot.api.web": web,
        "astrbot.api.message_components": comps,
    })


_install_astrbot_stub()


# ---------------------------------------------------------------------------
# 共享 fixture：多个测试模块都要一个跑得起来的 App
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


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


class _FakePersonaManager:
    """AstrBot 的人格管理器：她是谁的唯一出处。

    真环境里插件只读不写；假环境给一份，才测得出"读到了没有"。
    """

    def __init__(self, text="我叫桃桃，在郑州做前台。"):
        self.text = text

    async def resolve_selected_persona(self, **_kw):
        if not self.text:
            raise RuntimeError("没有人格")
        return None, {"prompt": self.text}, None, None

    def get_persona_v3_by_id(self, _pid):
        return {"prompt": self.text} if self.text else None

    async def get_default_persona_v3(self, **_kw):
        return {"prompt": self.text} if self.text else None


class _FakeConfigMgr:
    @staticmethod
    def get_conf(_umo):
        return {"provider_settings": {}}


class _FakeContext:
    def __init__(self, persona="我叫桃桃，在郑州做前台。"):
        self.web_apis = []
        self.registered_web_apis = []     # 路由表：面板端点测试按它逐个调用
        self.history = []
        self.conversation_manager = _FakeConvManager(self)
        self.persona_manager = _FakePersonaManager(persona)
        self.astrbot_config_mgr = _FakeConfigMgr()

    def register_web_api(self, route, handler, methods, desc):
        self.web_apis.append(route)
        self.registered_web_apis.append((route, handler))

    def get_provider_by_id(self, _pid):
        return None

    def get_using_provider(self, **_kw):
        return None

    def get_platform_inst(self, _pid):
        return None


class _FakeStar:
    def __init__(self, conf):
        self.conf = conf


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


@pytest.fixture
def scheduled_app(app_factory):
    """带一天完整日程（起床/上班/睡觉）的 app。

    空表读不到 schedule.kind 列——线上那三处 KeyError 只有在有数据时才现形。
    """

    async def build():
        app = app_factory()
        await app.initialize()
        today = app.clock.today_str()
        await app.dao.set_rhythm(today, "08:00", "22:30")
        await app.dao.add_schedule_item(today, "09:00", "18:00", "上班", source="user")
        return app

    return build
