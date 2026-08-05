from astrlover.chat.composer import parse_reply


def test_plain_text_single_segment():
    r = parse_reply("今天有点想你")
    assert len(r.segments) == 1
    assert r.segments[0].type == "text"
    assert r.segments[0].text == "今天有点想你"


def test_seg_split():
    r = parse_reply("第一条<seg/>第二条<seg/>第三条")
    assert [s.text for s in r.segments] == ["第一条", "第二条", "第三条"]


def test_markers():
    raw = "哈哈哈笑死<seg/><sticker>笑到打鸣</sticker><seg/><voice>晚安哦</voice><seg/><photo>咖啡店窗边自拍，午后阳光</photo>"
    r = parse_reply(raw)
    types = [s.type for s in r.segments]
    assert types == ["text", "sticker", "voice", "photo"]
    assert r.segments[1].text == "笑到打鸣"
    assert r.segments[3].text.startswith("咖啡店窗边自拍")


def test_improv_and_told():
    raw = "我妈是老师嘛<seg/>嘿嘿<improv>妈妈的职业是老师</improv><told>12</told><found>7</found>"
    r = parse_reply(raw)
    assert r.improvs == ["妈妈的职业是老师"]
    assert r.told_events == [12]
    assert r.found_events == [7]
    joined = " ".join(s.text for s in r.segments)
    assert "improv" not in joined and "told" not in joined


def test_stray_tags_cleaned():
    r = parse_reply("你好</voice><sticker>呀")
    assert all("<" not in s.text and ">" not in s.text for s in r.segments)


def test_empty_and_whitespace():
    assert parse_reply("").segments == []
    assert parse_reply("   \n  ").segments == []


def test_max_segments_cap():
    raw = "<seg/>".join(f"第{i}条" for i in range(10))
    r = parse_reply(raw, max_segments=4)
    assert len(r.segments) == 4


def test_marker_inline_with_text():
    r = parse_reply("给你看<photo>雪地里抱着猫</photo>好不好看！")
    assert [s.type for s in r.segments] == ["text", "photo", "text"]
