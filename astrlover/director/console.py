"""导演控制台（R7）：命令与自然语言编排的解析执行核心。

纯"文本进 → 文本出"，与传输层（director/bot.py 的独立 Telegram bot）解耦。
- 斜杠命令：确定性操作（状态/日记/对话绑定/图库/待办/配置）；
- 自然语言：轻模型解析为编排意图（说/做/定时），与她的自主行为
  共用 ActionExecutor——效果是"她恰好做了你想让她做的事"。
"""

import re
import time
from datetime import datetime, timedelta

from astrbot.api import logger

from .status import build_status

_HELP = """🎬 AstrLover 导演控制台

【对话绑定】
/umo — 列出 AstrBot 里的全部对话 UMO
/link <UMO> — 绑定到该对话（她将在这个对话里生活）
  例：/link astrbotbot:FriendMessage:9876543210
/unlink — 解除绑定

【查看】
/status — 运行状态
/diary [YYYY-MM-DD] — 偷看日记（默认最近一篇）
/events — 最近的生活事件流
/pending — 待办队列   /cancel <id> — 取消待办

【让她做事】
/say <内容> — 让她以自己的口吻对他说这件事
/voice <内容> — 让她发条语音
/post <主题> — 让她发条频道动态
/avatar [提示] — 让她换头像
/sign [提示] — 让她改签名

【维护】
/scan — 扫描图库新文件   /tagall — 立即全量打标
/config — 查看可调参数   /config set <路径> <值>

【自然语言编排】直接说话即可，例如：
「今晚8点提醒他吃药，要像她自己惦记着一样」
「明早让她发条关于早餐的动态」"""

_INTENT_SYSTEM = (
    "你是编排指令解析器。把管理员的话解析成 JSON："
    '{"action": "say|voice|post|avatar|signature|status|diary|events|pending|help|none", '
    '"instruction": "要她转达/说的内容（say/voice 用，保留完整语义）", '
    '"topic": "动态主题（post 用）", "hint": "提示（avatar/signature 用）", '
    '"when": null 或 "+30m"/"+2h" 或 "HH:MM" 或 "YYYY-MM-DD HH:MM"}。\n'
    "「提醒他/跟他说/告诉他」类=say；「发语音」=voice；「发动态/发朋友圈」=post；"
    "带时间的一律填 when。看不懂就 action=none。只输出 JSON。"
)

_CMD_RE = re.compile(r"^/(\w+)\s*(.*)$", re.S)
_UMO_RE = re.compile(r"^[^:\s]+:[^:\s]+:\S+$")

# /config set 允许的白名单：防打扰三参数与系统参数
_CONFIG_PATHS = {
    "proactive.min_gap_minutes": int,
    "proactive.max_silence_hours": int,
    "proactive.max_unanswered": int,
    "system.heartbeat_minutes": int,
    "system.debug": lambda v: str(v).lower() in ("1", "true", "on", "开"),
}


