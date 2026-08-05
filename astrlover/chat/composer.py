"""回复标记协议解析（D2）：把主模型输出解析为有序消息段。

纯函数、无副作用，便于单元测试。解析永不抛异常——
任何不规范输出都降级为纯文本段，绝不吞消息。
"""

import random
import re
from dataclasses import dataclass, field

_SEG_SPLIT = re.compile(r"<seg\s*/?>", re.I)
_IMPROV = re.compile(r"<improv>(.*?)</improv>", re.I | re.S)
_TOLD = re.compile(r"<(told|found)>\s*(\d+)\s*</\1>", re.I)
_MARKER = re.compile(r"<(voice|sticker|photo)>(.*?)</\1>", re.I | re.S)
# 清理模型可能误发明的孤立标签
_STRAY_TAGS = re.compile(r"</?(seg|voice|sticker|photo|improv|told|found)\s*/?>", re.I)


@dataclass
class Segment:
    type: str  # text / voice / sticker / photo
    text: str


@dataclass
class ParsedReply:
    segments: list[Segment] = field(default_factory=list)
    improvs: list[str] = field(default_factory=list)          # A6 临场编造
    told_events: list[int] = field(default_factory=list)       # 她主动讲过的事件 id
    found_events: list[int] = field(default_factory=list)      # 被对方发现的事件 id

    def plain_text(self) -> str:
        return " ".join(s.text for s in self.segments if s.type == "text")


def parse_reply(raw: str, max_segments: int = 6) -> ParsedReply:
    result = ParsedReply()
    if not raw or not raw.strip():
        return result
    text = raw.strip()

    # 1. 内部标记先摘走
    for m in _IMPROV.finditer(text):
        note = m.group(1).strip()
        if note:
            result.improvs.append(note)
    text = _IMPROV.sub("", text)

    for m in _TOLD.finditer(text):
        try:
            eid = int(m.group(2))
        except ValueError:
            continue
        (result.told_events if m.group(1).lower() == "told" else result.found_events).append(eid)
    text = _TOLD.sub("", text)

    # 2. 按 <seg/> 切段，段内再拆 voice/sticker/photo
    for part in _SEG_SPLIT.split(text):
        _parse_part(part, result.segments)

    # 3. 清理与限量
    cleaned: list[Segment] = []
    for seg in result.segments:
        seg.text = _STRAY_TAGS.sub("", seg.text).strip()
        if seg.text:
            cleaned.append(seg)
    result.segments = cleaned[:max_segments]

    # 4. 全军覆没时兜底为纯文本
    if not result.segments:
        fallback = _STRAY_TAGS.sub("", raw).strip()
        if fallback:
            result.segments = [Segment("text", fallback)]
    return result


def _parse_part(part: str, out: list[Segment]):
    pos = 0
    for m in _MARKER.finditer(part):
        before = part[pos:m.start()].strip()
        if before:
            out.append(Segment("text", before))
        inner = m.group(2).strip()
        if inner:
            out.append(Segment(m.group(1).lower(), inner))
        pos = m.end()
    tail = part[pos:].strip()
    if tail:
        out.append(Segment("text", tail))


def typing_delay(text: str) -> float:
    """模拟真人打字节奏：按长度粗估，带抖动。"""
    base = 0.8 + min(len(text), 80) / 16.0
    return min(6.0, base * random.uniform(0.75, 1.25))
