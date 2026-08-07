"""对话素材：直接读 AstrBot 的对话历史，插件不再自己存一份。

她的对话本来就在 AstrBot 的对话管理里（WebUI 可看可改可清），插件再存一份
就是第二事实源——你在那边清空了对话，这边的副本还在，日记就会写出你已经
删掉的东西。所以写日记和抽事实时现取现用。

时间从哪来：AstrBot 会把 `Current datetime: ...` 注进 user 消息正文（历史里
唯一可靠的时间锚点），她自己的消息则带导演桥打的 `[MM-DD HH:MM]` 戳。
两种都没有时不硬猜——退化成"取最近 N 条"，日记照写，只是素材范围粗一点。
"""

import json
import re
import time
from datetime import datetime

from astrbot.api import logger

_CTX_TIME = re.compile(r"Current datetime:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
_OWN_STAMP = re.compile(r"^\s*\[(\d{2})-(\d{2}) (\d{2}):(\d{2})\]\s*")
_NOISE = re.compile(r"<(system_reminder|describe_images|your_own_moments)>.*?</\1>", re.S)
_FALLBACK_TURNS = 40


async def load(app) -> list[dict]:
    """取绑定会话的完整对话，返回 [{role, text, ts}]（ts 可能为 0）。"""
    umo = app.state_target
    if not umo:
        return []
    try:
        cm = app.context.conversation_manager
        cid = await cm.get_curr_conversation_id(umo)
        conv = await cm.get_conversation(umo, cid) if cid else None
        history = json.loads(conv.history or "[]") if conv else []
    except Exception as e:
        logger.warning(f"[AstrLover] 读对话历史失败：{e}")
        return []
    if not isinstance(history, list):
        return []

    tz = app.clock.tz if app.clock else None
    year = app.clock.now().year if app.clock else datetime.now().year
    out: list[dict] = []
    last_ts = 0
    for msg in history:
        if not isinstance(msg, dict):
            continue
        text = _plain_text(msg.get("content"))
        ts = _timestamp(msg, text, tz, year)
        if ts:
            last_ts = ts
        else:
            ts = last_ts          # 没锚点的沿用上一条的时间，保证单调
        text = _OWN_STAMP.sub("", _CTX_TIME.sub("", text)).strip()
        if not text:
            continue
        out.append({
            "role": "user" if msg.get("role") == "user" else "her",
            "text": text,
            "ts": ts,
        })
    return out


async def since(app, ts0: int) -> list[dict]:
    """某个时刻之后的对话。历史里一个时间锚点都没有时退化为最近 N 条。"""
    rows = await load(app)
    if not rows:
        return []
    if not any(r["ts"] for r in rows):
        return rows[-_FALLBACK_TURNS:]
    return [r for r in rows if r["ts"] >= ts0]


async def on_day(app, date_str: str) -> list[dict]:
    """某一天的对话（写日记用）。没有锚点时退化为最近 N 条。"""
    rows = await load(app)
    if not rows:
        return []
    if not any(r["ts"] for r in rows):
        return rows[-_FALLBACK_TURNS:]
    try:
        start = datetime.fromisoformat(date_str)
    except ValueError:
        return []
    if app.clock and app.clock.tz is not None:
        start = start.replace(tzinfo=app.clock.tz)
    lo = int(start.timestamp())
    return [r for r in rows if lo <= r["ts"] < lo + 86400]


def as_script(rows: list[dict], limit: int = 40, width: int = 120) -> str:
    """给模型看的对话稿。"""
    return "\n".join(
        f"{'他' if r['role'] == 'user' else '我'}：{r['text'][:width]}"
        for r in rows[-limit:]
    )


def last_user_ts(rows: list[dict]) -> int:
    for r in reversed(rows):
        if r["role"] == "user" and r["ts"]:
            return r["ts"]
    return 0


# ----------------------------------------------------------------------
def _plain_text(content) -> str:
    """取正文：跳过图片，剥掉注入块与折叠占位之外的噪声。"""
    if isinstance(content, str):
        return _NOISE.sub("", content).strip()
    if not isinstance(content, list):
        return ""
    bits = []
    for part in content:
        if not (isinstance(part, dict) and part.get("type") == "text"):
            continue
        t = (part.get("text") or "").strip()
        if t and "<system_reminder>" not in t:
            bits.append(t)
    return _NOISE.sub("", " ".join(bits)).strip()


def _timestamp(msg: dict, text: str, tz, year: int) -> int:
    if m := _CTX_TIME.search(text):
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            return int((dt.replace(tzinfo=tz) if tz else dt).timestamp())
        except (ValueError, TypeError):
            pass
    if msg.get("role") != "user":
        if m := _OWN_STAMP.match(text):
            try:
                dt = datetime(year, int(m.group(1)), int(m.group(2)),
                              int(m.group(3)), int(m.group(4)))
                ts = int((dt.replace(tzinfo=tz) if tz else dt).timestamp())
                # 她的戳不带年份，跨年时会落到未来——回退一年
                return ts if ts <= time.time() + 86400 else int(dt.replace(year=year - 1).timestamp())
            except (ValueError, TypeError):
                pass
    return 0