class DirectorConsole:
    def __init__(self, app):
        self.app = app

    async def handle(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        try:
            m = _CMD_RE.match(text)
            if m:
                return await self._command(m.group(1).lower(), m.group(2).strip())
            return await self._natural(text)
        except Exception as e:
            logger.error("[AstrLover] 导演指令异常：", exc_info=True)
            return f"执行出错：{e}"

    # ------------------------------------------------------------------
    # 斜杠命令
    # ------------------------------------------------------------------
    async def _command(self, cmd: str, arg: str) -> str:
        app = self.app
        if cmd in ("help", "start"):
            return _HELP
        if cmd == "umo":
            return await self._list_umos()
        if cmd == "link":
            return await self._link(arg)
        if cmd == "unlink":
            await app.set_linked_umo("")
            return "已解除绑定。她暂时不在任何对话里，用 /link 重新绑定。"
        if cmd == "status":
            return await build_status(app)
        if cmd == "diary":
            return await self._show_diary(arg)
        if cmd == "events":
            rows = await app.dao.recent_events(12)
            if not rows:
                return "（还没有事件）"
            return "🧾 最近事件：\n" + "\n".join(
                f"#{r['id']} [{r['kind']}|{r['mention_status']}] {r['description']}"
                + (f"｜动机：{r['motivation']}" if r["motivation"] else "")
                for r in rows
            )
        if cmd == "pending":
            rows = await app.dao.pending_list(15)
            if not rows:
                return "（待办队列为空）"
            return "⏰ 待办：\n" + "\n".join(
                f"#{r['id']} [{r['kind']}] {datetime.fromtimestamp(r['due_ts']).strftime('%m-%d %H:%M')} {str(r['payload'])[:50]}"
                for r in rows
            )
        if cmd == "cancel":
            if not arg.isdigit():
                return "用法：/cancel <id>"
            await app.dao.finish_action(int(arg), "cancelled")
            return f"已取消 #{arg}"
        if cmd == "scan":
            n = await app.gallery.ingest.scan_dir()
            return f"扫描完成：新增 {n} 张待打标。"
        if cmd == "tagall":
            n = await app.gallery.ingest.tag_all()
            await app.refresh_capabilities()
            return f"打标完成：处理 {n} 张。"
        if cmd == "post":
            return await self._run_or_fail("post", {"topic": arg})
        if cmd == "avatar":
            return await self._run_or_fail("avatar", {"hint": arg})
        if cmd == "sign":
            return await self._run_or_fail("signature", {"hint": arg})
        if cmd == "say":
            if not arg:
                return "用法：/say <要她说的事>"
            return await self._run_or_fail("say", {"instruction": arg})
        if cmd == "voice":
            if not arg:
                return "用法：/voice <要她说的话>"
            return await self._run_or_fail("voice", {"instruction": arg})
        if cmd == "config":
            return await self._config(arg)
        return "未知命令，/help 查看用法。"

    # ------------------------------------------------------------------
    # 对话绑定（/umo /link）
    # ------------------------------------------------------------------
    async def _list_umos(self) -> str:
        app = self.app
        try:
            convs = await app.context.conversation_manager.get_conversations()
        except Exception as e:
            return f"读取对话列表失败：{e}"
        latest: dict[str, float] = {}
        for conv in convs:
            umo = str(getattr(conv, "user_id", "") or "")
            if not umo:
                continue
            updated = getattr(conv, "updated_at", 0) or 0
            try:
                updated = float(updated)
            except (TypeError, ValueError):
                updated = 0.0
            latest[umo] = max(latest.get(umo, 0.0), updated)
        if not latest:
            return "（AstrBot 里还没有任何对话记录）"
        current = await app.linked_umo()
        lines = ["📡 AstrBot 全部对话 UMO（按最近活跃排序，/link <UMO> 绑定）：", ""]
        for umo, _ in sorted(latest.items(), key=lambda kv: -kv[1])[:40]:
            mark = " ← 当前绑定" if umo == current else ""
            lines.append(f"{umo}{mark}")
        if len(latest) > 40:
            lines.append(f"…（共 {len(latest)} 个，仅显示前 40）")
        return "\n".join(lines)

    async def _link(self, arg: str) -> str:
        app = self.app
        umo = arg.strip()
        if not umo:
            return "用法：/link <UMO>，先用 /umo 查看可选对话。"
        if not _UMO_RE.match(umo):
            return "UMO 格式不对，应为 平台ID:消息类型:会话ID，例如 astrbotbot:FriendMessage:9876543210"
        await app.set_linked_umo(umo)
        return (
            f"✅ 已绑定到：{umo}\n"
            "她现在在这个对话里生活：聊天、主动消息、提醒都发生在这里。"
            "随时可用 /link 切换、/unlink 解除。"
        )

    # ------------------------------------------------------------------
    async def _run_or_fail(self, kind: str, payload: dict) -> str:
        ok = await self.app.actions.run(kind, payload)
        return "✅ 已完成。" if ok else "❌ 执行失败（详见日志；可能缺少对应能力配置或未绑定对话）。"

    async def _show_diary(self, arg: str) -> str:
        app = self.app
        if arg:
            row = await app.dao.get_diary(arg, "weekly" if "W" in arg else "daily")
        else:
            rows = await app.dao.recent_diaries(1, "daily")
            row = rows[0] if rows else None
        if not row:
            return "（没有这篇日记）"
        return f"📖 {row['date']}（{row['mood'] or '—'}）\n{row['content']}"

    async def _config(self, arg: str) -> str:
        app = self.app
        if not arg:
            lines = ["🔧 可调参数（/config set <路径> <值>）："]
            for path in _CONFIG_PATHS:
                sect, key = path.split(".")
                cur = (app.star.config.get(sect) or {}).get(key)
                lines.append(f"  {path} = {cur}")
            return "\n".join(lines)
        parts = arg.split()
        if len(parts) != 3 or parts[0] != "set":
            return "用法：/config set <路径> <值>"
        _, path, value = parts
        caster = _CONFIG_PATHS.get(path)
        if caster is None:
            return f"不允许修改：{path}（仅限 {', '.join(_CONFIG_PATHS)}）"
        sect, key = path.split(".")
        try:
            casted = caster(value)
        except (ValueError, TypeError):
            return "值的格式不对。"
        app.star.config.setdefault(sect, {})[key] = casted
        app.star.config.save_config()
        app.cfg._raw.setdefault(sect, {})[key] = casted
        return f"✅ {path} = {casted}"

    # ------------------------------------------------------------------
    # 自然语言编排
    # ------------------------------------------------------------------
    async def _natural(self, text: str) -> str:
        app = self.app
        intent = await app.llm.light_json(text, system_prompt=_INTENT_SYSTEM)
        if not isinstance(intent, dict):
            return "没解析出意图，试试 /help 里的写法。"
        action = str(intent.get("action") or "none")
        if action == "none":
            return "没看懂想让她做什么，试试 /help 里的写法。"
        if action in ("status", "diary", "events", "pending", "help"):
            return await self._command(action, str(intent.get("hint") or ""))

        payload_map = {
            "say": {"instruction": str(intent.get("instruction") or text)},
            "voice": {"instruction": str(intent.get("instruction") or text)},
            "post": {"topic": str(intent.get("topic") or intent.get("instruction") or "")},
            "avatar": {"hint": str(intent.get("hint") or "")},
            "signature": {"hint": str(intent.get("hint") or "")},
        }
        payload = payload_map.get(action)
        if payload is None:
            return f"不支持的动作：{action}"

        due_ts = self._parse_when(intent.get("when"))
        if due_ts is None:
            return await self._run_or_fail(action, payload)
        aid = await app.actions.schedule(action, payload, due_ts, source="director")
        due_str = datetime.fromtimestamp(due_ts).strftime("%m-%d %H:%M")
        return f"⏰ 已排程 #{aid}：{due_str} 执行 {action}。到点她会像自己想起来一样去做。"

    def _parse_when(self, when) -> int | None:
        """None=立即；返回未来时间戳或 None。"""
        if not when:
            return None
        s = str(when).strip()
        now = self.app.clock.now()
        try:
            if s.startswith("+"):
                num = float(re.sub(r"[^\d.]", "", s))
                unit = s[-1].lower()
                delta = timedelta(minutes=num) if unit == "m" else timedelta(hours=num)
                return int(time.time() + delta.total_seconds())
            if re.fullmatch(r"\d{1,2}:\d{2}", s):
                h, m = map(int, s.split(":"))
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return int(target.timestamp())
            target = datetime.fromisoformat(s)
            if target.tzinfo is None and now.tzinfo is not None:
                target = target.replace(tzinfo=now.tzinfo)
            return int(target.timestamp())
        except (ValueError, TypeError):
            return None
