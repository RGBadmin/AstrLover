from astrlover.config import Cfg


def test_defaults_on_empty():
    cfg = Cfg({})
    assert cfg.timezone == "Asia/Shanghai"
    assert cfg.min_gap_minutes == 45
    assert cfg.max_silence_hours == 30
    assert cfg.max_unanswered == 3
    assert cfg.heartbeat_minutes == 5
    assert cfg.imagegen_order == []


def test_missing_required():
    missing = Cfg({}).missing_required()
    assert len(missing) == 2
    cfg = Cfg({"wiring": {"main_platform_id": "tg_main", "owner_id": "123"}})
    assert cfg.missing_required() == []


def test_nested_read():
    cfg = Cfg({
        "models": {"chat_provider_id": " gpt "},
        "imagegen": {"backend_order": ["nanobanana", ""], "nanobanana": {"api_key": "k"}},
        "proactive": {"min_gap_minutes": 10},
    })
    assert cfg.chat_provider_id == "gpt"
    assert cfg.imagegen_order == ["nanobanana"]
    assert cfg.imagegen_backend("nanobanana") == {"api_key": "k"}
    assert cfg.min_gap_minutes == 10


def test_heartbeat_floor():
    assert Cfg({"system": {"heartbeat_minutes": 0}}).heartbeat_minutes == 1
