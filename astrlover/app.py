"""App：全部子系统的装配中心与共享门面。

架构：骑在 AstrBot 默认对话管线上——对话历史、会话人格、其他插件、
WebUI 一切照旧；本插件在管线上做两件事：
  注入（on_llm_request）  她的此刻 + 历史动态时间线 + 图片记忆
  捕获（on_llm_response） 图片目录层描述、生命层内部标记
外加她自己的能力（相册/照片/动态/头像签名/语音/生图）作为 LLM 工具，
以及一个只认管理员的导演 bot（插件自持 PTB，与 AstrBot 平台无关）。
"""

import time
from pathlib import Path

from astrbot.api import logger

try:
    from astrbot.api.star import StarTools
except ImportError:
    from astrbot.core.star.star_tools import StarTools

from .actions import ActionExecutor
from .album.service import Album
from .markers import extract_internal
from .config import Cfg
from .director.bot import DirectorBot
from .director.bridge import DirectorBridge
from .heart.heartbeat import Heartbeat
from .heart.impulses import Impulses
from .heart.proactive import Proactive
from .imagegen.base import ImageGen
from .life.clock import Clock
from .life.engine import LifeEngine
from .life.mood import MoodEngine
from .llm import LLM
from .memory.pipeline import MemoryPipeline
from .panel.api import PanelApi
from .persona.prompt import build_life_block
from .records import Records
from .settings import Settings
from .photos.archive import PhotoArchive
from .photos.memory import PhotoMemory
from .photos.sender import PhotoSender
from .presence.limits import Limits
from .presence.moments import Moments
from .presence.profile import ProfileFace
from .store.dao import Dao
from .store.db import Database
from .store.vectors import Vectors
from .tools import Tools
from .vision.client import VisionClient
from .voice.service import VoiceService

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class App:
    def __init__(self, star, context, flat_conf: dict):
        self.star = star
        self.context = context
        self.conf = Settings(flat_conf)     # 接线来自 AstrBot 配置页，其余存数据库
        self.cfg = Cfg(self.conf)           # 生命层配置视图
        self.ready = False                  # 生命模拟层是否可用
        self.booted = False                 # 存储与 presence 子系统是否可用

        self.data_dir: Path = Path(StarTools.get_data_dir("astrlover"))
        self.vec_dir = self.data_dir / "vec"
        self.voice_dir = self.data_dir / "voice"
        self.gallery_dir = self.data_dir / "generated"
        self.export_dir = self.data_dir / "exports"

        self.db: Database | None = None
        self.dao: Dao | None = None
        self.vectors: Vectors | None = None
        self.clock: Clock | None = None
        self.llm: LLM | None = None

        # presence 侧
        self.vision: VisionClient | None = None
        self.album: Album | None = None
        self.photos: PhotoArchive | None = None
        self.photo_memory: PhotoMemory | None = None
        self.sender: PhotoSender | None = None
        self.moments: Moments | None = None
        self.face: ProfileFace | None = None
        self.limits: Limits | None = None
        self.tools: Tools | None = None
        self.imagegen: ImageGen | None = None
        self.voice: VoiceService | None = None

        # 导演
        self.bridge: DirectorBridge | None = None
        self.director_bot: DirectorBot | None = None
        self.state_target: str = ""

        # 生命层
        self.records: Records | None = None
        self.memory: MemoryPipeline | None = None
        self.life: LifeEngine | None = None
        self.mood: MoodEngine | None = None
        self.proactive: Proactive | None = None
        self.impulses: Impulses | None = None
        self.actions: ActionExecutor | None = None
        self.heart: Heartbeat | None = None
        self.panel: PanelApi | None = None

    # ==================================================================
    # 生命周期
    # ==================================================================
    def _clear_vec_dir(self):
        n = 0
        for p in self.vec_dir.glob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)
                n += 1
        if n:
            logger.warning(f"[AstrLover] 向量库已随数据库一起清空（{n} 个文件），相册会自动重跑索引。")

    async def initialize(self):
        for d in (self.vec_dir, self.voice_dir, self.gallery_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.clock = Clock(self.cfg.timezone)
        self.db = Database(self.data_dir / "astrlover.db")
        await self.db.open()
        if self.db.was_reset:
            # 向量库里存的是行 id，库重建了这些 id 全指空——一起清掉重跑索引
            self._clear_vec_dir()
        self.dao = Dao(self.db)
        await self.conf.load(self.dao)
        self.vectors = Vectors(self.vec_dir, self.context, self.cfg.embedding_provider_id)
        self.llm = LLM(self.context, self.cfg)

        self.records = Records(self)
        self.vision = VisionClient(self.conf)
        self.album = Album(self)
        self.photos = PhotoArchive(self)
        self.photo_memory = PhotoMemory(self)
        self.sender = PhotoSender(self)
        self.limits = Limits(self)
        self.moments = Moments(self)
        self.face = ProfileFace(self)
        self.imagegen = ImageGen(self)
        self.voice = VoiceService(self)
        self.tools = Tools(self)

        self.state_target = str(await self.dao.kv_get("director_target", "") or "")
        self.bridge = DirectorBridge(self)
        self.director_bot = DirectorBot(self)
        await self.director_bot.start()
        self.booted = True

        # ---- 生命模拟层（可关）----
        if self.cfg.enabled:
            await self._init_life()

        self.actions = ActionExecutor(self)
        self.heart = Heartbeat(self)
        self.heart.start()

        self.panel = PanelApi(self)
        self.panel.register()
        logger.info("[AstrLover] 装配完成。")

    async def _init_life(self):
        """生命层：她是谁由人格负责，这里只装配记录与心跳相关的子系统。"""
        self.memory = MemoryPipeline(self)
        self.life = LifeEngine(self)
        self.mood = MoodEngine(self.dao)
        self.proactive = Proactive(self)
        self.impulses = Impulses(self)
        self.ready = True
        logger.info("[AstrLover] 生命模拟层就绪。")

    async def terminate(self):
        self.ready = self.booted = False
        if self.heart:
            await self.heart.stop()
        if self.director_bot:
            await self.director_bot.stop()
        if self.photo_memory:
            self.photo_memory.cancel_all()
        if self.album:
            self.album.indexer.stop()
            self.album.embedder.stop()
        if self.vectors:
            await self.vectors.close()
        if self.db:
            await self.db.close()

    # ==================================================================
    # 管线钩子
    # ==================================================================
    def is_partner(self, event) -> bool:
        pid = self.cfg.partner_id
        return bool(pid) and str(event.get_sender_id()) == pid

    @staticmethod
    def inject_text(req, block: str) -> str:
        """优先塞进 user turn 末尾（贴着当前问题，且不落进对话历史），
        回退 system_prompt。"""
        try:
            from astrbot.core.agent.message import TextPart

            req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
            return "extra_user_content_parts"
        except Exception:
            req.system_prompt = (req.system_prompt or "") + "\n\n" + block + "\n"
            return "system_prompt"

    async def on_llm_request(self, event, req):
        """一次请求里做完全部注入，顺序自己控制（不靠多个钩子的优先级）。"""
        if not self.booted:
            return
        try:
            await self.photo_memory.register(req)      # 登记落盘 + 派发细节层
            await self.photo_memory.ask_descriptions(req)
            await self.photo_memory.serve_recalled(req)
            await self.moments.inject(req)             # 历史动态插进时间线
            await self.photo_memory.prune(req)         # 最后折叠（编号都已分配）
        except Exception:
            logger.error("[AstrLover] presence 注入失败：", exc_info=True)

        if not self.ready:
            return
        try:
            is_partner = self.is_partner(event)
            if is_partner:
                self.llm.owner_umo = event.unified_msg_origin
                if text := (event.message_str or "").strip():
                    await self.mood.on_user_message(text)
                await self.proactive.on_user_message()
            query = (event.message_str or "").strip() or "聊天"
            extra = "" if is_partner else (
                "【注意】现在跟你说话的不是你的恋人本人（可能是陌生人或群聊），"
                "按你的性格和边界应对：礼貌、有分寸、不透露你们的私事。"
            )
            self.inject_text(req, await self.build_life_block(query, extra_note=extra))
        except Exception:
            logger.error("[AstrLover] 生命层注入失败：", exc_info=True)

    async def on_llm_response(self, event, resp):
        if not self.booted:
            return
        text = getattr(resp, "completion_text", None)
        if not isinstance(text, str) or not text:
            return
        out = text
        try:
            out, _n = await self.photo_memory.capture(out)
        except Exception:
            logger.error("[AstrLover] 图片描述捕获失败：", exc_info=True)
        if self.ready and self.is_partner(event):
            try:
                out, improvs, told, found = extract_internal(out)
                for note in improvs:
                    await self.fix_improvised(note)
                for eid in told:
                    await self.dao.set_event_mention(eid, "told")
                for eid in found:
                    await self.dao.set_event_mention(eid, "discovered")
                await self.dao.kv_set("memory_dirty", 1)
            except Exception:
                logger.error("[AstrLover] 生命层响应处理失败：", exc_info=True)
        if out != text:
            resp.completion_text = out.strip()

    async def silent_now(self) -> bool:
        """/noreply 静默期：她先听着不回话。"""
        if not self.booted:
            return False
        until = await self.dao.kv_get("silent_until", 0) or 0
        return until == -1 or (until > 0 and until > time.time())

    # ==================================================================
    # 提示词
    # ==================================================================
    async def build_life_block(self, query_text: str, extra_note: str = "") -> str:
        """注入块：她的此刻 + 记忆 + 近况，全部来自记录。"""
        milestones = await self.records.milestones()
        return build_life_block(
            clock_text=self.clock.describe_now(milestones),
            stage=await self.records.get_state("stage"),
            life_text=await self.life.prompt_text() if self.life else "",
            mood_text=await self.mood.prompt_text() if self.mood else "",
            appearance_note=await self.records.get_state("appearance"),
            cheatsheet=await self.cheatsheet_text(),
            diaries_text=await self.memory.diaries_text(),
            memories_text=await self.memory.recall(query_text),
            events_text=await self.events_text(),
            extra_note=extra_note,
        )

    async def on_settings_changed(self, changed: list[str]):
        """设置改完即时生效：把持有旧值的组件重置掉，其余下次读就是新值。"""
        if any(k.startswith("vision_") or k.startswith("gemini_") for k in changed):
            self.vision._gate = None          # 并发数可能变了
        if any(k.startswith("ig_") for k in changed):
            self.imagegen = ImageGen(self)    # 后端顺序/密钥变了，重建降级链
        if "gallery_dir" in changed:
            logger.info("[AstrLover] 相册目录已改，记得跑 /gallery scan")
        logger.info(f"[AstrLover] 设置已更新：{'、'.join(changed)}")

    async def appearance_text(self) -> str:
        """生图用的外观基准。第一次要用时让她自己描述一次，存成记录；
        之后可以 /rec set appearance 改，剪头发之类的演变也写回这里。"""
        if text := await self.records.get_state("appearance"):
            return text
        if not self.state_target:
            return ""
        try:
            text = await self.bridge.generate(
                "用一句话客观描述你的长相、身材和常穿的风格（发型发色、五官特点、身高体型、"
                "穿衣风格），供画像参考。只输出这一句，不要解释。", instruct="",
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 外观基准生成失败：{e}")
            return ""
        text = text.strip()[:200]
        await self.records.set_state("appearance", text)
        logger.info(f"[AstrLover] 外观基准已记下：{text}")
        return text

    async def cheatsheet_text(self) -> str:
        row = await self.dao.latest_cheatsheet()
        return row["content"] if row else ""

    async def events_text(self) -> str:
        rows = await self.dao.unmentioned_events(n=5)
        if not rows:
            return ""
        lines = [
            f"[{r['id']}] {r['description']}"
            + (f"（当时的想法：{r['motivation']}）" if r["motivation"] else "")
            for r in rows
        ]
        lines.append(
            "这些事他还不知道。想说就自然地讲；不说也行，等他自己发现——发现时要用当时真实的理由回答。"
        )
        return "\n".join(lines)

    # ==================================================================
    # 跨子系统动作
    # ==================================================================
    async def set_target(self, umo: str):
        self.state_target = umo
        await self.dao.kv_set("director_target", umo)
        if umo:
            self.llm.owner_umo = umo
        logger.info(f"[AstrLover] 绑定会话：{umo or '（无）'}")

    async def send_photo_as_her(self, photo_id: str, caption: str = "") -> str:
        """控制台/排期用：借她的身份把照片发到绑定会话。"""
        from astrbot.api.event import MessageChain

        kind, row, path = await self.sender.resolve(photo_id)
        if row is None or path is None:
            return f"找不到 {photo_id} 或文件已不在。"
        chain = MessageChain()
        if caption:
            chain.message(caption)
        chain.file_image(str(path))
        try:
            ok = await self.context.send_message(self.state_target, chain)
        except Exception as e:
            return f"没发出去：{e}"
        if not ok:
            return "找不到目标平台，那个 bot 还连着吗？"
        if kind == "album":
            await self.album.mark_sent(int(row["id"]))
        note = caption or f"[发了一张照片 {photo_id}]"
        await self.bridge.append_assistant(self.state_target, note)
        return f"已发出 {photo_id}" + (f"，附言：{caption}" if caption else "")

    async def fix_improvised(self, note: str):
        fact_id = await self.dao.add_fact("self", note, category="编造固化", source="improvise")
        if vec_id := await self.vectors.add_memory(note, {"type": "fact", "fact_id": fact_id}):
            await self.dao.set_fact_vec(fact_id, vec_id)
        logger.info(f"[AstrLover] 编造固化：{note}")

    # ==================================================================
    # 控制台委托：相册 / 视觉 / 状态
    # ==================================================================
    async def gallery_command(self, arg: str, progress_cb=None) -> str:
        album = self.album
        parts = (arg or "").split()
        action = parts[0].lower() if parts else ""
        rest = parts[1:]

        if not action:
            return await album.overview()
        if action == "scan":
            if rest and rest[0] == "reset":
                if len(rest) > 1 and rest[1] == "go":
                    return await album.reset()
                return "这会清空整个库（描述和向量一起没）。确定就发 /gallery scan reset go"
            res = await album.scanner.scan(
                prune=bool(rest and rest[0] == "prune"),
                use_snowflake=bool(self.conf.get("use_snowflake_time", True)),
            )
            if "error" in res:
                return res["error"]
            return (f"扫描完成：磁盘 {res['total']} 张，新增 {res['added']} 张"
                    + (f"，清理 {res['pruned']} 条" if res["pruned"] else "")
                    + (f"\n分类：{'、'.join(res['folders'])}" if res["folders"] else ""))
        if action == "index":
            sub = rest[0] if rest else ""
            if sub == "stop":
                album.indexer.stop()
                return "已停止后台索引，进度不丢。"
            if sub in ("auto", ""):
                if album.indexer.start_auto(progress_cb):
                    return "后台索引已开始，跑完会报告。/gallery index stop 可以停。"
                return "后台索引已经在跑了。"
            if sub.isdigit():
                return await album.indexer.run_count(int(sub), progress_cb)
            return "用法：/gallery index auto | 50 | stop"
        if action == "embed":
            sub = rest[0] if rest else ""
            if sub == "stop":
                album.embedder.stop()
                return "已停止向量转换。"
            if sub == "test":
                return await album.embedder.probe()
            if sub == "redo":
                return await album.embedder.redo_all()
            if sub in ("auto", ""):
                if album.embedder.start_auto(progress_cb):
                    return "后台向量转换已开始。"
                return "向量转换已经在跑了。"
            if sub.isdigit():
                return await album.embedder.run_count(int(sub), progress_cb)
            return "用法：/gallery embed auto | 500 | test | redo | stop"
        if action == "search":
            if not rest:
                return "用法：/gallery search 黑丝 车里"
            rows, report = await album.search(keywords=" ".join(rest), top_k=8)
            head = report.text()
            if not rows:
                return head + "\n（没有结果）"
            body = "\n".join(
                f"g{r['id']}｜{r['rating']}/{r['season']}｜{(r['desc'] or '')[:80]}" for r in rows
            )
            return head + "\n" + body
        if action == "show":
            row = await album.get(int(rest[0].lstrip("gG"))) if rest and rest[0].lstrip("gG").isdigit() else await album.random_ok()
            if row is None:
                return "没有这张，或库里还没有已索引的图。"
            return f"g{row['id']}｜{row['path']}\n{row['rating']}/{row['season']}\n{row['desc']}"
        if action == "polish":
            return await album.polish()
        if action == "clean":
            return await album.clean()
        if action == "retry":
            return await album.retry()
        if action == "audit":
            return await album.audit()
        if action == "redo":
            if rest and rest[0].lstrip("gG").isdigit():
                return await album.redo(int(rest[0].lstrip("gG")))
            return "用法：/gallery redo g123"
        if action.lstrip("gG").isdigit():
            row = await album.get(int(action.lstrip("gG")))
            if row is None:
                return f"没有 {action} 这张。"
            return f"g{row['id']}｜{row['path']}\n{row['desc'][:400]}"
        return ("用法：/gallery [scan|index|embed|search|show|polish|clean|retry|audit|redo]\n"
                "详见 README。")

    async def vision_command(self, arg: str) -> str:
        sub = (arg or "").strip().lower()
        if sub == "test":
            cfg = self.vision.config()
            if cfg is None:
                return "视觉 API 未配置（地址/Key/模型三项都要填）。"
            head = f"格式 {cfg.fmt} · 模型 {cfg.model}\n地址 {self.vision._url(cfg)}"
            row = await self.album.random_ok() or None
            path = None
            if row:
                path = self.album.abs_path(row["path"])
            if path is None:
                rows = await self.db.fetchall("SELECT * FROM photo_archive ORDER BY id DESC LIMIT 1")
                if rows:
                    path = self.photos.abs_path(rows[0])
            if path is None:
                return head + "\n\n（没有可用来测试的图片，先 /gallery scan 或在聊天里发一张）"
            try:
                text, _ = await self.vision.describe_once(str(path))
                return head + f"\n\n✅ 通了，返回 {len(text)} 字：\n{text[:300]}"
            except Exception as e:
                return head + f"\n\n❌ {type(e).__name__}: {e}"
        if sub in ("", "backfill"):
            return await self.photo_memory.backfill_details()
        return "用法：/vision test | /vision backfill"

    async def status_report(self) -> str:
        lines = ["🌸 AstrLover 状态", ""]
        lines.append(f"🔗 绑定会话：{self.state_target or '（未绑定，/umo 看、/link 绑）'}")
        if self.ready:
            lines.append(self.clock.describe_now(await self.records.milestones()))
            cur = await self.life.current_activity()
            lines.append(f"🧍 此刻：{cur}" + ("（睡眠时段）" if await self.life.sleeping_now() else ""))
            if sched := await self.dao.day_schedule(self.clock.today_str()):
                lines.append("📅 " + "；".join(f"{s['start_hm']} {s['activity']}[{s['status']}]" for s in sched))
            lines.append(f"💭 {await self.mood.prompt_text() or '心情平静。'}")
            facts = len(await self.dao.list_facts(limit=1000))
            sheet = await self.dao.latest_cheatsheet()
            diaries = await self.dao.recent_diaries(1)
            lines.append(
                f"🧠 记忆：事实 {facts} 条 · 小抄 v{sheet['version'] if sheet else 0} · "
                f"最近日记 {diaries[0]['date'] if diaries else '（还没写过）'}"
            )
            if stage := await self.records.get_state("stage"):
                lines.append(f"💞 关系阶段：{stage}")
        else:
            lines.append("（生命模拟层未启用）")
        st = await self.album.stats()
        lines.append(
            f"🖼 相册：已索引 {st.get('ok', 0)} · 待索引 {st.get('pending', 0)} · "
            f"失败 {st.get('failed', 0)} · 已转向量 {st.get('embedded', 0)}"
        )
        checks = [
            ("视觉", self.vision.ready()),
            ("向量", self.vectors.available or not self.vectors._init_failed),
            ("生图", bool(self.imagegen and self.imagegen.available)),
            ("语音", bool(self.voice and self.voice.tts_ready)),
            ("频道", bool(self.moments.channel())),
        ]
        lines.append("🔌 " + "  ".join(f"{n}{'✅' if ok else '❌'}" for n, ok in checks))
        if self.ready:
            lines.append("💌 " + (await self.proactive.status()).splitlines()[0])
        return "\n".join(lines)
