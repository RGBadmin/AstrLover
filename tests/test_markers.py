from astrlover.markers import extract_internal


def test_extract_and_strip():
    raw = '嘿嘿我妈是老师嘛\n<improv>妈妈的职业是老师</improv>\n<told>5</told><found>9</found>'
    clean, improvs, told, found = extract_internal(raw)
    assert improvs == ["妈妈的职业是老师"]
    assert told == [5] and found == [9]
    assert "<improv>" not in clean and "<told>" not in clean
    assert clean.startswith("嘿嘿我妈是老师嘛")


def test_multiple_improvs():
    clean, improvs, _, _ = extract_internal(
        "<improv>我妈是老师</improv><improv>我养过猫</improv>好啦"
    )
    assert improvs == ["我妈是老师", "我养过猫"]
    assert clean.strip() == "好啦"


def test_untouched_when_no_markers():
    raw = "普通回复，一个标记都没有。"
    clean, improvs, told, found = extract_internal(raw)
    assert clean == raw and not improvs and not told and not found


def test_malformed_ignored():
    clean, improvs, told, found = extract_internal("<improv></improv><told>abc</told>")
    assert not improvs and not told and not found
    assert "<told>abc</told>" in clean          # 认不出的标记原样留着，不吞内容
