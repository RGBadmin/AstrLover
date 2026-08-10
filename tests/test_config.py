from astrlover.config import Cfg


def test_defaults_on_empty():
    cfg = Cfg({})
    assert cfg.enabled is True
    assert cfg.timezone == "Asia/Shanghai"
    assert cfg.heartbeat_minutes == 5
    assert cfg.partner_id == ""
    assert [t for _s, c in cfg.imagegen_slots() for t in [c['type']] if c['api_key']] == []


def test_partner_fallback_console_admins():
    assert Cfg({"life_partner_id": "111", "console_admins": ["222"]}).partner_id == "111"
    assert Cfg({"console_admins": ["222", "333"]}).partner_id == "222"
    assert Cfg({"console_admins": "444"}).partner_id == "444"


def test_flat_reads():
    cfg = Cfg({
        "life_timezone": " Asia/Tokyo ",
        "life_heartbeat_minutes": 10,
        "ig_main_type": "api", "ig_main_key": "k",
        "ig_main_url": "https://x.com/v1/chat/completions",
    })
    assert cfg.timezone == "Asia/Tokyo"
    assert cfg.heartbeat_minutes == 10
    slots = dict(cfg.imagegen_slots())
    assert slots["主"]["type"] == "api" and slots["主"]["api_key"] == "k"


def test_heartbeat_floor():
    assert Cfg({"life_heartbeat_minutes": 0}).heartbeat_minutes == 1
    assert Cfg({"life_heartbeat_minutes": "bad"}).heartbeat_minutes == 5


def test_disabled():
    assert Cfg({"life_enabled": False}).enabled is False
