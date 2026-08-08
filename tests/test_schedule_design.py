"""日程分两层：作息（按天）+ 约定（跨天、稀疏、带日期）。

以前是每天早上把一整天铺满，于是「周六下午跟小雅逛街」这种提前几天
定下的事根本存不住——第二天重排就没了。现在约定独立于当天，
她提前几天就知道；没安排的时段就说没安排，不编。
"""

import asyncio
from datetime import timedelta

import pytest


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def life_app(app_factory):
    async def build():
        app = app_factory()
        await app.initialize()
        return app

    return build


def test_rhythm_and_commitments_coexist(life_app):
    """作息覆盖式写入，不能连带把当天的约定冲掉。"""

    async def go():
        app = await life_app()
        today = app.clock.today_str()
        await app.dao.add_schedule_item(today, "14:00", "17:00", "跟小雅逛街", "chat")
        await app.dao.set_rhythm(today, "08:00", "23:00")
        await app.dao.set_rhythm(today, "09:00", "23:30")   # 改一次作息

        rows = await app.dao.day_schedule(today)
        kinds = [r["kind"] for r in rows]
        assert kinds.count("wake") == 1 and kinds.count("sleep") == 1, "作息不能堆积"
        assert any(r["activity"] == "跟小雅逛街" for r in rows), "约定被作息冲掉了"
        assert await app.life.wake_sleep() == ((9, 0), (23, 30))
        await app.terminate()

    run(go())


def test_commitment_survives_across_days(life_app):
    """几天后的约定要能存住，并且提前出现在注入块里。"""

    async def go():
        app = await life_app()
        today = app.clock.today()
        sat = (today + timedelta(days=3)).isoformat()
        await app.life.add_commitment(sat, "14:00", "17:00", "跟小雅逛街")

        upcoming = await app.dao.upcoming_schedule(today.isoformat(), days=7)
        assert [r["activity"] for r in upcoming] == ["跟小雅逛街"]

        text = await app.life.prompt_text()
        assert "接下来定好的事" in text and sat in text and "跟小雅逛街" in text
        await app.terminate()

    run(go())


def test_commitment_is_deduped(life_app):
    """同一段对话被反复读到时，不能记成好几条。"""

    async def go():
        app = await life_app()
        d = (app.clock.today() + timedelta(days=1)).isoformat()
        first = await app.life.add_commitment(d, "14:00", "17:00", "看电影")
        again = await app.life.add_commitment(d, "14:00", "17:00", "看电影")
        assert first and not again
        rows = await app.dao.day_schedule(d)
        assert len([r for r in rows if r["kind"] == "activity"]) == 1
        await app.terminate()

    run(go())


def test_no_arrangement_is_not_invented(life_app):
    """没安排就说没安排——以前这里会编一句"闲着，刷刷手机"。"""

    async def go():
        app = await life_app()
        await app.dao.set_rhythm(app.clock.today_str(), "08:00", "23:30")
        assert await app.life.current_activity() == ""
        text = await app.life.prompt_text()
        assert "没有特别安排" in text
        assert "刷刷手机" not in text
        await app.terminate()

    run(go())


def test_past_commitments_are_rejected(life_app):
    """聊天里提到的已发生的事不是安排，不能写进日程。"""

    async def go():
        app = await life_app()
        yesterday = (app.clock.today() - timedelta(days=1)).isoformat()
        tomorrow = (app.clock.today() + timedelta(days=1)).isoformat()
        n = await app.memory._save_commitments([
            {"date": yesterday, "start": "14:00", "end": "17:00", "what": "昨天已经去过了"},
            {"date": tomorrow, "start": "14:00", "end": "17:00", "what": "明天去看展"},
            {"date": "下周六", "start": "14:00", "end": "17:00", "what": "日期没折算"},
            {"date": tomorrow, "start": "", "end": "", "what": "没有时刻"},
        ])
        assert n == 1
        assert not [r for r in await app.dao.day_schedule(yesterday) if r["kind"] == "activity"]
        rows = await app.dao.day_schedule(tomorrow)
        assert [r["activity"] for r in rows if r["kind"] == "activity"] == ["明天去看展"]
        await app.terminate()

    run(go())


def test_manual_add_accepts_date(life_app):
    """/rec add s 可以指定哪天，不写就是今天。"""

    async def go():
        app = await life_app()
        d = (app.clock.today() + timedelta(days=5)).isoformat()
        msg = await app.records.add("s", f"{d} 14:00-16:00 体检")
        assert "记下了" in msg and d in msg
        assert [r["activity"] for r in await app.dao.day_schedule(d)] == ["体检"]

        await app.records.add("s", "19:00-20:00 跑步")
        today_rows = await app.dao.day_schedule(app.clock.today_str())
        assert "跑步" in [r["activity"] for r in today_rows]

        assert "用法" in await app.records.add("s", "随便写点什么")
        await app.terminate()

    run(go())


def test_advance_only_touches_activities(scheduled_app):
    """心跳推进只动 activity，作息两条不该被标成 done。"""

    async def go():
        app = await scheduled_app()
        await app.life.advance()
        rows = await app.dao.day_schedule(app.clock.today_str())
        for r in rows:
            if r["kind"] in ("wake", "sleep"):
                assert r["status"] == "planned"
        await app.terminate()

    run(go())
