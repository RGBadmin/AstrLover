from astrlover.config import Cfg


def test_defaults_on_empty():
    cfg = Cfg({})
    assert cfg.enabled is True
    assert cfg.timezone == "Asia/Shanghai"
    assert cfg.heartbeat_minutes == 5
    assert cfg.partner_id == ""
    assert cfg.imagegen_order == []


def test_partner_fallback_console_admins():
    assert Cfg({"life_partner_id": "111", "console_admins": ["222"]}).partner_id == "111"
    assert Cfg({"console_admins": ["222", "333"]}).partner_id == "222"
    assert Cfg({"console_admins": "444"}).partner_id == "444"


def test_flat_reads():
    cfg = Cfg({
        "life_timezone": " Asia/Tokyo ",
        "life_heartbeat_minutes": 10,
        "ig_backend_order": ["nanobanana", ""],
        "ig_nb_api_key": "k",
    })
    assert cfg.timezone == "Asia/Tokyo"
    assert cfg.heartbeat_minutes == 10
    assert cfg.imagegen_order == ["nanobanana"]
    assert cfg.imagegen_backend("nanobanana")["api_key"] == "k"


def test_heartbeat_floor():
    assert Cfg({"life_heartbeat_minutes": 0}).heartbeat_minutes == 1
    assert Cfg({"life_heartbeat_minutes": "bad"}).heartbeat_minutes == 5


def test_disabled():
    assert Cfg({"life_enabled": False}).enabled is False
