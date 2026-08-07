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
