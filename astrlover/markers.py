"""内部标记：她在回复里写给系统看的东西，发出去之前摘掉。

  <improv>…</improv>   临场编的关于她自己的新设定 → 固化成事实记录
  <told>12</told>      她主动讲起了 12 号事件
  <found>7</found>     7 号事件被他自己发现了

（图片的 <img_note> 由 photos/archive.py 处理，那套跟图片编号绑在一起。）
"""

import re

_IMPROV = re.compile(r"<improv>(.*?)</improv>", re.I | re.S)
_TOLD = re.compile(r"<(told|found)>\s*(\d+)\s*</\1>", re.I)

# 导演桥给她自己的消息打的 [MM-DD HH:MM] 戳。那是给插件读时间用的记账，
# 可模型在历史里看到的是同一份文本，下一轮就照着开头写一个——于是戳漏进了
# 真正发出去的消息，写回历史时还会再叠一层。所以进出两处都剥掉：
# 时间归时间，正文归正文。
#
# 按行剥（她可能分几行说，每行都戳一个），连着几个也一起剥；
# 位数放宽到 1~2 位，她未必照着 %m-%d 补零。
_STAMP_HEAD = re.compile(
    r"^[ \t]*(?:\[\d{1,2}-\d{1,2}[ \t]+\d{1,2}:\d{2}\][ \t]*)+", re.M
)


def strip_stamp(text: str) -> str:
    """剥掉行首的时间戳。"""
    return _STAMP_HEAD.sub("", text or "")


def extract_internal(text: str) -> tuple[str, list[str], list[int], list[int]]:
    """返回 (清理后的文本, 编造固化, told 事件号, found 事件号)。

    只摘标记，其余原样保留——回复的形态归 AstrBot 管，插件不改写正文。
    """
    improvs = [m.group(1).strip() for m in _IMPROV.finditer(text or "") if m.group(1).strip()]
    told: list[int] = []
    found: list[int] = []
    for m in _TOLD.finditer(text or ""):
        try:
            eid = int(m.group(2))
        except ValueError:
            continue
        (told if m.group(1).lower() == "told" else found).append(eid)
    clean = _TOLD.sub("", _IMPROV.sub("", text or ""))
    return clean.rstrip(), improvs, told, found
