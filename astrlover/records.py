"""记录：她的一切会变的东西都是记录，不是配置。

人格（AstrBot 人格设定）是唯一的固定事实源——她是谁、什么性格、怎么说话，
每次生成实时读取，你改了立刻生效，插件不复制、不提取、不缓存。
插件只维护随时间生长的部分：

    类型      自创建                        自销毁
    事实 f    对话沉淀、临场编造固化        失效且久未更新 → 清
    日记 d    每晚/每周她自己写            长期保留（她的内心世界）
    事件 e    她做了什么就记什么            写进日记且已提及后 → 清
    日程 s    她每天自己排明天的            过期 → 清
    纪念日 m  周记复盘时她记；你也能加      一次性的过期 → 清
    排期 p    /plan 或她自己的打算          完成 → 清
    情绪 o    事件触发                      半衰期归零 → 清

每类都能手动增删改（控制台 /rec、面板「记录」页），编号带前缀：f12 / e5 / m1。
另有几个单值状态（关系阶段、签名、外观基准）走 states，同样可改。
"""

import re
import time
from datetime import datetime

from astrbot.api import logger

_RID = re.compile(r"^\s*([a-z])(\d+)\s*$", re.I)

# 单值状态：她演化出来的、只有一份的东西
STATES = {
    "stage": "关系阶段",
    "appearance": "外观基准（生图用）",
    "signature": "当前签名",
    "avatar": "当前头像",
}

# 保留时长（天）。0 = 永不自动清理
KEEP_DAYS = {
    "events": 30,
    "schedule": 7,
    "plans": 7,          # 完成/取消后
    "facts": 60,         # 仅限已失效的
}


