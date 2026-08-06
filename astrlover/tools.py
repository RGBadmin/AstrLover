"""LLM 工具的实现体：main.py 的 @filter.llm_tool 委托到这里。

工具名与语义与她已习惯的一致；实现全部走新的 album/photos/imagegen/voice 子系统。
"""

from astrbot.api import logger

from .photos.sender import parse_photo_id

_WANT_SYSTEM = (
    "你是检索助手。根据角色想发照片的理由和最近的对话，"
    "给出用于在她相册里检索的关键词与画面描述。"
    '输出 JSON：{"keywords": "空格分隔的检索词", "want": "一句话画面描述", '
    '"rating": "生活|OOTD|性感|诱惑|露点|淫荡 之一，判断不出留空", '
    '"season": "春|夏|秋|冬|春秋|四季，判断不出留空"}。'
    "关键词要多给几个：地点、姿势、身体部位、衣着、动作都算。只输出 JSON。"
)


class Tools:
    def __init__(self, app):
        self.app = app

    # ================================================================ 相册
    async def browse_gallery(
        self, keywords: str = "", want: str = "", folder: str = "",
        prefer_sent: str = "", around: str = "", rating: str = "", season: str = "",
    ) -> str:
        app = self.app
        rows, report = await app.album.search(
            keywords=keywords, want=want, folder=folder, rating=rating,
            season=season, around=around, prefer_sent=prefer_sent or "fresh",
            top_k=int(app.star_conf.get("gallery_top_k", 10) or 10),
            fetch_k=int(app.star_conf.get("gallery_fetch_k", 60) or 60),
        )
        if not rows:
            hint = "相册里没有匹配的。"
            if not app.album.scanner.root():
                hint += "（相册目录还没配置）"
            elif report.lexical_hits == 0 and report.semantic_hits == 0:
                hint += "换几个词再试，或者用 generate_photo 现场拍一张。"
            return hint
        lines = ["相册候选（挑一张，然后用 send_photo 发出去）："]
        for r in rows:
            tag = "/".join(x for x in (r["rating"], r["season"]) if x)
            sent = f"发过{r['sent_count']}次" if r["sent_count"] else "没发过"
            lines.append(f"g{r['id']}｜{tag}｜{sent}｜{(r['desc'] or '')[:110]}")
        return "\n".join(lines)

    async def want_photo(self, event, reason: str = "") -> str:
        """她只说"为什么想发"，由轻模型想检索词，再走同一套检索。"""
        app = self.app
        prompt = f"角色想发照片的理由：{reason or '（没说，看最近对话）'}"
        plan = await app.llm.light_json(prompt, system_prompt=_WANT_SYSTEM)
        if not isinstance(plan, dict):
            plan = {}
        return await self.browse_gallery(
            keywords=str(plan.get("keywords") or reason),
            want=str(plan.get("want") or reason),
            rating=str(plan.get("rating") or ""),
            season=str(plan.get("season") or ""),
        )

    async def send_photo(self, event, photo_id: str, caption: str = "") -> str:
        return await self.app.sender.send(event, photo_id, caption)

    async def inspect_photo(self, photo_id: str = "") -> str:
        app = self.app
        parsed = parse_photo_id(photo_id)
        if parsed is None:
            return "编号格式不对。相册里的填 g123，聊过的图填 #3。"
        kind, num = parsed
        if kind == "album":
            row = await app.album.get(num)
            if row is None:
                return f"相册里没有 g{num}。"
            if not row["desc"]:
                return f"g{num} 还没索引过（跑 /gallery index）。"
            return f"g{num}｜{row['rating']}｜{row['season']}\n{row['desc']}"
        row = await app.photos.get(num)
        if row is None:
            return f"存档里没有 #{num}。"
        bits = []
        if row["catalog"]:
            bits.append(f"你当时记的：{row['catalog']}")
        if row["detail"]:
            bits.append(f"画面细节：{row['detail']}")
        if not bits:
            return f"#{num} 还没有任何描述记录。"
        return "\n".join(bits)

    async def find_photo(self, keywords: str = "", day: str = "") -> str:
        rows = await self.app.photos.search(keywords, day)
        if not rows:
            return "存档里没找到符合的旧图。"
        lines = ["聊过的旧图候选（不止一张时，问他是哪张，别自己挑）："]
        for r in rows:
            desc = r["catalog"] or (r["detail"] or "")[:80] or "（没有描述）"
            lines.append(f"#{r['id']}｜{desc[:110]}")
        return "\n".join(lines)

    async def recall_photo(self, photo_id: str) -> str:
        parsed = parse_photo_id(photo_id)
        if parsed is None:
            return "编号格式不对，填对话里 [图片 #N] 的那个数字。"
        _kind, num = parsed
        row = await self.app.photos.get(num)
        if row is None:
            return f"存档里没有 #{num}。"
        if self.app.photos.abs_path(row) is None:
            return f"#{num} 的原图文件不在了；用 inspect_photo 看文字记录吧。"
        self.app.photo_memory.queue_recall(num)
        return f"#{num} 已取回，你在下一次回复时就能看到这张图。"

    # ================================================================ 生图
    async def generate_photo(self, event, situation: str, caption: str = "") -> str:
        app = self.app
        if app.imagegen is None or not app.imagegen.available:
            return "拍不了（生图后端没配置），用 browse_gallery 在相册里找一张吧。"
        path = await app.imagegen.generate(situation)
        if not path:
            return "没拍成（生成失败了），用 browse_gallery 找一张类似的吧。"
        from astrbot.api.event import MessageChain

        chain = MessageChain()
        if caption:
            chain.message(caption)
        chain.file_image(path)
        await event.send(chain)
        if app.dao:
            await app.dao.add_event("photo_gen", f"现拍了一张照片给他：{situation[:50]}", motivation="")
        logger.info(f"[AstrLover] 生成并发送照片：{situation[:40]}")
        return "照片已经发出去了。照常继续说你的话。"

    # ================================================================ 语音
    async def send_voice(self, event, text: str) -> str:
        app = self.app
        if app.voice is None or not app.voice.tts_ready:
            return "语音发不了（TTS 未配置），把这段话用文字说出来吧。"
        record = await app.voice.tts_record(text)
        if record is None:
            return "语音生成失败了，把这段话用文字说出来吧。"
        from astrbot.api.event import MessageChain

        await event.send(MessageChain(chain=[record]))
        return "语音已发出。照常继续，不用复述语音内容。"
