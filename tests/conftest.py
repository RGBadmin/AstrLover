"""测试桩：让纯逻辑模块在没有安装 AstrBot 的环境下可测。"""

import logging
import sys
import types
from pathlib import Path

# 仓库根目录进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    web.json_response = lambda data, **_k: data
    web.error_response = lambda msg, **_k: {"error": msg}
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
