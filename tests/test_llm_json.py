from astrlover.llm import LLM


def test_plain_json():
    assert LLM.extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    raw = '```json\n{"action": "say", "when": null}\n```'
    assert LLM.extract_json(raw) == {"action": "say", "when": None}


def test_embedded_json():
    raw = '好的，解析结果如下：{"new": [], "expire_ids": [3]}，请查收。'
    assert LLM.extract_json(raw) == {"new": [], "expire_ids": [3]}


def test_invalid_returns_none():
    assert LLM.extract_json("完全不是 JSON") is None
