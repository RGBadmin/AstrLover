"""SQLite 存储：她的记忆、日记、人生的落地层。

- aiosqlite（AstrBot 主程序自带依赖），WAL 模式；
- 版本化迁移：schema_version 存于 meta 表，向后兼容升级。
"""

from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 自管对话历史（工作记忆底层）
CREATE TABLE IF NOT EXISTS chat_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    role    TEXT NOT NULL,              -- user / her
    kind    TEXT NOT NULL DEFAULT 'text', -- text/voice/photo/sticker/system
    content TEXT NOT NULL,
    meta    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_log (ts);

-- 结构化事实（A1 第三层；A6 编造固化也进这里，subject='self'）
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject    TEXT NOT NULL,            -- user / self / npc:小雅
    content    TEXT NOT NULL,            -- 原子事实
    category   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'active',  -- active / expired
    source     TEXT NOT NULL DEFAULT '',        -- chat/init/director/improvise
    created_ts INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL,
    vec_id     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts (subject, status);

-- 情景记忆：日记 / 周记（A1 第四层）
CREATE TABLE IF NOT EXISTS diary (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,            -- daily: YYYY-MM-DD / weekly: YYYY-Www
    type       TEXT NOT NULL DEFAULT 'daily',
    content    TEXT NOT NULL,
    mood       TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL,
    vec_id     TEXT NOT NULL DEFAULT '',
    UNIQUE (date, type)
);

-- 生活事件流（A2：内容/动机/提及状态）
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    kind           TEXT NOT NULL,        -- avatar/signature/post/proactive/appearance/life/gift...
    description    TEXT NOT NULL,        -- 内容描述（她随时能"回忆"）
    motivation     TEXT NOT NULL DEFAULT '',   -- 决策当时的真实理由
    mention_status TEXT NOT NULL DEFAULT 'unmentioned', -- unmentioned/told/discovered
    meta           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);

-- 日程（A4）
CREATE TABLE IF NOT EXISTS schedule (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,
    start_hm TEXT NOT NULL,
    end_hm   TEXT NOT NULL,
    activity TEXT NOT NULL,
    status   TEXT NOT NULL DEFAULT 'planned', -- planned/ongoing/done/cancelled
    notes    TEXT NOT NULL DEFAULT ''         -- 叙事细节，保证连续性
);
CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule (date);

-- 情绪（P1：有半衰期，绝不累积）
CREATE TABLE IF NOT EXISTS mood (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,          -- happy/excited/miss/sulk/blue...
    intensity     REAL NOT NULL,          -- 0~1
    cause         TEXT NOT NULL DEFAULT '',
    started_ts    INTEGER NOT NULL,
    half_life_min INTEGER NOT NULL DEFAULT 120,
    active        INTEGER NOT NULL DEFAULT 1
);

-- 核心小抄（A1 第二层，版本化，她自己修订）
CREATE TABLE IF NOT EXISTS cheatsheet (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    version    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    updated_ts INTEGER NOT NULL
);

-- 图库（R4）
CREATE TABLE IF NOT EXISTS gallery (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file         TEXT NOT NULL,           -- 数据目录相对路径
    category     TEXT NOT NULL DEFAULT 'life', -- selfie/life/scene/sticker
    desc         TEXT NOT NULL DEFAULT '',     -- 打标综合描述（检索语言）
    tags         TEXT NOT NULL DEFAULT '{}',   -- 结构化打标 JSON
    appearance   TEXT NOT NULL DEFAULT '{}',   -- 外观标签（发型等，A9 过滤用）
    source       TEXT NOT NULL DEFAULT 'user', -- user/gen
    is_anchor    INTEGER NOT NULL DEFAULT 0,   -- 外观锚点（生图参考）
    status       TEXT NOT NULL DEFAULT 'pending', -- pending/ok/failed
    created_ts   INTEGER NOT NULL,
    last_used_ts INTEGER NOT NULL DEFAULT 0,
    used_count   INTEGER NOT NULL DEFAULT 0,
    vec_id       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_gallery_cat ON gallery (category, status);

-- 待执行动作（D7：定时/延时统一队列，重启天然恢复）
CREATE TABLE IF NOT EXISTS pending_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    due_ts     INTEGER NOT NULL,
    kind       TEXT NOT NULL,             -- say/post/avatar/voice/proactive/...
    payload    TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending', -- pending/done/failed/cancelled
    source     TEXT NOT NULL DEFAULT 'self',    -- self/director
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_due ON pending_actions (status, due_ts);

-- 杂项键值（游标、计数器）
CREATE TABLE IF NOT EXISTS kvmisc (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.path = db_path
        self.conn: aiosqlite.Connection | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript(_SCHEMA)
        await self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await self.conn.commit()

    async def close(self):
        if self.conn is not None:
            await self.conn.commit()
            await self.conn.close()
            self.conn = None

    # ---- 轻量执行助手 ----
    async def execute(self, sql: str, params: tuple = ()) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid or cur.rowcount

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None
