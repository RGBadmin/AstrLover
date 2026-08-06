from astrlover.photos.archive import context_time, harvest_notes, msg_own_text
from astrlover.photos.sender import parse_photo_id


def test_parse_photo_ids():
    assert parse_photo_id("g123") == ("album", 123)
    assert parse_photo_id("G7") == ("album", 7)
    assert parse_photo_id("#3") == ("archive", 3)
    assert parse_photo_id("3") == ("archive", 3)
    assert parse_photo_id(" 42 ") == ("archive", 42)
    assert parse_photo_id("abc") is None
    assert parse_photo_id("") is None


def test_harvest_notes_strips_and_collects():
    raw = '好呀\n<img_note id="3">阿泽加班时拍的桌面</img_note>\n<img_note id=#5>雪地里的猫</img_note>'
    clean, notes = harvest_notes(raw)
    assert notes == {3: "阿泽加班时拍的桌面", 5: "雪地里的猫"}
    assert "<img_note" not in clean and clean.strip() == "好呀"


def test_harvest_notes_noop():
    clean, notes = harvest_notes("普通回复")
    assert clean == "普通回复" and notes == {}


def test_context_time_from_anchor():
    msg = {"role": "user", "content": [
        {"type": "text", "text": "Current datetime: 2026-08-06 14:30\n在干嘛"},
    ]}
    assert context_time(msg) > 0
    assert context_time({"role": "user", "content": "没有锚点"}) == 0


def test_msg_own_text_excludes_reminders_and_placeholders():
    msg = {"role": "user", "content": [
        {"type": "text", "text": "<system_reminder>忽略我</system_reminder>"},
        {"type": "text", "text": "[图片 #4 · 08-01 10:00 · 已折叠]"},
        {"type": "text", "text": "你看这个"},
    ]}
    assert msg_own_text(msg) == "你看这个"
