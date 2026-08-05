"""测试桩：让纯逻辑模块在没有安装 AstrBot 的环境下可测。"""

import logging
import sys
import types
from pathlib import Path

# 仓库根目录进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 伪造 astrbot.api（仅 logger 等被纯逻辑模块引用的符号）
if "astrbot" not in sys.modules:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrlover-test")
    api.AstrBotConfig = dict
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
