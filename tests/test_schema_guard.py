"""旧库自检：建表全是 IF NOT EXISTS，缺列的老库必须被挡下来重建。

这类 bug 在空库上测不出来——SELECT * 拿到空列表就不会读到缺的列，
所以每个用例都往老库里塞一行真数据。
"""

import asyncio
import sqlite3

from astrlover.store.db import SCHEMA_VERSION, Database


def run(coro):
    return asyncio.run(coro)


def _make_old_db(path, version, with_kind=False):
    """造一个旧版 astrlover.db：schedule 表按老结构建，塞一行。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if version is not None:
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(version),))
    cols = "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, start_hm TEXT, end_hm TEXT, activity TEXT, status TEXT, notes TEXT"
    if with_kind:
        cols += ", kind TEXT NOT NULL DEFAULT 'activity'"
    conn.execute(f"CREATE TABLE schedule ({cols})")
    conn.execute(
        "INSERT INTO schedule(date, start_hm, end_hm, activity, status, notes) "
        "VALUES ('2026-08-07','09:00','10:00','上班','planned','')"
    )
    conn.commit()
    conn.close()


def test_stale_db_is_rebuilt(tmp_path):
    """版本对不上 → 旧库改名让开，新库按当前 schema 建，kind 列在。"""
    path = tmp_path / "astrlover.db"
    _make_old_db(path, SCHEMA_VERSION - 1)

    async def go():
        db = Database(path)
        await db.open()
        assert db.was_reset
        await db.execute(
            "INSERT INTO schedule(date, kind, start_hm, end_hm, activity) "
            "VALUES ('2026-08-07','wake','08:00','08:00','起床')"
        )
        rows = await db.fetchall("SELECT * FROM schedule WHERE date='2026-08-07'")
        assert rows and rows[0]["kind"] == "wake"   # 老库读这个键会 KeyError
        assert len(rows) == 1                       # 旧数据没跟过来
        await db.close()

    run(go())
    baks = list(tmp_path.glob("astrlover.db.v*.bak"))
    assert len(baks) == 1, "旧库要留一份，不能直接删"
    old = sqlite3.connect(baks[0])
    assert old.execute("SELECT COUNT(*) FROM schedule").fetchone()[0] == 1
    old.close()


def test_current_db_is_kept(tmp_path):
    """版本一致 → 不动，数据要还在。"""
    path = tmp_path / "astrlover.db"

    async def first():
        db = Database(path)
        await db.open()
        assert not db.was_reset
        await db.execute(
            "INSERT INTO schedule(date, kind, start_hm, end_hm, activity) "
            "VALUES ('2026-08-07','activity','09:00','10:00','上班')"
        )
        await db.close()

    async def second():
        db = Database(path)
        await db.open()
        assert not db.was_reset, "同版本重开不该重建"
        rows = await db.fetchall("SELECT * FROM schedule")
        assert len(rows) == 1 and rows[0]["kind"] == "activity"
        await db.close()

    run(first())
    run(second())
    assert not list(tmp_path.glob("*.bak"))


def test_db_without_meta_is_rebuilt(tmp_path):
    """老到连 schema_version 都没写过的库，也要重建。"""
    path = tmp_path / "astrlover.db"
    _make_old_db(path, None)

    async def go():
        db = Database(path)
        await db.open()
        assert db.was_reset
        await db.close()

    run(go())
    assert list(tmp_path.glob("astrlover.db.v0.*.bak"))


def test_wal_db_leaves_nothing_behind(tmp_path):
    """WAL 模式的旧库（进程被 docker stop 硬杀那种）重建后不能留下旧 WAL。

    探测版本时正常打开旧库，SQLite 会把 -wal 归并回主库再删掉，
    随后主库整个改名成 .bak——数据一份不丢，新库这边干干净净。
    """
    path = tmp_path / "astrlover.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION - 1),))
    conn.execute("CREATE TABLE schedule (id INTEGER PRIMARY KEY, date TEXT, activity TEXT)")
    conn.execute("INSERT INTO schedule(date, activity) VALUES ('2026-08-07','上班')")
    conn.commit()
    conn.close()

    async def go():
        db = Database(path)
        await db.open()
        assert db.was_reset
        assert await db.fetchall("SELECT * FROM schedule") == []   # 新库是空的
        await db.close()

    run(go())
    for suffix in ("-wal", "-shm"):
        live = tmp_path / f"astrlover.db{suffix}"
        assert not live.exists() or live.stat().st_size < 100_000
    bak = list(tmp_path.glob("astrlover.db.v*.bak"))
    assert len(bak) == 1
    old = sqlite3.connect(bak[0])
    assert old.execute("SELECT activity FROM schedule").fetchone()[0] == "上班"
    old.close()
