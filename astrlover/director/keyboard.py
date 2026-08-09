"""可点击的回执：按钮点一下 = 替你把那条指令发出去。

不新造一套执行逻辑——按钮携带的就是一行控制台指令，点击后原样交给
console.handle()，跟 /plan 排期到点重放是同一条路。所以任何指令都能
做成按钮，不用为它单独写处理函数。

Telegram 的 callback_data 有 **64 字节**上限（UTF-8 计），UMO 长一点就
装不下。超了就存进令牌表，按钮里只带 `c:3f2a` 这种短键。表是内存的、
有上限的——重启后旧按钮点了会提示重发指令，比把长文塞进按钮可靠。
"""

import hashlib

CB_LIMIT = 64          # Telegram 的硬上限
_TOKEN_CAP = 400       # 令牌表上限，超了丢最老的


class Reply(str):
    """带按钮的回执。

    是 str 的子类——所有把回执当字符串用的地方（拼接、startswith、
    `if reply:`）全都照旧，不用改一行。
    """

    __slots__ = ("buttons",)

    def __new__(cls, text: str, buttons=None):
        obj = super().__new__(cls, text)
        # [[(标签, 指令), …], …]，外层是行，内层是同一行里的几个按钮
        obj.buttons = list(buttons or [])
        return obj


class Callbacks:
    """长指令 ↔ 短令牌。"""

    def __init__(self):
        self._map: dict[str, str] = {}

    def encode(self, cmd: str) -> str:
        if len(cmd.encode("utf-8")) <= CB_LIMIT:
            return cmd
        key = "c:" + hashlib.sha1(cmd.encode("utf-8")).hexdigest()[:12]
        if key not in self._map and len(self._map) >= _TOKEN_CAP:
            self._map.pop(next(iter(self._map)))     # dict 保序，第一个就是最老的
        self._map[key] = cmd
        return key

    def decode(self, data: str) -> str:
        return self._map.get(data, "") if data.startswith("c:") else data


def markup(buttons, callbacks: Callbacks):
    """把 [[(标签, 指令)…]…] 转成 PTB 的 InlineKeyboardMarkup。"""
    if not buttons:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for row in buttons:
        cells = [
            InlineKeyboardButton(str(label)[:64], callback_data=callbacks.encode(str(cmd)))
            for label, cmd in row if str(label).strip() and str(cmd).strip()
        ]
        if cells:
            rows.append(cells)
    return InlineKeyboardMarkup(rows) if rows else None


def grid(items, per_row: int = 3):
    """一维的 (标签, 指令) 列表按每行几个排成网格。"""
    items = [x for x in items if x]
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]
