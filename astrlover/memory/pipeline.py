"""记忆沉淀与情景记忆（A1 二三四层 + A15 适度遗忘）。

- 事实沉淀：对话空闲后批处理（轻模型），可更新可失效；
- 核心小抄：事实有变化时她"自己修订"对他的认知；
- 日记：每天睡前以她的第一人称写成；周记每周复盘并推进关系阶段；
- 召回：语义检索 + 时间衰减；久远低相似的记忆带"模糊"标注。
"""

import json
import time
from datetime import datetime, timedelta

from astrbot.api import logger

_FACTS_SYSTEM = (
    "你是记忆整理助手。从对话片段中提取值得长期记住的原子事实，并对照已有事实找出已过时的。"
    "事实要短、独立、无上下文依赖。subject 取值：user（关于他）、self（关于她自己）、"
    "npc:名字（关于她生活里的人物）。类别示例：生日/喜好/禁忌/工作/约定/经历/心事。"
    '输出 JSON：{"new": [{"subject": "...", "content": "...", "category": "..."}], '
    '"expire_ids": [数字id], "significant": true或false}。'
    "没有新东西就输出空数组。significant 表示这轮认识是否明显加深（值得更新小抄）。"
)

_CHEATSHEET_SYSTEM = (
    "你是{name}。下面是你关于恋人「{call_me}」的旧小抄和最新的事实清单。"
    "用你自己的口吻重写小抄（250字以内），保持三块：他是个什么样的人；我们现在怎么样；"
    "没做完的约定和他的心事。只输出小抄正文。"
)

_DIARY_SYSTEM = (
    "你是{name}。现在是{date}深夜，你在睡前写今天的日记。"
    "用第一人称、你自己的语气写 120~250 字：今天过得怎么样、发生了什么、"
    "和{call_me}聊了什么让你在意的、你的心情。像真的日记一样有细节有情绪，"
    "不要报流水账。只输出日记正文。"
)

_WEEKLY_SYSTEM = (
    "你是{name}。周日晚上，你翻看这一周的日记，写一篇周记（150~250字）：这周的主线、"
    "和{call_me}的关系有什么变化、下周的小期待。然后判断你们的关系阶段。"
    '输出 JSON：{{"weekly": "周记正文", "stage": "关系阶段(如 暧昧/热恋/稳定/老夫老妻)", '
    '"stage_changed": true或false, "new_milestone": "如果这周有值得纪念的第一次，写一句，否则空字符串"}}'
)


