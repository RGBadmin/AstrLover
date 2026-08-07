"""各表 DAO：业务层唯一的 SQL 入口，方法名即语义。"""

import json
import time

from .db import Database


def now_ts() -> int:
    return int(time.time())


def _j(obj) -> str:
    return json.dumps(obj or {}, ensure_ascii=False)


def _load_meta(row: dict, *fields: str) -> dict:
    for f in fields:
        try:
            row[f] = json.loads(row.get(f) or "{}")
        except (json.JSONDecodeError, TypeError):
            row[f] = {}
    return row


class Dao:
    def __init__(self, db: Database):
        self.db = db

    # ================= chat_log =================
    async def add_chat(self, role: str, content: str, kind: str = "text", meta: dict | None = None, ts: int | None = None) -> int:
        return await self.db.execute(
            "INSERT INTO chat_log(ts, role, kind, content, meta) VALUES (?,?,?,?,?)",
            (ts or now_ts(), role, kind, content, _j(meta)),
        )

    async def recent_chat(self, limit: int = 40) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM chat_log ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
        )
        return [_load_meta(r, "meta") for r in reversed(rows)]

    async def chat_between(self, ts0: int, ts1: int) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM chat_log WHERE ts >= ? AND ts < ? ORDER BY ts, id", (ts0, ts1)
        )
        return [_load_meta(r, "meta") for r in rows]

    async def last_chat_ts(self, role: str | None = None) -> int:
        if role:
            row = await self.db.fetchone(
                "SELECT ts FROM chat_log WHERE role = ? ORDER BY ts DESC LIMIT 1", (role,)
            )
        else:
            row = await self.db.fetchone("SELECT ts FROM chat_log ORDER BY ts DESC LIMIT 1")
        return int(row["ts"]) if row else 0

    # ================= facts =================
    async def add_fact(self, subject: str, content: str, category: str = "", source: str = "chat", vec_id: str = "") -> int:
        ts = now_ts()
        return await self.db.execute(
            "INSERT INTO facts(subject, content, category, status, source, created_ts, updated_ts, vec_id) "
            "VALUES (?,?,?,'active',?,?,?,?)",
            (subject, content, category, source, ts, ts, vec_id),
        )

    async def expire_fact(self, fact_id: int):
        await self.db.execute(
            "UPDATE facts SET status='expired', updated_ts=? WHERE id=?", (now_ts(), fact_id)
        )

    async def set_fact_vec(self, fact_id: int, vec_id: str):
        await self.db.execute("UPDATE facts SET vec_id=? WHERE id=?", (vec_id, fact_id))

    async def list_facts(self, subject: str | None = None, status: str = "active", limit: int = 200) -> list[dict]:
        if subject:
            return await self.db.fetchall(
                "SELECT * FROM facts WHERE subject=? AND status=? ORDER BY updated_ts DESC LIMIT ?",
                (subject, status, limit),
            )
        return await self.db.fetchall(
            "SELECT * FROM facts WHERE status=? ORDER BY updated_ts DESC LIMIT ?", (status, limit)
        )

    async def get_fact(self, fact_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM facts WHERE id=?", (fact_id,))

    # ================= diary =================
    async def save_diary(self, date: str, content: str, mood: str = "", dtype: str = "daily", vec_id: str = "") -> int:
        return await self.db.execute(
            "INSERT INTO diary(date, type, content, mood, created_ts, vec_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(date, type) DO UPDATE SET content=excluded.content, mood=excluded.mood",
            (date, dtype, content, mood, now_ts(), vec_id),
        )

    async def get_diary(self, date: str, dtype: str = "daily") -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM diary WHERE date=? AND type=?", (date, dtype)
        )

    async def recent_diaries(self, n: int = 2, dtype: str = "daily") -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM diary WHERE type=? ORDER BY date DESC LIMIT ?", (dtype, n)
        )
        return list(reversed(rows))

    # ================= events =================
    async def add_event(self, kind: str, description: str, motivation: str = "", meta: dict | None = None, ts: int | None = None) -> int:
        return await self.db.execute(
            "INSERT INTO events(ts, kind, description, motivation, mention_status, meta) "
            "VALUES (?,?,?,?,'unmentioned',?)",
            (ts or now_ts(), kind, description, motivation, _j(meta)),
        )

    async def recent_events(self, n: int = 10, kinds: list[str] | None = None) -> list[dict]:
        if kinds:
            marks = ",".join("?" * len(kinds))
            rows = await self.db.fetchall(
                f"SELECT * FROM events WHERE kind IN ({marks}) ORDER BY ts DESC LIMIT ?",
                (*kinds, n),
            )
        else:
            rows = await self.db.fetchall("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (n,))
        return [_load_meta(r, "meta") for r in rows]

    async def unmentioned_events(self, n: int = 5, within_hours: int = 96) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM events WHERE mention_status='unmentioned' AND ts > ? ORDER BY ts DESC LIMIT ?",
            (now_ts() - within_hours * 3600, n),
        )
        return [_load_meta(r, "meta") for r in rows]

    async def set_event_mention(self, event_id: int, status: str):
        await self.db.execute("UPDATE events SET mention_status=? WHERE id=?", (status, event_id))

    async def latest_event(self, kind: str) -> dict | None:
        row = await self.db.fetchone(
            "SELECT * FROM events WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,)
        )
        return _load_meta(row, "meta") if row else None

    # ================= schedule =================
    async def replace_day_schedule(self, date: str, items: list[dict]):
        await self.db.execute("DELETE FROM schedule WHERE date=? AND status='planned'", (date,))
        for it in items:
            await self.db.execute(
                "INSERT INTO schedule(date, kind, start_hm, end_hm, activity, status, notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (date, it.get("kind", "activity"), it["start_hm"], it["end_hm"],
                 it["activity"], it.get("status", "planned"), it.get("notes", "")),
            )

    async def day_schedule(self, date: str) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM schedule WHERE date=? ORDER BY start_hm", (date,)
        )

    async def set_schedule_status(self, item_id: int, status: str, notes: str | None = None):
        if notes is None:
            await self.db.execute("UPDATE schedule SET status=? WHERE id=?", (status, item_id))
        else:
            await self.db.execute(
                "UPDATE schedule SET status=?, notes=? WHERE id=?", (status, notes, item_id)
            )

    # ================= mood =================
    async def add_mood(self, kind: str, intensity: float, cause: str = "", half_life_min: int = 120) -> int:
        return await self.db.execute(
            "INSERT INTO mood(kind, intensity, cause, started_ts, half_life_min, active) VALUES (?,?,?,?,?,1)",
            (kind, max(0.0, min(1.0, intensity)), cause, now_ts(), half_life_min),
        )

    async def active_moods(self) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM mood WHERE active=1 ORDER BY started_ts DESC")

    async def deactivate_mood(self, mood_id: int):
        await self.db.execute("UPDATE mood SET active=0 WHERE id=?", (mood_id,))

    # ================= cheatsheet =================
    async def latest_cheatsheet(self) -> dict | None:
        return await self.db.fetchone("SELECT * FROM cheatsheet ORDER BY version DESC LIMIT 1")

    async def save_cheatsheet(self, content: str, reason: str = "") -> int:
        latest = await self.latest_cheatsheet()
        version = (latest["version"] + 1) if latest else 1
        await self.db.execute(
            "INSERT INTO cheatsheet(version, content, reason, updated_ts) VALUES (?,?,?,?)",
            (version, content, reason, now_ts()),
        )
        return version


    # ================= pending_actions =================
    async def add_action(self, kind: str, payload: dict, due_ts: int | None = None, source: str = "self") -> int:
        return await self.db.execute(
            "INSERT INTO pending_actions(due_ts, kind, payload, status, source, created_ts) "
            "VALUES (?,?,?,'pending',?,?)",
            (due_ts or now_ts(), kind, _j(payload), source, now_ts()),
        )

    async def due_actions(self, now: int | None = None) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM pending_actions WHERE status='pending' AND due_ts <= ? ORDER BY due_ts",
            (now or now_ts(),),
        )
        return [_load_meta(r, "payload") for r in rows]

    async def pending_list(self, limit: int = 50) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM pending_actions WHERE status='pending' ORDER BY due_ts LIMIT ?", (limit,)
        )
        return [_load_meta(r, "payload") for r in rows]

    async def finish_action(self, action_id: int, status: str = "done"):
        await self.db.execute("UPDATE pending_actions SET status=? WHERE id=?", (status, action_id))

    # ================= kvmisc =================
    async def kv_get(self, key: str, default=None):
        row = await self.db.fetchone("SELECT value FROM kvmisc WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    async def kv_set(self, key: str, value):
        await self.db.execute(
            "INSERT INTO kvmisc(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
