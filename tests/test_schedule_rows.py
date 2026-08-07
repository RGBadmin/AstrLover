"""日程有数据时的三条读取路径。

线上炸的三处（面板 overview、心跳 advance、记录列表）都读 schedule 的
kind 列，而空表永远读不到列——所以这里必须先写进 wake/activity/sleep
三条真数据，再走一遍。
"""

import asyncio

def run(coro):
    return asyncio.run(coro)


def test_wake_sleep_reads_kind(scheduled_app):
    async def go():
        app = await scheduled_app()
        wake, sleep = await app.life.wake_sleep()
        assert (wake, sleep) == ((8, 0), (22, 30))   # 返回的是解析后的 (时, 分)
        assert isinstance(await app.life.sleeping_now(), bool)
        assert isinstance(await app.life.current_activity(), str)
        await app.terminate()

    run(go())


def test_advance_reads_kind(scheduled_app):
    """心跳推进日程：只推 activity，作息两条不算日程项。"""

    async def go():
        app = await scheduled_app()
        await app.life.advance()          # 线上就是在这里 KeyError
        rows = await app.dao.day_schedule(app.clock.today_str())
        assert len(rows) == 3
        assert {r["kind"] for r in rows} == {"wake", "activity", "sleep"}
        await app.terminate()

    run(go())


def test_records_rows_renders_schedule(scheduled_app):
    """面板日程卡片：activity 显示区间，作息两条只显示时刻。"""

    async def go():
        app = await scheduled_app()
        rows = await app.records.rows("s", 60)
        assert len(rows) == 3
        by_body = {r["body"]: r for r in rows}
        assert "~" in by_body["上班"]["chips"][1]
        assert "~" not in by_body["起床"]["chips"][1]
        assert all(r["rid"].startswith("s") for r in rows)
        await app.terminate()

    run(go())
