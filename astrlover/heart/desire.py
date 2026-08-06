"""A3 主动消息的意愿计算：时机由"想不想"决定，不是定时器。

纯代码打分，零 token；过阈值后由心跳调用 presence 的 _proactive_fire
（同一条导演生成+投递+写回历史的通道）。
门槛与 presence 共用：静默时段、连发未回上限、绑定目标；
presence 自带的随机倒计时（proactive_enable）开启时本引擎自动让位。
"""

import random
import time

from astrbot.api import logger

THRESHOLD = 0.55

_REASON_CN = {
    "morning": "想跟他道早安",
    "goodnight": "想跟他说晚安",
    "meal": "饭点了，想问问他吃了没",
    "silence": "太久没说话，想他了",
    "share": "有想跟他炫耀/分享的事",
    "special": "今天是特别的日子",
    "milestone": "纪念日到了",
    "miss": "就是想他",
}


def reason_cn(key: str) -> str:
    return _REASON_CN.get(key, key)


class Desire:
    def __init__(self, app):
        self.app = app

    async def _flag(self, kind: str) -> bool:
        return bool(await self.app.dao.kv_get(f"proactive_flag:{kind}:{self.app.clock.today_str()}"))

    async def mark(self, kind: str):
        await self.app.dao.kv_set(f"proactive_flag:{kind}:{self.app.clock.today_str()}", 1)

    async def evaluate(self) -> dict | None:
        app = self.app
        star = app.star
        now = time.time()

        if not app.cfg.proactive_enabled:
            return None
        if star.conf.get("proactive_enable", False):
            return None  # presence 的随机倒计时开着，让位，避免双重开火
        if not (star.state.get("director_target") or "").strip():
            return None  # 没绑定会话，无处可发
        if star._in_quiet():
            return None  # 静默时段

        # --- 硬门槛 ---
        st = star.state.get("proactive") or {}
        cap = int(star.conf.get("proactive_max_unanswered", 3) or 0)
        if cap > 0 and int(st.get("unanswered", 0) or 0) >= cap:
            return None  # 连发未回，先停下等他
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        if (now - last_user) / 60 < app.cfg.proactive_min_gap_minutes:
            return None
        last_any = await app.dao.last_chat_ts()
        if (now - last_any) / 60 < app.cfg.proactive_min_gap_minutes:
            return None

        sleeping = app.life.sleeping_now() if app.life else False
        in_goodnight = self._in_goodnight_window()
        if sleeping and not in_goodnight:
            return None  # 睡着了就是睡着了

        score, reasons = 0.0, []

        # --- 作息节律 ---
        if self._in_morning_window() and not await self._flag("morning"):
            score += 0.45
            reasons.append("morning")
        if in_goodnight and not await self._flag("goodnight"):
            score += 0.5
            reasons.append("goodnight")
        if self._in_meal_window() and not await self._flag("meal"):
            score += 0.25
            reasons.append("meal")

        # --- 沉默压力 ---
        silence_h = (now - last_any) / 3600 if last_any else 0.0
        max_h = max(1, app.cfg.max_silence_hours)
        score += min(0.6, 0.6 * silence_h / max_h)
        force = silence_h >= max_h
        if silence_h > max_h * 0.5:
            reasons.append("silence")

        # --- 事件驱动：有想炫耀的 ---
        shareworthy = [
            e for e in await app.dao.unmentioned_events(n=5, within_hours=24)
            if e["kind"] in ("avatar", "signature", "post", "appearance")
        ]
        if shareworthy:
            score += 0.35
            reasons.append("share")

        # --- 特别的日子 ---
        specials = app.clock.upcoming_specials(app.dynamic.milestones, app.profile.birthday, within_days=0)
        fests = app.clock.festivals_on(app.clock.today())
        if (specials or fests) and not await self._flag("special"):
            score += 0.4
            reasons.append("special")
        met_days = app.clock.days_since(app.profile.met_on)
        if met_days is not None and (met_days + 1) % 100 == 0 and not await self._flag("milestone"):
            score += 0.5
            reasons.append("milestone")

        # --- 情绪：想念 ---
        if app.mood:
            for m in await app.mood.current():
                if m["kind"] == "miss":
                    score += 0.2 * m["decayed"]
                    if "miss" not in reasons:
                        reasons.append("miss")

        score += random.uniform(-0.05, 0.05)

        if force and not reasons:
            reasons.append("silence")
        if score >= THRESHOLD or force:
            logger.info(f"[AstrLover] 主动意愿 {score:.2f}，理由：{reasons}")
            return {"reasons": reasons or ["miss"], "score": score}
        return None

    # ---- 时间窗口 ----
    def _now_min(self) -> int:
        n = self.app.clock.now()
        return n.hour * 60 + n.minute

    def _in_morning_window(self) -> bool:
        (wh, wm), _ = self.app.life.wake_sleep()
        cur = self._now_min()
        start = wh * 60 + wm
        return start <= cur <= start + 90

    def _in_goodnight_window(self) -> bool:
        _, (sh, sm) = self.app.life.wake_sleep()
        cur = self._now_min()
        sleep = sh * 60 + sm
        if sleep < 300:  # 跨零点睡（如 01:00）
            return cur >= (sleep + 1440 - 40) % 1440 or cur < sleep
        return sleep - 40 <= cur < sleep

    def _in_meal_window(self) -> bool:
        cur = self._now_min()
        return (12 * 60 <= cur <= 13 * 60) or (18 * 60 <= cur <= 19 * 60 + 30)
