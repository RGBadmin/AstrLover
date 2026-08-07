"""SQLite 存储：她的记忆、日记、人生的落地层。

aiosqlite（AstrBot 主程序自带依赖），WAL 模式。

v1.0 之前不做数据迁移：schema 变了就重建 astrlover.db，
表结构改起来不用背兼容包袱。

但"不迁移"不等于"不检查"——建表全是 CREATE TABLE IF NOT EXISTS，
旧库里缺的列永远补不上，代码要到几小时后读到那张表才 KeyError，
而且是在毫不相干的地方炸（面板、心跳、日程各炸一次）。
所以开库先对 schema_version：对不上就把旧库挪到 .bak 重建，
一次响亮的日志换掉一堆莫名其妙的崩溃。旧文件留着，随时能翻回去。
"""

import sqlite3
import time
from pathlib import Path

import aiosqlite

from astrbot.api import logger

# 4：schedule 加了 kind 列（wake/sleep/activity）。老库靠 CREATE TABLE
# IF NOT EXISTS 补不上这列，必须重建——之前版本号一直没跟着改，所以旧库
# 里存的也是 3，这次是把漏掉的那一跳补上。
SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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
    kind     TEXT NOT NULL DEFAULT 'activity', -- activity / wake / sleep
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

-- 相册（她的照片库：视觉解析产出的描述+分级+季节，向量在 FAISS）
CREATE TABLE IF NOT EXISTS album_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL UNIQUE,        -- 相册目录内相对路径
    folder       TEXT NOT NULL DEFAULT '',    -- .archive 标记的分类名
    shot_ts      INTEGER NOT NULL DEFAULT 0,  -- snowflake 还原或 mtime
    desc         TEXT NOT NULL DEFAULT '',
    rating       TEXT NOT NULL DEFAULT '',    -- 六档/相邻双档
    season       TEXT NOT NULL DEFAULT '',    -- 春夏秋冬组合或 四季
    status       TEXT NOT NULL DEFAULT 'pending', -- pending/ok/failed
    fails        INTEGER NOT NULL DEFAULT 0,  -- 只累计图片自身问题
    embedded     INTEGER NOT NULL DEFAULT 0,  -- 四段向量已建
    sent_count   INTEGER NOT NULL DEFAULT 0,
    last_sent_ts INTEGER NOT NULL DEFAULT 0,
    created_ts   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_album_status ON album_images (status, embedded);
CREATE INDEX IF NOT EXISTS idx_album_folder ON album_images (folder);

-- 聊天图片存档（图片记忆：两层描述，原图落盘 context_photos/）
CREATE TABLE IF NOT EXISTS photo_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- 即 [图片 #N] 的编号
    sha         TEXT NOT NULL UNIQUE,               -- 内容哈希去重
    file        TEXT NOT NULL,                      -- 数据目录相对路径
    seen_ts     INTEGER NOT NULL,                   -- 首次进入上下文
    catalog     TEXT NOT NULL DEFAULT '',           -- 目录层：她自己写的那句
    detail      TEXT NOT NULL DEFAULT '',           -- 细节层：视觉模型的画面记录
    detail_fail INTEGER NOT NULL DEFAULT 0
);

-- 纪念日（A12）：她自己记的、你手动加的，都在这
CREATE TABLE IF NOT EXISTS milestones (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,            -- YYYY-MM-DD
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'anniversary', -- anniversary(每年)/since(算天数)/once(一次性)
    source     TEXT NOT NULL DEFAULT 'user',        -- user/self
    created_ts INTEGER NOT NULL,
    UNIQUE (date, title)
);

-- 杂项键值（游标、计数器、演化状态）
CREATE TABLE IF NOT EXISTS kvmisc (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.path = db_path
        self.conn: aiosqlite.Connection | None = None
        self.was_reset = False      # 本次开库是不是重建过（向量库要跟着重来）

    # ------------------------------------------------------------------
    def _existing_version(self) -> int | None:
        """旧库的 schema_version。库不存在返回 None，读不出来当 0（老到没 meta 表）。"""
        if not self.path.exists():
            return None
        conn = None
        try:
            conn = sqlite3.connect(self.path)
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            if conn is not None:
                conn.close()

    def _reset_if_stale(self):
        old = self._existing_version()
        if old is None or old == SCHEMA_VERSION:
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        # WAL 的三个文件要一起挪，只挪主库会让新库接上旧 -wal
        for suffix in ("", "-wal", "-shm"):
            p = self.path.with_name(self.path.name + suffix)
            if p.exists():
                p.rename(p.with_name(f"{p.name}.v{old}.{stamp}.bak"))
        self.was_reset = True
        logger.warning(
            f"[AstrLover] 数据库是 v{old} 的、当前代码要 v{SCHEMA_VERSION}——"
            f"v1.0 前不做迁移，已重建空库。旧库改名保留为 "
            f"{self.path.name}.v{old}.{stamp}.bak，需要的话可以自己捞。"
        )

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._reset_if_stale()
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript(_SCHEMA)
        await self.conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
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