class Records:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ 读
    async def overview(self) -> str:
        db = self.app.db
        async def n(sql, params=()):
            row = await db.fetchone(sql, params)
            return int(row["n"]) if row else 0

        lines = ["📚 她的记录", ""]
        lines.append(f"f 事实　{await n('SELECT COUNT(*) n FROM facts WHERE status=?', ('active',))} 条"
                     f"（已失效 {await n('SELECT COUNT(*) n FROM facts WHERE status=?', ('expired',))}）")
        lines.append(f"d 日记　{await n('SELECT COUNT(*) n FROM diary WHERE type=?', ('daily',))} 篇"
                     f"　周记 {await n('SELECT COUNT(*) n FROM diary WHERE type=?', ('weekly',))} 篇")
        lines.append(f"e 事件　{await n('SELECT COUNT(*) n FROM events')} 条"
                     f"（还没跟他提过 {await n('SELECT COUNT(*) n FROM events WHERE mention_status=?', ('unmentioned',))}）")
        lines.append(f"s 日程　今天 {await n('SELECT COUNT(*) n FROM schedule WHERE date=?', (self.app.clock.today_str(),))} 项")
        lines.append(f"m 纪念日 {await n('SELECT COUNT(*) n FROM milestones')} 个")
        lines.append(f"p 排期　{await n('SELECT COUNT(*) n FROM pending_actions WHERE status=?', ('pending',))} 个待执行")
        lines.append(f"o 情绪　{await n('SELECT COUNT(*) n FROM mood WHERE active=1')} 个在场")
        lines.append("")
        lines.append("状态：" + "　".join(
            f"{label} {await self.get_state(key) or '—'}" for key, label in STATES.items()
        ))
        lines.append("\n/rec <类型> 看条目 · /rec add <类型> <内容> · /rec edit <编号> <内容> · /rec del <编号>")
        return "\n".join(lines)

    async def listing(self, kind: str, limit: int = 20) -> str:
        kind = (kind or "").strip().lower()
        fn = {
            "f": self._list_facts, "fact": self._list_facts, "facts": self._list_facts, "事实": self._list_facts,
            "d": self._list_diary, "diary": self._list_diary, "日记": self._list_diary,
            "e": self._list_events, "event": self._list_events, "events": self._list_events, "事件": self._list_events,
            "s": self._list_schedule, "schedule": self._list_schedule, "日程": self._list_schedule,
            "m": self._list_milestones, "milestone": self._list_milestones, "milestones": self._list_milestones, "纪念日": self._list_milestones,
            "p": self._list_plans, "plan": self._list_plans, "plans": self._list_plans, "排期": self._list_plans,
            "o": self._list_mood, "mood": self._list_mood, "情绪": self._list_mood,
        }.get(kind)
        if fn is None:
            return "类型是 f 事实 / d 日记 / e 事件 / s 日程 / m 纪念日 / p 排期 / o 情绪"
        return await fn(limit)

    async def _list_facts(self, limit: int) -> str:
        rows = await self.app.db.fetchall(
            "SELECT * FROM facts ORDER BY status, updated_ts DESC LIMIT ?", (limit,)
        )
        if not rows:
            return "（还没有事实）"
        return "\n".join(
            f"f{r['id']}｜{r['subject']}{'/' + r['category'] if r['category'] else ''}"
            f"{'｜已失效' if r['status'] != 'active' else ''}｜{r['content']}"
            for r in rows
        )

    async def _list_diary(self, limit: int) -> str:
        rows = await self.app.db.fetchall(
            "SELECT * FROM diary ORDER BY date DESC LIMIT ?", (limit,)
        )
        if not rows:
            return "（她还没写过日记）"
        return "\n".join(
            f"d{r['id']}｜{r['date']}{'（周记）' if r['type'] == 'weekly' else ''}｜{r['content'][:60]}…"
            for r in rows
        )

    async def _list_events(self, limit: int) -> str:
        rows = await self.app.db.fetchall("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        if not rows:
            return "（还没有事件）"
        m = {"unmentioned": "未提及", "told": "已讲过", "discovered": "被发现"}
        return "\n".join(
            f"e{r['id']}｜{r['kind']}｜{m.get(r['mention_status'], '')}｜{r['description']}"
            + (f"｜动机：{r['motivation']}" if r["motivation"] else "")
            for r in rows
        )

    async def _list_schedule(self, limit: int) -> str:
        date = self.app.clock.today_str()
        rows = await self.app.db.fetchall(
            "SELECT * FROM schedule WHERE date >= ? ORDER BY date, start_hm LIMIT ?", (date, limit)
        )
        if not rows:
            return "（今天还没有日程——她会在心跳里自己排）"
        return "\n".join(
            f"s{r['id']}｜{r['date']} {r['start_hm']}"
            + (f"~{r['end_hm']}" if r["kind"] == "activity" else "")
            + f"｜{r['activity']}｜{r['status']}"
            for r in rows
        )

    async def _list_milestones(self, limit: int) -> str:
        rows = await self.app.db.fetchall("SELECT * FROM milestones ORDER BY date LIMIT ?", (limit,))
        if not rows:
            return "（还没有纪念日。/rec add m 2026-04-20 认识的日子 since）"
        k = {"anniversary": "每年", "since": "算天数", "once": "一次性"}
        return "\n".join(
            f"m{r['id']}｜{r['date']}｜{r['title']}｜{k.get(r['kind'], r['kind'])}"
            f"｜{'她记的' if r['source'] == 'self' else '你加的'}"
            for r in rows
        )

    async def _list_plans(self, limit: int) -> str:
        rows = await self.app.dao.pending_list(limit)
        if not rows:
            return "（没有排期）"
        return "\n".join(
            f"p{r['id']}｜{datetime.fromtimestamp(r['due_ts']).strftime('%m-%d %H:%M')}"
            f"｜{r['payload'].get('cmd', r['kind'])}"
            for r in rows
        )

    async def _list_mood(self, limit: int) -> str:
        rows = await self.app.mood.current() if self.app.mood else []
        if not rows:
            return "（心情平静）"
        return "\n".join(
            f"o{r['id']}｜{r['kind']}｜强度 {r['decayed']:.2f}"
            + (f"｜因为{r['cause']}" if r["cause"] else "")
            for r in rows[:limit]
        )

    # ------------------------------------------------------------------ 结构化（面板用）
    KINDS = (
        ("f", "事实"), ("d", "日记"), ("e", "事件"), ("s", "日程"),
        ("m", "纪念日"), ("p", "排期"), ("o", "情绪"), ("state", "状态"),
    )

    async def rows(self, kind: str, limit: int = 50) -> list[dict]:
        """每条一行，面板据此渲染成可单独编辑/删除的卡片。

        chips 是标签、body 是可改的正文、meta 是不可改的附注；
        editable/deletable 决定卡片上出现哪些按钮。
        """
        k = (kind or "f").strip().lower()
        db = self.app.db
        if k == "f":
            rs = await db.fetchall("SELECT * FROM facts ORDER BY status, updated_ts DESC LIMIT ?", (limit,))
            return [{
                "rid": f"f{r['id']}",
                "chips": [r["subject"]] + ([r["category"]] if r["category"] else [])
                         + (["已失效"] if r["status"] != "active" else []),
                "body": r["content"],
                "meta": f"{_when(r['updated_ts'])} · 来自{_source_cn(r['source'])}",
                "editable": True, "deletable": True,
            } for r in rs]
        if k == "d":
            rs = await db.fetchall("SELECT * FROM diary ORDER BY date DESC LIMIT ?", (limit,))
            return [{
                "rid": f"d{r['id']}",
                "chips": [r["date"]] + (["周记"] if r["type"] == "weekly" else []),
                "body": r["content"], "meta": r["mood"] or "",
                "editable": True, "deletable": True, "multiline": True,
            } for r in rs]
        if k == "e":
            rs = await db.fetchall("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
            mention = {"unmentioned": "还没跟他提", "told": "已讲过", "discovered": "被他发现"}
            return [{
                "rid": f"e{r['id']}",
                "chips": [r["kind"], mention.get(r["mention_status"], "")],
                "body": r["description"],
                "meta": _when(r["ts"]) + (f" · 动机：{r['motivation']}" if r["motivation"] else ""),
                "editable": True, "deletable": True,
            } for r in rs]
        if k == "s":
            rs = await db.fetchall(
                "SELECT * FROM schedule WHERE date >= ? ORDER BY date, start_hm LIMIT ?",
                (self.app.clock.today_str(), limit))
            return [{
                "rid": f"s{r['id']}",
                "chips": [r["date"], r["start_hm"] + (f"~{r['end_hm']}" if r["kind"] == "activity" else ""),
                          r["status"]],
                "body": r["activity"], "meta": r["notes"] or "",
                "editable": True, "deletable": True,
            } for r in rs]
        if k == "m":
            rs = await db.fetchall("SELECT * FROM milestones ORDER BY date LIMIT ?", (limit,))
            kc = {"anniversary": "每年", "since": "算天数", "once": "一次性"}
            return [{
                "rid": f"m{r['id']}",
                "chips": [r["date"], kc.get(r["kind"], r["kind"])],
                "body": r["title"],
                "meta": "她自己记的" if r["source"] == "self" else "你加的",
                "editable": True, "deletable": True,
            } for r in rs]
        if k == "p":
            rs = await self.app.dao.pending_list(limit)
            return [{
                "rid": f"p{r['id']}",
                "chips": [datetime.fromtimestamp(r["due_ts"]).strftime("%m-%d %H:%M")],
                "body": str(r["payload"].get("cmd", r["kind"])),
                "meta": "到点由心跳执行",
                "editable": False, "deletable": True,
            } for r in rs]
        if k == "o":
            rs = await self.app.mood.current() if self.app.mood else []
            return [{
                "rid": f"o{r['id']}",
                "chips": [r["kind"], f"强度 {r['decayed']:.2f}"],
                "body": r["cause"] or "（没说原因）",
                "meta": "会自己消散",
                "editable": False, "deletable": True,
            } for r in rs[:limit]]
        if k == "state":
            out = []
            for key, label in STATES.items():
                out.append({
                    "rid": f"state:{key}", "chips": [label],
                    "body": await self.get_state(key), "meta": "只有一份，被新值覆盖",
                    "editable": True, "deletable": False,
                    "multiline": key == "appearance",
                })
            sheet = await self.app.dao.latest_cheatsheet()
            out.append({
                "rid": "state:cheatsheet",
                "chips": [f"核心小抄 v{sheet['version'] if sheet else 0}"],
                "body": sheet["content"] if sheet else "",
                "meta": "她自己修订；你改了她下次会在这个基础上继续写",
                "editable": True, "deletable": False, "multiline": True,
            })
            return out
        return []

    async def mutate(self, op: str, rid: str = "", kind: str = "", text: str = "") -> str:
        """面板统一入口：add / edit / del。"""
        if op == "add":
            return await self.add(kind, text)
        if op == "edit":
            if rid.startswith("state:"):
                return await self._set_state_row(rid[6:], text)
            return await self.edit(rid, text)
        if op == "del":
            return await self.delete(rid)
        return "op 必须是 add / edit / del"

    async def _set_state_row(self, key: str, text: str) -> str:
        if key == "cheatsheet":
            await self.app.dao.save_cheatsheet(text.strip(), reason="你手动改的")
            return "小抄已更新"
        return await self.set_state_cmd(key, text)

    # ------------------------------------------------------------------ 写
    async def add(self, kind: str, text: str) -> str:
        kind = (kind or "").strip().lower()
        text = (text or "").strip()
        if not text:
            return "内容是空的。"
        if kind in ("f", "fact", "facts", "事实"):
            # /rec add f user 他不吃香菜   （不写 subject 则默认 user）
            parts = text.split(None, 1)
            subject, content = (parts[0], parts[1]) if len(parts) == 2 and parts[0] in ("user", "self") else ("user", text)
            fid = await self.app.dao.add_fact(subject, content, source="user")
            if vec := await self.app.vectors.add_memory(content, {"type": "fact", "fact_id": fid, "ts": int(time.time())}):
                await self.app.dao.set_fact_vec(fid, vec)
            return f"记下了 f{fid}：({subject}) {content}"
        if kind in ("e", "event", "events", "事件"):
            eid = await self.app.dao.add_event("user", text, motivation="")
            return f"记下了 e{eid}：{text}"
        if kind in ("m", "milestone", "milestones", "纪念日"):
            # /rec add m 2026-04-20 认识的日子 [since|anniversary|once]
            bits = text.split()
            if not bits or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", bits[0]):
                return "用法：/rec add m 2026-04-20 认识的日子 [since|anniversary|once]"
            mkind = "anniversary"
            if len(bits) > 2 and bits[-1] in ("since", "anniversary", "once"):
                mkind, bits = bits[-1], bits[:-1]
            title = " ".join(bits[1:]) or "纪念日"
            mid = await self.add_milestone(bits[0], title, mkind, source="user")
            return f"记下了 m{mid}：{bits[0]} {title}（{mkind}）"
        if kind in ("s", "schedule", "日程"):
            # /rec add s 14:00-16:00 和小雅逛街          → 今天
            # /rec add s 2026-08-12 14:00-16:00 和小雅逛街 → 指定那天
            date = self.app.clock.today_str()
            if m := re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$", text):
                date, text = m.group(1), m.group(2)
            m = re.match(r"^(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})\s+(.+)$", text)
            if not m:
                return "用法：/rec add s [2026-08-12] 14:00-16:00 和小雅逛街（不写日期就是今天）"
            sid = await self.app.dao.add_schedule_item(
                date, m.group(1), m.group(2), m.group(3), source="user"
            )
            if not sid:
                return f"{date} {m.group(1)} 已经有同一件事了，没重复记。"
            return f"记下了 s{sid}：{date} {m.group(1)}~{m.group(2)} {m.group(3)}"
        return "能手动加的类型：f 事实 / e 事件 / m 纪念日 / s 日程"

    async def edit(self, rid: str, text: str) -> str:
        parsed = self._parse(rid)
        if parsed is None:
            return "编号格式不对，例如 f12 / e5 / m1。"
        prefix, num = parsed
        text = (text or "").strip()
        if not text:
            return "新内容是空的。"
        table_col = {
            "f": ("facts", "content"), "d": ("diary", "content"),
            "e": ("events", "description"), "s": ("schedule", "activity"),
            "m": ("milestones", "title"),
        }.get(prefix)
        if table_col is None:
            return "这类记录不支持改内容（排期用 /rec del 后重新 /plan；情绪会自己消散）。"
        table, col = table_col
        row = await self.app.db.fetchone(f"SELECT id FROM {table} WHERE id=?", (num,))
        if row is None:
            return f"没有 {rid} 这条记录。"
        await self.app.db.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (text, num))
        if prefix == "f":
            await self.app.db.execute("UPDATE facts SET updated_ts=? WHERE id=?", (int(time.time()), num))
        return f"{prefix}{num} 已改成：{text}"

    async def delete(self, rid: str) -> str:
        parsed = self._parse(rid)
        if parsed is None:
            return "编号格式不对，例如 f12 / e5 / m1。"
        prefix, num = parsed
        table = {"f": "facts", "d": "diary", "e": "events", "s": "schedule",
                 "m": "milestones", "p": "pending_actions", "o": "mood"}.get(prefix)
        if table is None:
            return "不认识这个编号前缀。"
        row = await self.app.db.fetchone(f"SELECT id FROM {table} WHERE id=?", (num,))
        if row is None:
            return f"没有 {rid} 这条记录。"
        await self.app.db.execute(f"DELETE FROM {table} WHERE id=?", (num,))
        return f"{rid} 已删除。"

    # ------------------------------------------------------------------ 纪念日
    async def add_milestone(self, date: str, title: str, kind: str = "anniversary",
                            source: str = "self") -> int:
        return await self.app.db.execute(
            "INSERT INTO milestones(date, title, kind, source, created_ts) VALUES (?,?,?,?,?) "
            "ON CONFLICT(date, title) DO NOTHING",
            (date, title, kind, source, int(time.time())),
        )

    async def milestones(self) -> list[dict]:
        return await self.app.db.fetchall("SELECT * FROM milestones ORDER BY date")

    # ------------------------------------------------------------------ 单值状态
    async def get_state(self, key: str) -> str:
        return str(await self.app.dao.kv_get(f"state:{key}", "") or "")

    async def set_state(self, key: str, value: str):
        await self.app.dao.kv_set(f"state:{key}", value)

    async def set_state_cmd(self, key: str, value: str) -> str:
        if key not in STATES:
            return "可设的状态：" + "、".join(f"{k}（{v}）" for k, v in STATES.items())
        await self.set_state(key, value)
        return f"{STATES[key]} 已设为：{value}"

    # ------------------------------------------------------------------ 自销毁
    async def cleanup(self) -> dict:
        """完成的、过期的记录自己消失。心跳每天调一次。"""
        db = self.app.db
        now = int(time.time())
        gone: dict[str, int] = {}

        async def purge(name: str, sql: str, params: tuple):
            n = await db.execute(sql, params)
            if n:
                gone[name] = n

        # 事件：讲过或被发现的旧事件才清；还没提过的留着（她还等着说呢）
        await purge("事件", "DELETE FROM events WHERE ts < ? AND mention_status != 'unmentioned'",
                    (now - KEEP_DAYS["events"] * 86400,))
        await purge("日程", "DELETE FROM schedule WHERE date < ?",
                    ((datetime.fromtimestamp(now - KEEP_DAYS["schedule"] * 86400)).strftime("%Y-%m-%d"),))
        await purge("排期", "DELETE FROM pending_actions WHERE status != 'pending' AND created_ts < ?",
                    (now - KEEP_DAYS["plans"] * 86400,))
        await purge("失效事实", "DELETE FROM facts WHERE status='expired' AND updated_ts < ?",
                    (now - KEEP_DAYS["facts"] * 86400,))
        await purge("消散的情绪", "DELETE FROM mood WHERE active=0", ())
        # 一次性纪念日过完就没意义了
        await purge("过期纪念日", "DELETE FROM milestones WHERE kind='once' AND date < ?",
                    ((datetime.fromtimestamp(now - 30 * 86400)).strftime("%Y-%m-%d"),))
        if gone:
            logger.info("[AstrLover] 记录清理：" + "、".join(f"{k} {v}" for k, v in gone.items()))
        return gone

    # ------------------------------------------------------------------
    @staticmethod
    def _parse(rid: str) -> tuple[str, int] | None:
        m = _RID.match(str(rid or ""))
        if not m:
            return None
        return m.group(1).lower(), int(m.group(2))


def _when(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _source_cn(src: str) -> str:
    return {"chat": "对话", "improvise": "她临场编的", "user": "你手动加的",
            "director": "控制台", "init": "初始"}.get(str(src), str(src) or "未知")
