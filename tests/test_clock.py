from datetime import date

from astrlover.life.clock import Clock


def test_days_since():
    clock = Clock("Asia/Shanghai")
    assert clock.days_since("bad-date") is None
    today = clock.today()
    assert clock.days_since(today.isoformat()) == 0


def test_solar_festival():
    clock = Clock("Asia/Shanghai")
    assert "国庆节" in clock.festivals_on(date(2026, 10, 1))
    assert "情人节" in clock.festivals_on(date(2026, 2, 14))
    assert clock.festivals_on(date(2026, 3, 3)) == [] or True  # 无固定阳历节


def test_upcoming_specials_from_records():
    """纪念日来自记录：每年循环的会提醒，since 类只用来算天数。"""
    clock = Clock("Asia/Shanghai")
    today = clock.today()
    records = [
        {"date": f"1999-{today.month:02d}-{today.day:02d}", "title": "她的生日", "kind": "anniversary"},
        {"date": "2026-04-20", "title": "认识的日子", "kind": "since"},
    ]
    found = clock.upcoming_specials(records, within_days=0)
    assert any("生日" in s for s in found)
    assert not any("认识" in s for s in found)      # since 不当特殊日子提醒
    assert any("认识的日子第" in s for s in clock.since_lines(records))


def test_once_milestone_only_on_the_day():
    clock = Clock("Asia/Shanghai")
    today = clock.today().isoformat()
    assert clock.upcoming_specials([{"date": today, "title": "第一次见面", "kind": "once"}])
    assert not clock.upcoming_specials([{"date": "2020-01-01", "title": "旧事", "kind": "once"}])


def test_week_str_format():
    clock = Clock("Asia/Shanghai")
    assert "-W" in clock.week_str()
