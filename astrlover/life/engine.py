"""A4 虚拟生活引擎：她的人生在你不在的时候也在继续。

- 每天按作息与活动池生成日程（纯代码，零 token）；
- 心跳推进日程状态；完成的活动概率性沉淀为生活事件（叙事素材）；
- "此刻在干什么"供对话上下文、回复节奏、即时自拍（A10）取用；
- 叙事连续性：日程一旦生成便固定，当天不再随机变化。
"""

import random
from datetime import timedelta

from astrbot.api import logger


def _parse_hm(s: str, default: str) -> tuple[int, int]:
    try:
        h, m = str(s or default).split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        h, m = default.split(":")
        return int(h), int(m)


def _hm_str(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


class LifeEngine:
    def __init__(self, app):
        self.app = app

    # ---- 作息 ----
    def _routine(self, weekend: bool) -> dict:
        r = self.app.profile.routine
        return (r.get("weekend") if weekend else r.get("weekday")) or {}

    def wake_sleep(self, weekend: bool | None = None) -> tuple[tuple[int, int], tuple[int, int]]:
        if weekend is None:
            weekend = self.app.clock.is_weekend()
        r = self._routine(weekend)
        return _parse_hm(r.get("wake"), "09:00"), _parse_hm(r.get("sleep"), "00:30")

    def sleeping_now(self) -> bool:
        now = self.app.clock.now()
        (wh, wm), (sh, sm) = self.wake_sleep()
        cur = now.hour * 60 + now.minute
        wake, sleep = wh * 60 + wm, sh * 60 + sm
        if sleep < wake:  # 跨零点睡（如 01:00 睡 09:30 醒）
            return cur >= sleep and cur < wake
        return cur >= sleep or cur < wake

    # ---- 日程生成（纯代码）----
    async def ensure_today_plan(self):
        app = self.app
        date = app.clock.today_str()
        if await app.dao.day_schedule(date):
            return
        weekend = app.clock.is_weekend()
        r = self._routine(weekend)
        day_pool = [str(x) for x in (r.get("day_pool") or ["宅家休息"])]
        night_pool = [str(x) for x in (r.get("night_pool") or ["刷手机放松"])]
        (wh, wm), (sh, sm) = self.wake_sleep(weekend)

        rng = random.Random(f"{date}:{app.profile.name}")  # 当天固定，重启不变
        items = []
        # 白天 1~2 项
        day_acts = rng.sample(day_pool, min(len(day_pool), rng.choice([1, 2])))
        cursor = wh + 1
        for act in day_acts:
            dur = rng.choice([2, 3, 4])
            start, end = cursor, min(cursor + dur, 18)
            if start >= 18:
                break
            items.append({"start_hm": _hm_str(start, rng.choice([0, 30])), "end_hm": _hm_str(end, 0), "activity": act})
            cursor = end + rng.choice([0, 1])
        # 晚上 1 项
        night_act = rng.choice(night_pool)
        items.append({"start_hm": "20:00", "end_hm": "23:00", "activity": night_act})

        await app.dao.replace_day_schedule(date, items)
        logger.info(f"[AstrLover] 生成 {date} 日程：" + "；".join(i["activity"] for i in items))

    # ---- 推进 ----
    async def advance(self):
        app = self.app
        date = app.clock.today_str()
        now_hm = app.clock.now().strftime("%H:%M")
        for item in await app.dao.day_schedule(date):
            if item["status"] == "planned" and item["start_hm"] <= now_hm < item["end_hm"]:
                await app.dao.set_schedule_status(item["id"], "ongoing")
            elif item["status"] in ("planned", "ongoing") and now_hm >= item["end_hm"]:
                await app.dao.set_schedule_status(item["id"], "done")
                # 概率沉淀为生活事件：她的叙事素材（A2/A4）
                if random.random() < 0.6:
                    await app.dao.add_event(
                        "life", f"{item['activity']}", motivation="", meta={"date": date}
                    )

    async def current_activity(self) -> str:
        app = self.app
        if self.sleeping_now():
            return "睡觉"
        date = app.clock.today_str()
        now_hm = app.clock.now().strftime("%H:%M")
        for item in await app.dao.day_schedule(date):
            if item["start_hm"] <= now_hm < item["end_hm"] and item["status"] != "cancelled":
                return item["activity"]
        return "闲着，刷刷手机"

    # ---- 供对话取用 ----
    async def prompt_text(self) -> str:
        app = self.app
        date = app.clock.today_str()
        sched = await app.dao.day_schedule(date)
        plan = "；".join(
            f"{s['start_hm']}~{s['end_hm']} {s['activity']}({s['status']})" for s in sched
        )
        cur = await self.current_activity()
        lines = [f"你此刻：{cur}。"]
        if plan:
            lines.append(f"你今天的安排：{plan}。")
        if self.sleeping_now():
            lines.append("你本来已经睡下了，是被消息吵醒或恰好没睡着，语气应当带着困意。")
        return "\n".join(lines)

    async def pre_reply_delay(self) -> float:
        """回复节奏：睡着/忙碌时晚一点回，回来要有交代（由提示词层保证）。"""
        if self.sleeping_now():
            return random.uniform(20, 75)
        cur = await self.current_activity()
        if any(k in cur for k in ("洗澡", "开会", "对稿", "牙医")):
            return random.uniform(15, 45)
        return 0.0

    # ---- 日记时机 ----
    def diary_due(self) -> str | None:
        """该写哪天的日记：睡前窗口写今天的；凌晨补昨天的。返回日期或 None。"""
        app = self.app
        now = app.clock.now()
        (_, _), (sh, sm) = self.wake_sleep()
        # 统一在 23:40 后写"今天"；若作息睡得早（23:00 前），提前到睡前 20 分钟
        due_min = min(23 * 60 + 40, sh * 60 + sm - 20) if sh >= 21 else 23 * 60 + 40
        cur_min = now.hour * 60 + now.minute
        if cur_min >= due_min:
            return app.clock.today_str()
        if now.hour >= 3:  # 凌晨之后兜底补昨天
            return (app.clock.today() - timedelta(days=1)).isoformat()
        return None
