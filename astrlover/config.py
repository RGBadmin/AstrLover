"""生命层配置视图：读插件压平后的扁平配置。

分组只是配置页的排版，代码一律按扁平 key 读。
presence 侧的配置直接用 app.conf[...]，不再包一层。
"""


class Cfg:
    def __init__(self, conf):
        self._c = conf              # Settings：接线 + 数据库覆盖 + 默认值

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
    def tts_provider_id(self) -> str:
        return self._s("life_tts_provider_id")

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
    def imagegen_slots(self) -> list[tuple[str, dict]]:
        """主用、备用两槽。返回 [(槽名, 配置)]，未配的自然被后端的 configured() 滤掉。

        类型专属的旋钮（ComfyUI 的 workflow、NovelAI 的步数、API 的出图尺寸）
        是全局一份——它们描述"那种供应商怎么跑"，不该跟着槽复制两遍。
        """
        c = self._c
        out = []
        for slot, prefix in (("主", "ig_main"), ("备", "ig_backup")):
            out.append((slot, {
                "type": str(c.get(f"{prefix}_type", "") or "").strip().lower(),
                "url": c.get(f"{prefix}_url", ""),
                "base_url": c.get(f"{prefix}_url", ""),   # ComfyUI 用这个名字
                "api_key": c.get(f"{prefix}_key", ""),
                "model": c.get(f"{prefix}_model", ""),
                # 类型专属
                "image_size": c.get("ig_api_image_size", "1K"),
                "workflow_file": c.get("ig_comfy_workflow", "comfyui_workflow.json"),
                "steps": c.get("ig_nai_steps", 24),
            }))
        return out
