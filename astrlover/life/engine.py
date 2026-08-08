"""她的一天，分两层：作息 + 约定。

**作息**（wake/sleep）确实是按天的，每天问她一次几点起几点睡。
不做成配置：作息写在人格里（「工作日早8:00起床，每周日单休」），
从自由文本里稳定提取不出来，提取出来还会跟人格脱钩——你改了人设，
参数就过期了。让她自己说：人格在上下文里，她当然知道。

**约定**是提前定下来的事，跨天、稀疏、带具体日期——「8-12 14:00 跟小雅逛街」。
来源是聊天里真的约好了什么（记忆沉淀那一趟顺带抽出来），或者你手动加。
不铺满一天：真人的日程大部分时段是空的，只有少数几件事被钉在某个时刻。
没安排的时段就说没安排，不编——编出来的"她此刻在追剧"经不起追问。

心跳只读记录，没有记录就不做假设。
"""

import json
import re
from datetime import timedelta

from astrbot.api import logger

_RHYTHM_PROMPT = """今天是 {date} {weekday}。按你真实的作息，今天几点起、几点睡？

只输出 JSON，不要解释：{{"wake": "08:00", "sleep": "23:30"}}"""

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class LifeEngine:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ 作息
    async def ensure_today_rhythm(self):
        """今天还没问过作息就问一次。一天一次轻调用，只问两个时刻。"""
        app = self.app
        date = app.clock.today_str()
        wake, sleep = await self._bound(date)
        if wake and sleep:
            return
        if not app.state_target:
            return  # 没绑定会话，借不到她的人格
        if await app.dao.kv_get(f"rhythm_tried:{date}"):
            return  # 今天试过且失败了，别反复烧 token
        await app.dao.kv_set(f"rhythm_tried:{date}", 1)
        await self._generate_rhythm(date)

    async def _generate_rhythm(self, date: str) -> bool:
        app = self.app
        weekday = _WEEKDAY_CN[app.clock.now().weekday()]
        try:
            raw = await app.bridge.generate(
                _RHYTHM_PROMPT.format(date=date, weekday=weekday), instruct=""
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 问作息失败：{e}")
            return False
        data = _extract_json(raw)
        if not isinstance(data, dict):
            logger.warning(f"[AstrLover] 作息不是合法 JSON，跳过：{raw[:80]}")
            return False

        wake, sleep = _hm(data.get("wake")), _hm(data.get("sleep"))
        if not (wake and sleep):
            return False
        await app.dao.set_rhythm(date, wake, sleep)
        logger.info(f"[AstrLover] {date} 作息：{wake} 起，{sleep} 睡。")
        return True

    # ------------------------------------------------------------------ 约定
    async def add_commitment(self, date: str, start: str, end: str, what: str,
                             source: str = "chat") -> int:
        """记下一件提前定好的事。同一天同一时刻的同一件事不重复记。"""
        start, end = _hm(start), _hm(end) or _hm(start)
        what = str(what or "").strip()[:60]
        if not (date and start and what):
            return 0
        return await self.app.dao.add_schedule_item(date, start, end, what, source)

    # ------------------------------------------------------------------ 读记录
    async def _bound(self, date: str) -> tuple[str, str]:
        """当天的起床/睡觉时间；没有记录返回空串。"""
        rows = await self.app.dao.day_schedule(date)
        wake = next((r["start_hm"] for r in rows if r["kind"] == "wake"), "")
        sleep = next((r["start_hm"] for r in rows if r["kind"] == "sleep"), "")
        return wake, sleep

    async def wake_sleep(self) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        wake, sleep = await self._bound(self.app.clock.today_str())
        return _parse_hm(wake), _parse_hm(sleep)

    async def sleeping_now(self) -> bool:
        """睡着了没有。没有日程记录时不做假设——当她醒着。"""
        wake, sleep = await self.wake_sleep()
        if wake is None or sleep is None:
            return False
        now = self.app.clock.now()
        cur = now.hour * 60 + now.minute
        w, s = wake[0] * 60 + wake[1], sleep[0] * 60 + sleep[1]
        if s < w:                       # 跨零点睡（01:00 睡 / 09:00 醒）
            return cur >= s and cur < w
        return cur >= s or cur < w

    async def current_activity(self) -> str:
        """此刻在做的那件已安排的事；没有就返回空串。

        以前这里兜底成"闲着，刷刷手机"——但那是编的，而且日程一稀疏
        就成了常态。没安排就说没安排，让她自己临场发挥（那才有 <improv>）。
        """
        app = self.app
        if await self.sleeping_now():
            return "睡觉"
        now_hm = app.clock.now().strftime("%H:%M")
        for r in await app.dao.day_schedule(app.clock.today_str()):
            if r["kind"] == "activity" and r["start_hm"] <= now_hm < r["end_hm"] \
                    and r["status"] != "cancelled":
                return r["activity"]
        return ""

    # ------------------------------------------------------------------ 推进
    async def advance(self):
        app = self.app
        date = app.clock.today_str()
        now_hm = app.clock.now().strftime("%H:%M")
        for r in await app.dao.day_schedule(date):
            if r["kind"] != "activity":
                continue
            if r["status"] == "planned" and r["start_hm"] <= now_hm < r["end_hm"]:
                await app.dao.set_schedule_status(r["id"], "ongoing")
            elif r["status"] in ("planned", "ongoing") and now_hm >= r["end_hm"]:
                await app.dao.set_schedule_status(r["id"], "done")
                await app.dao.add_event("life", r["activity"], motivation="", meta={"date": date})

    async def prompt_text(self) -> str:
        app = self.app
        today = app.clock.today_str()
        rows = await app.dao.day_schedule(today)
        cur = await self.current_activity()
        lines = [f"你此刻：{cur}。" if cur else
                 "你此刻没有特别安排——在做什么由你自己临场决定，要合乎时间点和你的性格。"]
        if plan := "；".join(
            f"{r['start_hm']}~{r['end_hm']} {r['activity']}({r['status']})"
            for r in rows if r["kind"] == "activity"
        ):
            lines.append(f"你今天定好的事：{plan}。")
        # 往后几天已经约好的——「周六下午逛街」得让她周四就知道
        if upcoming := await app.dao.upcoming_schedule(today, days=7):
            lines.append("接下来定好的事：" + "；".join(
                f"{r['date']} {r['start_hm']} {r['activity']}" for r in upcoming) + "。")
        if await self.sleeping_now():
            lines.append("你已经睡下了，是被消息吵醒或恰好没睡着，语气该带着困意。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 日记时机
    async def diary_due(self) -> str | None:
        """睡前写今天的；凌晨补昨天的。没有作息记录时按 23:40 兜底。"""
        app = self.app
        now = app.clock.now()
        cur = now.hour * 60 + now.minute
        _wake, sleep = await self.wake_sleep()
        due = min(23 * 60 + 40, sleep[0] * 60 + sleep[1] - 20) if sleep and sleep[0] >= 21 else 23 * 60 + 40
        if cur >= due:
            return app.clock.today_str()
        if now.hour >= 3:
            return (app.clock.today() - timedelta(days=1)).isoformat()
        return None


# ---------------------------------------------------------------------- 工具
def _hm(v) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(v or ""))
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if 0 <= h < 24 and 0 <= mi < 60 else ""


def _parse_hm(s: str) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        h, m = s.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return None


def _extract_json(raw: str):
    raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", (raw or "").strip(), flags=re.S).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None
