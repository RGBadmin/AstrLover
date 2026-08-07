"""导演控制台：命令解析与执行（传输层在 bot.py，指令在这里）。

你想让她说点什么，但不希望这条指令出现在你俩的聊天里——控制台走的是
另一条会话，既不进她的 LLM 上下文，也不出现在你俩的 Telegram 窗口里。
她的对话历史里只会多出她自己发的那条。
"""

import re
import time
from datetime import datetime, timedelta

from astrbot.api import logger

MENU = [
    ("umo", "列出所有会话，挑一个绑"),
    ("link", "绑定目标会话 · /link UMO"),
    ("say", "让她原样说一句 · /say 内容"),
    ("act", "给个方向，她自己组织语言 · /act 方向"),
    ("photo", "发张照片 · /photo 方向，或 /photo g123 [附言]"),
    ("moment", "让她发条动态 · /moment [内容]"),
    ("avatar", "给她换个头像 · /avatar [分类]"),
    ("signature", "改她的签名 · /signature [内容]"),
    ("noreply", "让她先别回话 · /noreply [分钟]"),
    ("reply", "解除静默，她重新开口"),
    ("proactive", "主动消息状态 · /proactive now 立即发"),
    ("status", "她的生命状态：此刻/日程/心情/记忆"),
    ("rec", "记录：看/加/改/删 · /rec f · /rec add m 03-21 生日"),
    ("diary", "偷看日记 · /diary [日期]"),
    ("events", "她最近做的事"),
    ("plan", "定时编排 · /plan 20:00 /act 提醒他吃药"),
    ("plans", "看排期 · /plans cancel <id>"),
    ("gallery", "相册 · scan / index auto / embed / search 词"),
    ("vision", "视觉 API 诊断 · /vision test"),
    ("presence", "插件状态：动态、冷却、图片存档"),
    ("help", "所有指令一览"),
]

_PLAN_INTENT = (
    "把管理员的话解析成定时任务 JSON："
    '{"when": "+30m 或 HH:MM 或 YYYY-MM-DD HH:MM", "cmd": "/say 内容 或 /act 方向 '
    '或 /moment 主题 或 /photo 方向 或 /avatar 或 /signature"}。'
    "「提醒他/跟他说」类用 /act；要求原话转达用 /say；发动态用 /moment。"
    "解析不出时间就把 when 设为空字符串。只输出 JSON。"
)

_IMPROVISE = {
    "moment": "你现在想发一条动态到自己的频道。写出正文即可，像发朋友圈那样，短一点、有你的味道。只输出正文。",
    "signature": "你想换一句资料页签名（120 字以内）。写一句和你最近心情呼应的。只输出签名本身。",
    "photo": "你想发一张自己的照片给他。用一句话说出你想发什么样的画面（场景、你的状态、穿着）。只输出这句描述。",
}


