"""生命层的控制台扩展：挂在最终插件类上的额外指令 + 自主执行入口。

presence 控制台按 CONSOLE_ROUTES 查名字、getattr(self, handler) 取方法，
所以这里的方法只要混进最终类、把路由注册进去就能用——不动 presence 一行。
新增：
  /status  她的生命状态（此刻/日程/心情/记忆/绑定）
  /diary   偷看日记        /events  生活事件流
  /plan    定时编排：把任意控制台指令排到未来执行
  /plans   看排期 · /plans cancel <id> 取消
"""

import re
import time
from datetime import datetime, timedelta

from astrbot.api import logger

_TIME_RE = re.compile(r"^(\+\d+(?:\.\d+)?[mh]|\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})$")

_PLAN_INTENT = (
    "把管理员的话解析成定时任务 JSON："
    '{"when": "+30m 或 HH:MM 或 YYYY-MM-DD HH:MM", "cmd": "/say 内容 或 /act 方向 或 /moment 主题 '
    '或 /avatar 或 /signature"}。'
    "「提醒他/跟他说」类用 /act（她自己组织语言）；要求原话转达用 /say；"
    "发动态用 /moment。解析不出时间就把 when 设为空字符串。只输出 JSON。"
)


class LifeConsoleMixin:
    # ------------------------------------------------------------------
    # 自主执行入口：以控制台语义跑一行指令（心跳冲动/定时任务用）
    # ------------------------------------------------------------------
    async def run_console_line(self, text: str) -> list[str]:
        """无控制台会话时的指令执行：回执不外发，返回给调用方记日志。"""
        from .presence.core import CONSOLE_AS_TARGET, CONSOLE_ROUTES, ConsoleEvent

        name, _, rest = text.lstrip("/").partition(" ")
        name = name.strip().lower()
        handler = CONSOLE_ROUTES.get(name)
        if handler is None:
            return [f"没有 /{name} 这个指令"]
        admins = self._console_admins()
        uid = admins[0] if admins else "0"
        if name in CONSOLE_AS_TARGET:
            target = (self.state.get("director_target") or "").strip()
            if not target:
                return ["未绑定目标会话（/link）"]
            client = self._platform_client(target)
            if client is None:
                return ["找不到目标会话的 bot 连接"]
            ev = ConsoleEvent(uid, 0, umo=target, client=client)
        else:
            ev = ConsoleEvent(uid, 0)
        func = getattr(self, handler)
        args, kwargs = self._bind_args(func, rest)
        async for _ in func(ev, *args, **kwargs):
            pass
        return ev.replies

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------
    async def cmd_life_status(self, event):
        """她的生命状态。用法：/status"""
        app = getattr(self, "app", None)
        if app is None or not app.ready:
            yield event.plain_result("生命模拟层未启用（配置 life_enabled）或初始化失败，看日志。")
            return
        lines = ["🌸 她的生命状态", ""]
        lines.append(app.clock.describe_now(app.profile.met_on, app.profile.anniversary))
        cur = await app.life.current_activity()
        lines.append(f"🧍 此刻：{cur}" + ("（睡眠时段）" if app.life.sleeping_now() else ""))
        sched = await app.dao.day_schedule(app.clock.today_str())
        if sched:
            lines.append("📅 " + "；".join(f"{s['start_hm']} {s['activity']}[{s['status']}]" for s in sched))
        mood = await app.mood.prompt_text()
        lines.append(f"💭 {mood or '心情平静。'}")
        facts = len(await app.dao.list_facts(limit=1000))
        diaries = await app.dao.recent_diaries(1)
        sheet = await app.dao.latest_cheatsheet()
        lines.append(
            f"🧠 记忆：事实 {facts} 条；小抄 v{sheet['version'] if sheet else 0}；"
            f"最近日记 {diaries[0]['date'] if diaries else '（还没写过）'}"
        )
        target = (self.state.get("director_target") or "").strip()
        lines.append(f"🔗 绑定会话：{target or '（未绑定，/umo 看、/link 绑）'}")
        st = self.state.get("proactive") or {}
        n = int(st.get("unanswered", 0) or 0)
        mode = "presence 倒计时" if self.conf.get("proactive_enable", False) else (
            "生命层意愿" if app.cfg.proactive_enabled else "关"
        )
        lines.append(f"💌 主动消息：{mode}" + (f"；连续未回 {n} 次" if n else ""))
        vec = "✅" if app.vectors.available else ("❌" if app.vectors._init_failed else "未激活")
        lines.append(f"🔌 记忆向量库 {vec}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # /diary /events
    # ------------------------------------------------------------------
    async def cmd_life_diary(self, event, date: str = ""):
        """偷看她的日记。用法：/diary [YYYY-MM-DD 或 YYYY-Www]"""
        app = getattr(self, "app", None)
        if app is None or not app.ready:
            yield event.plain_result("生命模拟层未启用。")
            return
        if date:
            row = await app.dao.get_diary(date, "weekly" if "W" in date.upper() else "daily")
        else:
            rows = await app.dao.recent_diaries(1, "daily")
            row = rows[0] if rows else None
        if not row:
            yield event.plain_result("（没有这篇日记）")
            return
        yield event.plain_result(f"📖 {row['date']}（{row['mood'] or '—'}）\n{row['content']}")

    async def cmd_life_events(self, event):
        """她最近的生活事件流。用法：/events"""
        app = getattr(self, "app", None)
        if app is None or not app.ready:
            yield event.plain_result("生命模拟层未启用。")
            return
        rows = await app.dao.recent_events(12)
        if not rows:
            yield event.plain_result("（还没有事件）")
            return
        mention = {"unmentioned": "未提及", "told": "已讲过", "discovered": "被发现"}
        yield event.plain_result("🧾 最近事件：\n" + "\n".join(
            f"#{r['id']} [{r['kind']}|{mention.get(r['mention_status'], r['mention_status'])}] "
            f"{r['description']}" + (f"｜动机：{r['motivation']}" if r["motivation"] else "")
            for r in rows
        ))

    # ------------------------------------------------------------------
    # /plan /plans —— 定时编排：任意控制台指令排到未来重放
    # ------------------------------------------------------------------
    async def cmd_life_plan(self, event, when: str = "", *, rest: str = ""):
        """定时编排。用法：/plan 20:00 /act 提醒他吃药，像自己惦记着一样
        也可以：/plan +30m /say 我到家啦 · /plan 今晚8点 提醒他吃药（自然语言）"""
        app = getattr(self, "app", None)
        if app is None or not app.ready:
            yield event.plain_result("生命模拟层未启用，排期功能不可用。")
            return
        raw = f"{when} {rest}".strip()
        if not raw:
            yield event.plain_result(
                "用法：/plan <时间> <指令或想让她做的事>\n"
                "时间：+30m / +2h / 20:00 / 2026-08-07 09:00，也可用自然语言\n"
                "例：/plan 20:00 /act 提醒他吃药\n　　/plan 明晚8点 让她发条关于晚霞的动态"
            )
            return

        due_ts, cmd = None, ""
        if _TIME_RE.match(when.strip()) and rest.strip():
            due_ts = self._parse_plan_when(when.strip())
            cmd = rest.strip() if rest.strip().startswith("/") else f"/act {rest.strip()}"
        else:
            intent = await app.llm.light_json(raw, system_prompt=_PLAN_INTENT)
            if isinstance(intent, dict) and intent.get("cmd"):
                due_ts = self._parse_plan_when(str(intent.get("when") or ""))
                cmd = str(intent["cmd"]).strip()
        if not cmd:
            yield event.plain_result("没解析出要做的事，试试「/plan 20:00 /act 提醒他……」的写法。")
            return
        if due_ts is None:
            yield event.plain_result("没解析出时间。支持 +30m / +2h / 20:00 / 2026-08-07 09:00。")
            return

        chat_id = getattr(event, "_console_umo", ":0").rsplit(":", 1)[-1]
        aid = await app.dao.add_action(
            "console_cmd",
            {"cmd": cmd, "chat_id": chat_id, "uid": event.get_sender_id()},
            due_ts=due_ts,
            source="director",
        )
        due_str = datetime.fromtimestamp(due_ts).strftime("%m-%d %H:%M")
        yield event.plain_result(f"⏰ 已排期 #{aid}：{due_str} 执行 {cmd}\n/plans 查看，/plans cancel {aid} 取消。")

    async def cmd_life_plans(self, event, action: str = "", arg: str = ""):
        """看/取消排期。用法：/plans · /plans cancel 3"""
        app = getattr(self, "app", None)
        if app is None or not app.ready:
            yield event.plain_result("生命模拟层未启用。")
            return
        if action.strip().lower() == "cancel" and arg.strip().isdigit():
            await app.dao.finish_action(int(arg), "cancelled")
            yield event.plain_result(f"已取消 #{arg}")
            return
        rows = await app.dao.pending_list(20)
        if not rows:
            yield event.plain_result("（没有排期）")
            return
        yield event.plain_result("⏰ 排期：\n" + "\n".join(
            f"#{r['id']} {datetime.fromtimestamp(r['due_ts']).strftime('%m-%d %H:%M')} "
            f"{r['payload'].get('cmd', r['kind'])}"
            for r in rows
        ))

    def _parse_plan_when(self, s: str) -> int | None:
        s = (s or "").strip()
        if not s:
            return None
        app = getattr(self, "app", None)
        now = app.clock.now() if app and app.clock else datetime.now()
        try:
            if s.startswith("+"):
                num = float(re.sub(r"[^\d.]", "", s))
                delta = timedelta(minutes=num) if s[-1].lower() == "m" else timedelta(hours=num)
                return int(time.time() + delta.total_seconds())
            if re.fullmatch(r"\d{1,2}:\d{2}", s):
                h, m = map(int, s.split(":"))
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return int(target.timestamp())
            target = datetime.fromisoformat(s)
            if target.tzinfo is None and now.tzinfo is not None:
                target = target.replace(tzinfo=now.tzinfo)
            return int(target.timestamp())
        except (ValueError, TypeError):
            return None


def register_console_routes():
    """把生命层指令并进 presence 控制台（模块导入时调用一次）。"""
    from .presence.core import CMD_HELP, CONSOLE_MENU, CONSOLE_ROUTES

    routes = {
        "status": "cmd_life_status",
        "diary": "cmd_life_diary",
        "events": "cmd_life_events",
        "plan": "cmd_life_plan",
        "plans": "cmd_life_plans",
    }
    for k, v in routes.items():
        CONSOLE_ROUTES.setdefault(k, v)
    menu_add = [
        ("status", "她的生命状态：此刻/日程/心情/记忆"),
        ("diary", "偷看日记 · /diary [日期]"),
        ("events", "她最近做的事（生活事件流）"),
        ("plan", "定时编排 · /plan 20:00 /act 提醒他……"),
        ("plans", "看排期 · /plans cancel <id> 取消"),
    ]
    existing = {name for name, _ in CONSOLE_MENU}
    for item in menu_add:
        if item[0] not in existing:
            CONSOLE_MENU.append(item)
    CMD_HELP.setdefault("plan", (
        "把一条控制台指令排到未来执行",
        [
            ("/plan 20:00 /act 提醒他吃药", "到点她像自己想起来一样去说"),
            ("/plan +30m /say 我到家啦", "半小时后原样说这句"),
            ("/plan 明晚8点 让她发条动态", "自然语言也认，会解析成指令"),
        ],
        "到点由心跳执行，回执发回你排期时所在的控制台会话。",
    ))
    logger.info("[AstrLover] 生命层控制台指令已注册：/status /diary /events /plan /plans")