class MemoryPipeline:
    def __init__(self, app):
        self.app = app

    # ==================================================================
    # 事实沉淀（心跳空闲时调用）
    # ==================================================================
    async def maybe_consolidate(self):
        app = self.app
        if not await app.dao.kv_get("memory_dirty"):
            return
        last_user = await app.dao.kv_get("last_user_ts", 0)
        if time.time() - last_user < 600:  # 还在聊，先不打扰
            return
        await app.dao.kv_set("memory_dirty", 0)

        since = await app.dao.kv_get("consolidated_ts", 0)
        rows = await app.dao.chat_between(since, int(time.time()))
        if len(rows) < 2:
            return
        convo = "\n".join(
            f"{'他' if r['role'] == 'user' else '她'}：{r['content'][:200]}" for r in rows[-80:]
        )
        existing = await app.dao.list_facts(limit=120)
        facts_list = "\n".join(f"[{f['id']}] ({f['subject']}) {f['content']}" for f in existing)

        result = await app.llm.light_json(
            f"已有事实：\n{facts_list or '（无）'}\n\n最近对话：\n{convo}",
            system_prompt=_FACTS_SYSTEM,
        )
        if not isinstance(result, dict):
            return

        changed = 0
        for item in result.get("new") or []:
            subject = str(item.get("subject", "")).strip()
            content = str(item.get("content", "")).strip()
            if not subject or not content:
                continue
            fact_id = await app.dao.add_fact(
                subject, content, category=str(item.get("category", "")), source="chat"
            )
            vec_id = await app.vectors.add_memory(
                content, {"type": "fact", "fact_id": fact_id, "ts": int(time.time())}
            )
            if vec_id:
                await app.dao.set_fact_vec(fact_id, vec_id)
            changed += 1
        for fid in result.get("expire_ids") or []:
            try:
                await app.dao.expire_fact(int(fid))
                changed += 1
            except (ValueError, TypeError):
                continue

        await app.dao.kv_set("consolidated_ts", int(time.time()))
        if changed and result.get("significant"):
            await self.revise_cheatsheet()
        if changed:
            logger.info(f"[AstrLover] 记忆沉淀：{changed} 处变化。")

    async def revise_cheatsheet(self):
        """核心小抄（A1 第二层）：她自己修订对他的认知。"""
        app = self.app
        old = await app.cheatsheet_text()
        user_facts = await app.dao.list_facts(subject="user", limit=60)
        facts_text = "\n".join(f"- {f['content']}" for f in user_facts) or "（还不多）"
        stage = app.dynamic.stage(app.profile.stage)
        try:
            new_sheet = await app.llm.light(
                f"旧小抄：\n{old or '（还没写过）'}\n\n最新事实：\n{facts_text}\n\n当前关系阶段：{stage}",
                system_prompt=_CHEATSHEET_SYSTEM.format(name=app.profile.name, call_me=app.profile.call_me),
            )
            await app.dao.save_cheatsheet(new_sheet.strip(), reason="认识加深，自动修订")
            logger.info("[AstrLover] 她更新了自己的小抄。")
        except Exception as e:
            logger.warning(f"[AstrLover] 小抄修订失败：{e}")

    # ==================================================================
    # 日记 / 周记（A1 第四层）
    # ==================================================================
    async def write_daily_diary(self, date_str: str) -> bool:
        app = self.app
        if await app.dao.get_diary(date_str, "daily"):
            return False
        day_dt = datetime.fromisoformat(date_str)
        if app.clock.tz is not None:
            day_dt = day_dt.replace(tzinfo=app.clock.tz)
        day_start = int(day_dt.timestamp())
        chats = await app.dao.chat_between(day_start, day_start + 86400)
        events = [e for e in await app.dao.recent_events(20) if e["ts"] >= day_start]
        sched = await app.dao.day_schedule(date_str)
        done = [s for s in sched if s["status"] in ("done", "ongoing")]

        material = []
        if done:
            material.append("今天做了：" + "；".join(f"{s['activity']}{('（' + s['notes'] + '）') if s['notes'] else ''}" for s in done))
        if events:
            material.append("发生的事：" + "；".join(e["description"] for e in events[:6]))
        if chats:
            snippet = "\n".join(
                f"{'他' if c['role'] == 'user' else '我'}：{c['content'][:120]}" for c in chats[-40:]
            )
            material.append(f"和他聊的（节选）：\n{snippet}")
        if not material:
            material.append("今天没和他说上话，自己过了一天。")

        try:
            content = await app.llm.chat(
                prompt="\n\n".join(material),
                system_prompt=_DIARY_SYSTEM.format(
                    name=app.profile.name, date=date_str, call_me=app.profile.call_me
                ),
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 日记生成失败：{e}")
            return False
        vec_id = await app.vectors.add_memory(
            f"{date_str} 的日记：{content}",
            {"type": "diary", "date": date_str, "ts": int(time.time())},
        )
        await app.dao.save_diary(date_str, content.strip(), dtype="daily", vec_id=vec_id)
        logger.info(f"[AstrLover] 她写完了 {date_str} 的日记。")
        return True

    async def write_weekly(self, week_str: str) -> bool:
        """周记 + 关系复盘（驱动关系阶段演进）。"""
        app = self.app
        if await app.dao.get_diary(week_str, "weekly"):
            return False
        diaries = await app.dao.recent_diaries(7, "daily")
        if not diaries:
            return False
        text = "\n\n".join(f"{d['date']}：{d['content']}" for d in diaries)
        result = await app.llm.light_json(
            f"这一周的日记：\n{text}",
            system_prompt=_WEEKLY_SYSTEM.format(name=app.profile.name, call_me=app.profile.call_me),
        )
        if not isinstance(result, dict) or not result.get("weekly"):
            return False
        vec_id = await app.vectors.add_memory(
            f"{week_str} 的周记：{result['weekly']}",
            {"type": "weekly", "date": week_str, "ts": int(time.time())},
        )
        await app.dao.save_diary(week_str, str(result["weekly"]).strip(), dtype="weekly", vec_id=vec_id)
        if result.get("stage_changed") and result.get("stage"):
            app.dynamic.set_stage(str(result["stage"]))
            logger.info(f"[AstrLover] 关系阶段更新为：{result['stage']}")
        milestone = str(result.get("new_milestone") or "").strip()
        if milestone:
            app.dynamic.add_milestone(app.clock.today_str(), milestone, recurring=False)
        await self.revise_cheatsheet()
        return True

    # ==================================================================
    # 召回（语义 + 时间衰减 + A15 模糊）
    # ==================================================================
    async def recall(self, query: str) -> str:
        app = self.app
        hits = await app.vectors.search_memory(query, k=6)
        now = time.time()
        lines: list[str] = []
        fuzzy = False
        for h in hits:
            sim = h["similarity"]
            ts = h["meta"].get("ts", now)
            age_days = max(0.0, (now - ts) / 86400)
            score = sim * (0.6 + 0.4 * (0.98 ** age_days))  # 轻度时间衰减
            if score < 0.4:
                continue
            text = h["text"]
            if age_days > 21 and sim < 0.58:
                text = "（模糊的印象，记不太清了）" + text
                fuzzy = True
            lines.append(f"- {text}")
            if len(lines) >= 4:
                break
        if fuzzy:
            lines.append(
                "标了「模糊」的记忆你确实记不清了，提起时要带不确定的语气（好像…是吧？），他确认后你才恍然大悟。"
            )
        return "\n".join(lines)

    async def diaries_text(self) -> str:
        """最近一两天的日记：始终在场，保证跨天连续性。"""
        app = self.app
        rows = await app.dao.recent_diaries(2, "daily")
        # 只取昨天和前天的（今天的还没写）
        cutoff = (app.clock.today() - timedelta(days=2)).isoformat()
        lines = [f"{r['date']}：{r['content']}" for r in rows if r["date"] >= cutoff]
        return "\n\n".join(lines)
