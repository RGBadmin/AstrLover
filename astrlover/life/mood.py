"""情绪引擎（P1）：她可以有情绪，但情绪永远不能变成他的义务。

- 每种情绪带半衰期，指数衰减，不哄也会自己好，绝不累积；
- 对方任何回应都会加速负面情绪消散；哄一句直接雨过天晴；
- 表达层的"只许可爱"规则在 system prompt 铁律里，这里只管状态。
"""

import math
import time

from ..store.dao import Dao

NEGATIVE_KINDS = {"sulk", "blue", "jealous"}  # 委屈/低落/吃醋
_COAX_WORDS = (
    "乖", "抱抱", "亲亲", "摸摸", "对不起", "别生气", "别难过", "哄", "错了",
    "疼你", "爱你", "想你", "最好了", "宝贝",
)

_KIND_CN = {
    "happy": "开心",
    "excited": "兴奋",
    "miss": "想他",
    "sulk": "有点小委屈",
    "blue": "情绪有点低",
    "jealous": "有点小吃醋",
    "proud": "小得意",
}


class MoodEngine:
    def __init__(self, dao: Dao):
        self.dao = dao

    async def add(self, kind: str, intensity: float, cause: str = "", half_life_min: int = 120):
        await self.dao.add_mood(kind, intensity, cause, half_life_min)

    async def current(self) -> list[dict]:
        """带衰减的当前情绪；衰过阈值自动熄灭。"""
        rows = await self.dao.active_moods()
        now = time.time()
        alive = []
        for r in rows:
            age_min = (now - r["started_ts"]) / 60
            decayed = r["intensity"] * math.pow(0.5, age_min / max(1, r["half_life_min"]))
            if decayed < 0.15:
                await self.dao.deactivate_mood(r["id"])
                continue
            r["decayed"] = decayed
            alive.append(r)
        return alive

    async def on_user_message(self, text: str):
        """他开口了：负面情绪减半；哄的话直接雨过天晴。"""
        rows = await self.dao.active_moods()
        coaxed = any(w in (text or "") for w in _COAX_WORDS)
        now = int(time.time())
        for r in rows:
            if r["kind"] not in NEGATIVE_KINDS:
                continue
            if coaxed:
                await self.dao.deactivate_mood(r["id"])
            else:
                # 用"把开始时间拉近半衰期"的方式实现强度减半，无需新字段
                await self.dao.db.execute(
                    "UPDATE mood SET started_ts=? WHERE id=?",
                    (now - r["half_life_min"] * 60, r["id"]),
                )
        if coaxed and rows:
            await self.add("happy", 0.8, "被他哄了，立刻雨过天晴", half_life_min=180)

    async def prompt_text(self) -> str:
        moods = await self.current()
        if not moods:
            return ""
        parts = []
        for m in sorted(moods, key=lambda x: -x["decayed"])[:3]:
            desc = _KIND_CN.get(m["kind"], m["kind"])
            cause = f"（因为{m['cause']}）" if m["cause"] else ""
            level = "很" if m["decayed"] > 0.6 else "有点"
            parts.append(f"{level}{desc}{cause}")
        return "你现在的心情：" + "；".join(parts) + "。"
