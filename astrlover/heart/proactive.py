"""主动消息：你不说话时，她自己开口。

时机由"想不想"决定而不是定时器：作息窗口（早晚安/饭点）、纪念日、
有想炫耀的事、太久没说话、想他了——纯代码打分，过阈值才让模型开口。
防打扰：静默时段不打扰；连着几条没回就停下（她知道这件事，下一条不装作
没发生）；你一回复计数清零。
"""

import random
import time
from datetime import datetime

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


class Proactive:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    async def on_user_message(self):
        """他一开口：计数清零、时间戳更新。"""
        await self.app.dao.kv_set("last_user_ts", int(time.time()))
        await self.app.dao.kv_set("unanswered", 0)

    def in_quiet(self) -> bool:
        """静默时段（支持跨零点，如 23:30-08:30）。"""
        raw = str(self.app.star_conf.get("proactive_quiet") or "").strip()
        if "-" not in raw:
            return False
        try:
            a, b = (x.strip() for x in raw.split("-", 1))
            ah, am = (int(x) for x in a.split(":"))
            bh, bm = (int(x) for x in b.split(":"))
        except ValueError:
            logger.warning(f"[AstrLover] 静默时段「{raw}」格式不对，按不静默处理")
            return False
        now = self.app.clock.now() if self.app.clock else datetime.now()
        cur, start, end = now.hour * 60 + now.minute, ah * 60 + am, bh * 60 + bm
        return start <= cur < end if start <= end else (cur >= start or cur < end)

    # ------------------------------------------------------------------
    async def evaluate(self) -> list[str] | None:
        """纯代码打分。返回理由列表或 None。"""
        app = self.app
        if not app.cfg.proactive_enabled or not app.state_target:
            return None
        if self.in_quiet():
            return None
        cap = int(app.star_conf.get("proactive_max_unanswered", 3) or 0)
        unanswered = int(await app.dao.kv_get("unanswered", 0) or 0)
        if cap > 0 and unanswered >= cap:
            return None

        now = time.time()
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        last_fire = await app.dao.kv_get("last_fire_ts", 0) or 0
        gap_min = app.cfg.proactive_min_gap_minutes
        if last_user and (now - last_user) / 60 < gap_min:
            return None
        if last_fire and (now - last_fire) / 60 < gap_min:
            return None

        sleeping = await app.life.sleeping_now() if app.life else False
        in_goodnight = await self._window_goodnight()
        if sleeping and not in_goodnight:
            return None

        score, reasons = 0.0, []
        if await self._window_morning() and not await self._flag("morning"):
            score += 0.45
            reasons.append("morning")
        if in_goodnight and not await self._flag("goodnight"):
            score += 0.5
            reasons.append("goodnight")
        if self._window_meal() and not await self._flag("meal"):
            score += 0.25
            reasons.append("meal")

        silence_h = (now - max(last_user, last_fire)) / 3600 if (last_user or last_fire) else 0.0
        max_h = max(1, app.cfg.max_silence_hours)
        score += min(0.6, 0.6 * silence_h / max_h)
        force = silence_h >= max_h
        if silence_h > max_h * 0.5:
            reasons.append("silence")

        share = [
            e for e in await app.dao.unmentioned_events(n=5, within_hours=24)
            if e["kind"] in ("avatar", "signature", "post", "appearance")
        ]
        if share:
            score += 0.35
            reasons.append("share")

        specials = app.clock.upcoming_specials(await app.records.milestones(), 0)
        fests = app.clock.festivals_on(app.clock.today())
        if (specials or fests) and not await self._flag("special"):
            score += 0.4
            reasons.append("special")
        for m in await app.records.milestones():
            if str(m.get("kind")) != "since":
                continue
            days = app.clock.days_since(str(m.get("date", "")))
            if days is not None and (days + 1) % 100 == 0 and not await self._flag("milestone"):
                score += 0.5
                reasons.append("milestone")
                break

        if app.mood:
            for m in await app.mood.current():
                if m["kind"] == "miss":
                    score += 0.2 * m["decayed"]
                    if "miss" not in reasons:
                        reasons.append("miss")

        score += random.uniform(-0.05, 0.05)
        if score >= THRESHOLD or force:
            logger.info(f"[AstrLover] 主动意愿 {score:.2f}，理由：{reasons}")
            return reasons or ["miss"]
        return None

    # ------------------------------------------------------------------
    async def tick(self):
        """心跳调用：过阈值就开口。"""
        reasons = await self.evaluate()
        if reasons:
            await self.fire(reasons=reasons)

    async def fire(self, reasons: list[str] | None = None, force: bool = False) -> str:
        app = self.app
        if not app.state_target:
            return "还没绑定目标会话（/link），发不出去。"
        if not force and self.in_quiet():
            return "现在是静默时段，没发。"
        readable = "；".join(_REASON_CN.get(r, r) for r in (reasons or ["miss"]))
        unanswered = int(await app.dao.kv_get("unanswered", 0) or 0)
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        tz = app.clock.tz if app.clock else None
        brief = (
            f"你现在想主动联系他。你想找他的缘由：{readable}。\n"
            f"上一次他跟你说话是 "
            f"{datetime.fromtimestamp(last_user, tz).strftime('%m-%d %H:%M') if last_user else '不记得了'}"
            f"，现在是 {datetime.now(tz).strftime('%m-%d %H:%M')}。\n"
            "自然地开口，别报菜名式罗列理由，也别用「好久没聊了」这种机械开场。"
        )
        if unanswered:
            brief += (
                f"\n注意：你已经连着主动找过他 {unanswered} 次，他一次都没回。"
                "这一条要体现出你察觉到了，别装作前面没发生过。"
            )
        try:
            text = await app.bridge.generate(brief, instruct="")
        except Exception as e:
            logger.warning(f"[AstrLover] 主动消息生成失败：{e}")
            return f"生成失败：{e}"

        out = await app.bridge.deliver(text)
        await app.dao.kv_set("unanswered", unanswered + 1)
        await app.dao.kv_set("last_fire_ts", int(time.time()))
        for r in (reasons or []):
            await self._mark(r)
        await app.dao.add_event(
            "proactive", f"主动给他发了消息：{text[:60]}", motivation=readable
        )
        logger.info(f"[AstrLover] 主动消息已发（{readable}）")
        return out

    async def status(self) -> str:
        app = self.app
        tz = app.clock.tz if app.clock else None
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        last_fire = await app.dao.kv_get("last_fire_ts", 0) or 0
        unanswered = int(await app.dao.kv_get("unanswered", 0) or 0)
        cap = int(app.star_conf.get("proactive_max_unanswered", 3) or 0)
        lines = [f"主动消息：{'开' if app.cfg.proactive_enabled else '关'}（意愿驱动）"]
        if last_user:
            lines.append(f"他上次说话：{datetime.fromtimestamp(last_user, tz).strftime('%m-%d %H:%M')}")
        if last_fire:
            lines.append(f"她上次开口：{datetime.fromtimestamp(last_fire, tz).strftime('%m-%d %H:%M')}")
        if unanswered:
            lines.append(f"连续未获回复：{unanswered} 次" + (f"，满 {cap} 次就停" if cap else ""))
        if self.in_quiet():
            lines.append(f"⏸ 正在静默时段（{app.star_conf.get('proactive_quiet')}）")
        if not app.state_target:
            lines.append("⚠️ 还没绑定目标会话，发不出去。先 /link")
        lines.append(f"节奏：最小间隔 {app.cfg.proactive_min_gap_minutes} 分钟 · "
                     f"最长沉默 {app.cfg.max_silence_hours} 小时")
        lines.append("\n/proactive now 立刻让她发一条")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    async def _flag(self, kind: str) -> bool:
        day = self.app.clock.today_str() if self.app.clock else time.strftime("%Y-%m-%d")
        return bool(await self.app.dao.kv_get(f"pflag:{kind}:{day}"))

    async def _mark(self, kind: str):
        day = self.app.clock.today_str() if self.app.clock else time.strftime("%Y-%m-%d")
        await self.app.dao.kv_set(f"pflag:{kind}:{day}", 1)

    def _now_min(self) -> int:
        n = self.app.clock.now() if self.app.clock else datetime.now()
        return n.hour * 60 + n.minute

    async def _window_morning(self) -> bool:
        if not self.app.life:
            return False
        wake, _ = await self.app.life.wake_sleep()
        if wake is None:
            return False
        start = wake[0] * 60 + wake[1]
        return start <= self._now_min() <= start + 90

    async def _window_goodnight(self) -> bool:
        if not self.app.life:
            return False
        _, sleep = await self.app.life.wake_sleep()
        if sleep is None:
            return False
        cur, s = self._now_min(), sleep[0] * 60 + sleep[1]
        if s < 300:
            return cur >= (s + 1440 - 40) % 1440 or cur < s
        return s - 40 <= cur < s

    def _window_meal(self) -> bool:
        cur = self._now_min()
        return (12 * 60 <= cur <= 13 * 60) or (18 * 60 <= cur <= 19 * 60 + 30)