class DirectorConsole:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    async def handle(self, text: str, chat_id: int | None = None) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if not text.startswith("/"):
            return await self._natural(text, chat_id)
        name, _, rest = text[1:].partition(" ")
        name = name.split("@", 1)[0].strip().lower()
        rest = rest.strip()
        fn = getattr(self, f"cmd_{name}", None)
        if fn is None:
            return f"没有 /{name} 这个指令。\n可用：" + "、".join(f"/{k}" for k, _ in MENU)
        try:
            return await fn(rest, chat_id)
        except Exception as e:
            logger.error(f"[AstrLover] 控制台执行 /{name} 出错：", exc_info=True)
            return f"执行 /{name} 出错：{e}"

    # ============================================================ 会话绑定
    async def cmd_umo(self, arg: str = "", chat_id=None) -> str:
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
            try:
                updated = float(getattr(conv, "updated_at", 0) or 0)
            except (TypeError, ValueError):
                updated = 0.0
            latest[umo] = max(latest.get(umo, 0.0), updated)
        if not latest:
            return "（AstrBot 里还没有任何对话记录）"
        current = app.state_target
        lines = ["会话列表（复制一整行执行即可绑定）：", ""]
        for umo, _ in sorted(latest.items(), key=lambda kv: -kv[1])[:30]:
            lines.append(f"/link {umo}" + ("   ← 当前绑定" if umo == current else ""))
        return "\n".join(lines)

    async def cmd_link(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        target = arg.strip()
        if not target or target == "show":
            cur = app.state_target
            if not cur:
                return "还没绑定。`/umo` 看有哪些会话。"
            return await self._link_report(cur)
        if len(target.split(":")) < 3:
            return "UMO 格式不对，应为 平台ID:消息类型:会话ID。`/umo` 看可选项。"
        await app.set_target(target)
        return await self._link_report(target)

    async def _link_report(self, target: str) -> str:
        """绑定时当场探查：有没有对话、历史多少条、人格读到没有。"""
        app = self.app
        lines = [f"目标会话：{target}"]
        try:
            cm = app.context.conversation_manager
            cid = await cm.get_curr_conversation_id(target)
            conv = await cm.get_conversation(target, cid) if cid else None
            if conv is None:
                lines.append("⚠️ 查不到对话——UMO 可能拼错，或那个会话还没聊过")
            else:
                import json

                try:
                    n = len(json.loads(conv.history or "[]"))
                except json.JSONDecodeError:
                    n = 0
                lines.append(f"历史 {n} 条")
                persona = await app.bridge.persona_of(target, conv)
                lines.append(f"人格 {len(persona)} 字" if persona else "⚠️ 没读到人格")
        except Exception as e:
            lines.append(f"探查失败：{e}")
        return "\n".join(lines)

    # ============================================================ 让她说话
    async def cmd_say(self, arg: str = "", chat_id=None) -> str:
        if not arg:
            return "用法：/say 内容（她原样说出这句）"
        return await self.app.bridge.deliver(arg)

    async def cmd_act(self, arg: str = "", chat_id=None) -> str:
        if not arg:
            return "用法：/act 方向（她带着人格和最近对话自己组织语言）"
        if re.search(r"(拍|照片|图|自拍)", arg):
            hint = "\n（提示：/act 只会让她说话、不发图。要图用 /photo <方向>）"
        else:
            hint = ""
        try:
            text = await self.app.bridge.generate(arg)
        except Exception as e:
            return f"没生成出来：{e}"
        return await self.app.bridge.deliver(text) + hint

    # ============================================================ 照片
    async def cmd_photo(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        target = app.state_target
        if not target:
            return "先 `/link` 绑定一个会话——照片要以她的身份发出去。"
        arg = arg.strip()
        photo_id, caption = "", ""
        if arg:
            head, _, rest = arg.partition(" ")
            from ..photos.sender import parse_photo_id

            if parse_photo_id(head):
                photo_id, caption = head, rest.strip()
        if not photo_id:
            direction = arg or await self._improvise("photo")
            if not direction:
                return "她没想出要发什么。直接给编号也行：/photo g123 [附言]"
            rows, _ = await app.album.search(keywords=direction, want=direction, top_k=1)
            if not rows:
                return f"她想找「{direction[:30]}」，但相册里没有对得上的。"
            photo_id = f"g{rows[0]['id']}"
        return await app.send_photo_as_her(photo_id, caption)

    # ============================================================ 动态/资料
    async def cmd_moment(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        client = app.bridge.platform_client(app.state_target)
        text = arg or await self._improvise("moment")
        if not text:
            return "她没想出要发什么。直接给内容也行：/moment 正文"
        head = "" if arg else f"她想发：\n{text}\n\n"
        return head + await app.moments.post(client, text, enforce_limits=False)

    async def cmd_avatar(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        client = app.bridge.platform_client(app.state_target)
        return await app.face.change_avatar(client, arg.strip(), enforce_limits=False)

    async def cmd_signature(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        client = app.bridge.platform_client(app.state_target)
        text = arg or await self._improvise("signature")
        if not text:
            return "她没想出要写什么。直接给内容也行：/signature 一句话"
        return await app.face.update_signature(client, text, enforce_limits=False)

    async def _improvise(self, key: str) -> str:
        try:
            return await self.app.bridge.generate(_IMPROVISE[key], instruct="")
        except Exception as e:
            logger.warning(f"[AstrLover] 自主构思失败（{key}）：{e}")
            return ""

    # ============================================================ 静默
    async def cmd_noreply(self, arg: str = "", chat_id=None) -> str:
        minutes = int(arg) if arg.strip().isdigit() else 0
        until = int(time.time()) + minutes * 60 if minutes else -1
        await self.app.dao.kv_set("silent_until", until)
        if minutes:
            return f"好，她先不回话了（{minutes} 分钟后自动恢复）。你说的仍然进她的记忆。"
        return "好，她先不回话了（发 /reply 恢复）。你说的仍然进她的记忆。"

    async def cmd_reply(self, arg: str = "", chat_id=None) -> str:
        await self.app.dao.kv_set("silent_until", 0)
        return "她可以开口了。刚才那段时间你说的话她都知道。"

    # ============================================================ 主动消息
    async def cmd_proactive(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        if arg.strip().lower() in ("now", "立刻", "试试"):
            return await app.proactive.fire(force=True)
        return await app.proactive.status()

    # ============================================================ 生命层
    async def cmd_status(self, arg: str = "", chat_id=None) -> str:
        return await self.app.status_report()

    async def cmd_diary(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        if not app.ready:
            return "生命模拟层未启用。"
        if arg:
            row = await app.dao.get_diary(arg, "weekly" if "W" in arg.upper() else "daily")
        else:
            rows = await app.dao.recent_diaries(1, "daily")
            row = rows[0] if rows else None
        if not row:
            return "（没有这篇日记）"
        return f"📖 {row['date']}（{row['mood'] or '—'}）\n{row['content']}"

    async def cmd_events(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        if not app.ready:
            return "生命模拟层未启用。"
        rows = await app.dao.recent_events(12)
        if not rows:
            return "（还没有事件）"
        mention = {"unmentioned": "未提及", "told": "已讲过", "discovered": "被发现"}
        return "🧾 最近事件：\n" + "\n".join(
            f"#{r['id']} [{r['kind']}|{mention.get(r['mention_status'], r['mention_status'])}] "
            f"{r['description']}" + (f"｜动机：{r['motivation']}" if r["motivation"] else "")
            for r in rows
        )

    # ============================================================ 排期
    async def cmd_plan(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        if not arg:
            return ("用法：/plan <时间> <指令或想让她做的事>\n"
                    "时间：+30m / +2h / 20:00 / 2026-08-07 09:00，也可用自然语言\n"
                    "例：/plan 20:00 /act 提醒他吃药")
        head, _, rest = arg.partition(" ")
        due_ts, cmd = None, ""
        if rest and re.fullmatch(r"\+\d+(?:\.\d+)?[mh]|\d{1,2}:\d{2}", head):
            due_ts = self._parse_when(head)
            cmd = rest.strip() if rest.strip().startswith("/") else f"/act {rest.strip()}"
        else:
            intent = await app.llm.light_json(arg, system_prompt=_PLAN_INTENT)
            if isinstance(intent, dict) and intent.get("cmd"):
                due_ts = self._parse_when(str(intent.get("when") or ""))
                cmd = str(intent["cmd"]).strip()
        if not cmd:
            return "没解析出要做的事，试试「/plan 20:00 /act 提醒他……」。"
        if due_ts is None:
            return "没解析出时间。支持 +30m / +2h / 20:00 / 2026-08-07 09:00。"
        aid = await app.dao.add_action(
            "console_cmd", {"cmd": cmd, "chat_id": chat_id}, due_ts=due_ts, source="director"
        )
        return (f"⏰ 已排期 #{aid}：{datetime.fromtimestamp(due_ts).strftime('%m-%d %H:%M')} "
                f"执行 {cmd}\n/plans 查看，/plans cancel {aid} 取消。")

    async def cmd_plans(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        parts = arg.split()
        if len(parts) == 2 and parts[0].lower() == "cancel" and parts[1].isdigit():
            await app.dao.finish_action(int(parts[1]), "cancelled")
            return f"已取消 #{parts[1]}"
        rows = await app.dao.pending_list(20)
        if not rows:
            return "（没有排期）"
        return "⏰ 排期：\n" + "\n".join(
            f"#{r['id']} {datetime.fromtimestamp(r['due_ts']).strftime('%m-%d %H:%M')} "
            f"{r['payload'].get('cmd', r['kind'])}" for r in rows
        )

    def _parse_when(self, s: str) -> int | None:
        s = (s or "").strip()
        if not s:
            return None
        now = self.app.clock.now() if self.app.clock else datetime.now()
        try:
            if s.startswith("+"):
                num = float(re.sub(r"[^\d.]", "", s))
                delta = timedelta(minutes=num) if s[-1].lower() == "m" else timedelta(hours=num)
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

    # ============================================================ 相册与诊断
    async def cmd_gallery(self, arg: str = "", chat_id=None) -> str:
        return await self.app.gallery_command(arg, self._progress(chat_id))

    async def cmd_vision(self, arg: str = "", chat_id=None) -> str:
        return await self.app.vision_command(arg)

    async def cmd_presence(self, arg: str = "", chat_id=None) -> str:
        app = self.app
        lines = [f"绑定会话：{app.state_target or '（未绑定）'}"]
        lines.append(f"动态：{await app.moments.count()} 条")
        lines += await app.limits.summary()
        st = await app.photos.stats()
        lines.append(f"图片存档：{st['total']} 张（目录层 {st['catalog']} · 细节层 {st['detail']}）")
        silent = await app.dao.kv_get("silent_until", 0) or 0
        if silent == -1:
            lines.append("⏸ 静默中（/reply 解除）")
        elif silent > time.time():
            lines.append(f"⏸ 静默中，{int((silent - time.time()) // 60) + 1} 分钟后恢复")
        return "\n".join(lines)

    async def cmd_rec(self, arg: str = "", chat_id=None) -> str:
        """记录：看/加/改/删。用法：
        /rec                    总览
        /rec f | d | e | s | m | p | o     看某类（事实/日记/事件/日程/纪念日/排期/情绪）
        /rec add f 他不吃香菜
        /rec add m 2026-04-20 认识的日子 since
        /rec add s 14:00-16:00 和小雅逛街
        /rec edit f12 新内容
        /rec del e5
        /rec set stage 稳定      （可设：stage/appearance/signature/avatar）"""
        app = self.app
        if not app.records:
            return "还没初始化好。"
        parts = (arg or "").split(None, 1)
        if not parts:
            return await app.records.overview()
        head = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if head == "add":
            bits = rest.split(None, 1)
            if len(bits) < 2:
                return "用法：/rec add <类型> <内容>，如 /rec add f 他不吃香菜"
            return await app.records.add(bits[0], bits[1])
        if head in ("edit", "改"):
            bits = rest.split(None, 1)
            if len(bits) < 2:
                return "用法：/rec edit f12 新内容"
            return await app.records.edit(bits[0], bits[1])
        if head in ("del", "delete", "rm", "删"):
            if not rest.strip():
                return "用法：/rec del f12"
            return await app.records.delete(rest.strip())
        if head == "set":
            bits = rest.split(None, 1)
            if len(bits) < 2:
                return "用法：/rec set stage 稳定"
            return await app.records.set_state_cmd(bits[0], bits[1])
        return await app.records.listing(head, 30)

    async def cmd_help(self, arg: str = "", chat_id=None) -> str:
        return "指令一览：\n" + "\n".join(f"/{k} — {d}" for k, d in MENU)

    def _progress(self, chat_id):
        async def cb(text: str):
            if chat_id is not None and self.app.director_bot:
                await self.app.director_bot.say(chat_id, text)
        return cb

    # ============================================================ 自然语言
    async def _natural(self, text: str, chat_id) -> str:
        """不带斜杠时按定时编排意图解析；解析不出就提示。"""
        result = await self.cmd_plan(text, chat_id)
        if result.startswith("⏰"):
            return result
        return "没看懂。/help 看指令；想让她说话用 /act 方向。"
