"""配置封装：把 _conf_schema.json 的嵌套结构包成带类型默认值的只读属性。

原则（ailover.md 原则3）：这里只有"接线"与防打扰最小集合，
没有任何表现强度旋钮——表现由生命档案推导。
"""

from typing import Any


class Cfg:
    def __init__(self, raw: dict):
        self._raw = raw or {}

    def _g(self, *path: str, default: Any = "") -> Any:
        node: Any = self._raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node if node is not None else default

    def _int(self, *path: str, default: int) -> int:
        """整型读取：显式 0 是合法值，仅在缺失/非法时用默认值。"""
        v = self._g(*path, default=None)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    # ---- 接线 ----
    @property
    def main_platform_id(self) -> str:
        return str(self._g("wiring", "main_platform_id")).strip()

    @property
    def owner_id(self) -> str:
        return str(self._g("wiring", "owner_id")).strip()

    # ---- 导演 bot（插件自持，不经过 AstrBot 平台管理）----
    @property
    def director_token(self) -> str:
        return str(self._g("director", "bot_token")).strip()

    @property
    def director_proxy(self) -> str:
        return str(self._g("director", "proxy")).strip()

    @property
    def channel_id(self) -> str:
        return str(self._g("wiring", "channel_id")).strip()

    @property
    def discussion_group_id(self) -> str:
        return str(self._g("wiring", "discussion_group_id")).strip()

    @property
    def timezone(self) -> str:
        return str(self._g("wiring", "timezone", default="Asia/Shanghai")).strip() or "Asia/Shanghai"

    # ---- 模型分工 ----
    @property
    def chat_provider_id(self) -> str:
        return str(self._g("models", "chat_provider_id")).strip()

    @property
    def light_provider_id(self) -> str:
        return str(self._g("models", "light_provider_id")).strip()

    @property
    def vlm_provider_id(self) -> str:
        return str(self._g("models", "vlm_provider_id")).strip()

    @property
    def embedding_provider_id(self) -> str:
        return str(self._g("models", "embedding_provider_id")).strip()

    @property
    def tts_provider_id(self) -> str:
        return str(self._g("models", "tts_provider_id")).strip()

    @property
    def stt_provider_id(self) -> str:
        return str(self._g("models", "stt_provider_id")).strip()

    # ---- 生图 ----
    @property
    def imagegen_order(self) -> list[str]:
        v = self._g("imagegen", "backend_order", default=[])
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    def imagegen_backend(self, name: str) -> dict:
        v = self._g("imagegen", name, default={})
        return v if isinstance(v, dict) else {}

    # ---- 防打扰（A3 仅有的三个行为参数）----
    @property
    def min_gap_minutes(self) -> int:
        return self._int("proactive", "min_gap_minutes", default=45)

    @property
    def max_silence_hours(self) -> int:
        return self._int("proactive", "max_silence_hours", default=30)

    @property
    def max_unanswered(self) -> int:
        return self._int("proactive", "max_unanswered", default=3)

    # ---- 系统 ----
    @property
    def heartbeat_minutes(self) -> int:
        return max(1, self._int("system", "heartbeat_minutes", default=5))

    @property
    def debug(self) -> bool:
        return bool(self._g("system", "debug", default=False))

    # ---- 校验 ----
    def missing_required(self) -> list[str]:
        missing = []
        if not self.main_platform_id:
            missing.append("wiring.main_platform_id（主 bot 平台实例 ID）")
        if not self.owner_id:
            missing.append("wiring.owner_id（主人 user id）")
        return missing
