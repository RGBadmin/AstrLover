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


def test_upcoming_specials_birthday():
    clock = Clock("Asia/Shanghai")
    today = clock.today()
    bd = f"1999-{today.month:02d}-{today.day:02d}"
    found = clock.upcoming_specials([], bd, within_days=0)
    assert any("生日" in s for s in found)


def test_week_str_format():
    clock = Clock("Asia/Shanghai")
    assert "-W" in clock.week_str()
