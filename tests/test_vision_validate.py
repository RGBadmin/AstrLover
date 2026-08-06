from astrlover.vision.validate import cut_at_end_mark, junk_reason


def test_end_mark_cuts_tail_and_reports_complete():
    text, ok = cut_at_end_mark("正文描述······草稿残留", "······")
    assert text == "正文描述" and ok is True


def test_end_mark_missing_means_truncated():
    text, ok = cut_at_end_mark("正文写到一半", "······")
    assert ok is False and text == "正文写到一半"


def test_end_mark_accepts_lookalike_dots():
    _, ok = cut_at_end_mark("正文••••••", "······")
    assert ok is True


def test_no_mark_configured_always_complete():
    text, ok = cut_at_end_mark("随便什么", "")
    assert ok is True and text == "随便什么"


def test_refusal_detected():
    assert "拒答" in junk_reason("很抱歉，我无法满足这个请求。")
    assert "拒答" in junk_reason("I'm sorry, I cannot assist with that.")


def test_long_text_with_refusal_word_not_flagged():
    long_desc = "画面中她穿着黑色丝袜" * 40 + "我无法确定背景是哪里"
    assert junk_reason(long_desc) == ""


def test_thinking_leak_detected():
    assert "思维链" in junk_reason("**Defining the Structure** I'm now analyzing the image...")


def test_min_chars_floor():
    assert "不到下限" in junk_reason("短描述", min_chars=100, max_chars=600)
    assert junk_reason("短描述", min_chars=0) == ""


def test_empty_is_handled_elsewhere():
    assert junk_reason("") == ""
