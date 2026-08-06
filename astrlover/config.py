"""生命层配置视图：读插件压平后的扁平配置。

分组只是配置页的排版，代码一律按扁平 key 读。
presence 侧的配置直接用 app.star_conf[...]，不再包一层。
"""


class Cfg:
    def __init__(self, flat: dict):
        self._c = flat or {}

    def _s(self, key: str, default: str = "") -> str:
        v = self._c.get(key, default)
        return str(v).strip() if v is not None else default

    def _i(self, key: str, default: int) -> int:
        try:
            return int(self._c.get(key))
        except (TypeError, ValueError):
            return default

    # ---- 生命层 ----
    @property
    def enabled(self) -> bool:
        return bool(self._c.get("life_enabled", True))

    @property
    def timezone(self) -> str:
        return self._s("life_timezone", "Asia/Shanghai") or "Asia/Shanghai"

    @property
    def partner_id(self) -> str:
        """恋人 user id：优先 life_partner_id，退化为控制台管理员。"""
        if pid := self._s("life_partner_id"):
            return pid
        admins = self._c.get("console_admins")
        if isinstance(admins, list) and admins:
            return str(admins[0]).strip()
        return self._s("console_admins").split(",")[0].strip()

    @property
    def heartbeat_minutes(self) -> int:
        return max(1, self._i("life_heartbeat_minutes", 5))

    @property
    def light_provider_id(self) -> str:
        return self._s("life_light_provider_id")

    @property
    def embedding_provider_id(self) -> str:
        return self._s("life_embedding_provider_id")

    @property
    def tts_provider_id(self) -> str:
        return self._s("life_tts_provider_id")

    @property
    def stt_provider_id(self) -> str:
        return ""  # STT 由 AstrBot 主管线负责，本插件不参与

    # ---- 主动消息 ----
    @property
    def proactive_enabled(self) -> bool:
        return bool(self._c.get("life_proactive", True))

    @property
    def proactive_min_gap_minutes(self) -> int:
        return self._i("life_proactive_min_gap_minutes", 45)

    @property
    def max_silence_hours(self) -> int:
        return max(1, self._i("life_max_silence_hours", 30))

    # ---- 生图 ----
    @property
    def imagegen_order(self) -> list[str]:
        v = self._c.get("ig_backend_order", [])
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    def imagegen_backend(self, name: str) -> dict:
        c = self._c
        if name == "nanobanana":
            return {"api_key": c.get("ig_nb_api_key", ""), "base_url": c.get("ig_nb_base_url", ""),
                    "model": c.get("ig_nb_model", "")}
        if name == "comfyui":
            return {"base_url": c.get("ig_comfy_base_url", ""), "api_key": c.get("ig_comfy_api_key", ""),
                    "workflow_file": c.get("ig_comfy_workflow", "comfyui_workflow.json")}
        if name == "novelai":
            return {"api_key": c.get("ig_nai_api_key", ""), "model": c.get("ig_nai_model", "")}
        return {}
