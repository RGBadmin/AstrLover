"""她的一天：日程由她自己排，不由配置定义。

为什么不做成配置：作息写在人格里（「工作日早8:00起床，17:30下班，每周日单休」），
从自由文本里稳定提取不出来，提取出来还会跟人格脱钩——你改了人设，参数就过期了。
所以每天让她自己排一次明天：人格在上下文里，她当然知道自己几点起、哪天休息。
排出来的是**记录**，不是配置——排错了你直接改那条（/rec edit s12 …）。

心跳只读记录：几点睡几点醒来自当天的 wake/sleep 两条记录，
没有记录就当她随时在线（不做假设，也不硬编码作息）。
"""

import json
import re
from datetime import timedelta

from astrbot.api import logger

_PLAN_PROMPT = """现在给你自己排一下{when}（{date} {weekday}）的日程。
按你真实的作息和工作安排来——今天是不是要上班、几点起、几点睡，你自己清楚。
白天安排 1~3 件事就够，晚上一件，不用排满；具体到你会做什么（不是"工作"这种笼统的）。

只输出 JSON，不要解释：
{{"wake": "08:00", "sleep": "23:30", "items": [
  {{"start": "09:00", "end": "12:00", "what": "在公司前台，上午来了两拨访客"}},
  {{"start": "20:00", "end": "22:00", "what": "追剧"}}
]}}"""

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class LifeEngine:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ 生成
    async def ensure_today_plan(self):
        """今天还没有日程就排一个。一天一次轻调用。"""
        app = self.app
        date = app.clock.today_str()
        if await app.dao.day_schedule(date):
            return
        if not app.state_target:
            return  # 没绑定会话，借不到她的人格
        if await app.dao.kv_get(f"plan_tried:{date}"):
            return  # 今天试过且失败了，别反复烧 token
        await app.dao.kv_set(f"plan_tried:{date}", 1)
        await self._generate(date, "今天")

    async def _generate(self, date: str, when: str) -> bool:
        app = self.app
        weekday = _WEEKDAY_CN[app.clock.now().weekday()]
        try:
            raw = await app.bridge.generate(
                _PLAN_PROMPT.format(when=when, date=date, weekday=weekday), instruct=""
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 排日程失败：{e}")
            return False
        data = _extract_json(raw)
        if not isinstance(data, dict):
            logger.warning(f"[AstrLover] 日程不是合法 JSON，跳过：{raw[:80]}")
            return False

        rows = []
        if wake := _hm(data.get("wake")):
            rows.append({"kind": "wake", "start_hm": wake, "end_hm": wake, "activity": "起床"})
        for it in (data.get("items") or [])[:5]:
            start, end = _hm(it.get("start")), _hm(it.get("end"))
            what = str(it.get("what") or "").strip()
            if start and end and what:
                rows.append({"kind": "activity", "start_hm": start, "end_hm": end, "activity": what[:60]})
        if sleep := _hm(data.get("sleep")):
            rows.append({"kind": "sleep", "start_hm": sleep, "end_hm": sleep, "activity": "睡觉"})
        if not rows:
            return False
        await app.dao.replace_day_schedule(date, rows)
        logger.info(f"[AstrLover] {date} 的日程她自己排好了："
                    + "；".join(r["activity"] for r in rows if r["kind"] == "activity"))
        return True

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
        app = self.app
        if await self.sleeping_now():
            return "睡觉"
        now_hm = app.clock.now().strftime("%H:%M")
        for r in await app.dao.day_schedule(app.clock.today_str()):
            if r["kind"] == "activity" and r["start_hm"] <= now_hm < r["end_hm"] \
                    and r["status"] != "cancelled":
                return r["activity"]
        return "闲着，刷刷手机"

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
        rows = await app.dao.day_schedule(app.clock.today_str())
        lines = [f"你此刻：{await self.current_activity()}。"]
        if plan := "；".join(
            f"{r['start_hm']}~{r['end_hm']} {r['activity']}({r['status']})"
            for r in rows if r["kind"] == "activity"
        ):
            lines.append(f"你今天的安排：{plan}。")
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
