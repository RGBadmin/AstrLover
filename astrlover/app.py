"""App：生命模拟层的装配中心（管线形态）。

自 v0.3 起不再接管对话：她的"生命"（人格/记忆/生活/情绪/时间/事件）
经 on_llm_request 注入 AstrBot 默认管线的 system prompt；对话素材经
钩子回流（chat_log 只作日记与沉淀的素材，上下文由 AstrBot 对话历史承载）。
presence 层（相册/照片/动态/头像签名/控制台）在 astrlover/presence/。
"""

import shutil
import time
from pathlib import Path

from astrbot.api import logger

try:
    from astrbot.api.star import StarTools
except ImportError:
    from astrbot.core.star.star_tools import StarTools

from .actions import ActionExecutor
from .chat.composer import extract_internal
from .config import Cfg
from .heart.desire import Desire
from .heart.heartbeat import Heartbeat
from .heart.impulses import Impulses
from .life.clock import Clock
from .life.engine import LifeEngine
from .life.mood import MoodEngine
from .llm import LLM
from .memory.pipeline import MemoryPipeline
from .memory.working import WorkingMemory
from .panel.api import PanelApi
from .persona.dynamic import DynamicState
from .persona.profile import Profile
from .persona.prompt import build_system_prompt
from .store.dao import Dao
from .store.db import Database
from .store.vectors import Vectors

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class App:
    def __init__(self, star, context, flat_conf: dict):
        self.star = star          # AstrLover 实例（同时是 PresenceCore）
        self.context = context
        self.cfg = Cfg(flat_conf)
        self.ready = False

        self.data_dir: Path = Path(StarTools.get_data_dir("astrlover"))
        self.persona_dir = self.data_dir / "persona"
        self.vec_dir = self.data_dir / "vec"
        self.voice_dir = self.data_dir / "voice"
        self.gallery_dir = self.data_dir / "gallery" / "files"  # 生图产物落这里
        self.export_dir = self.data_dir / "exports"

        self.db: Database | None = None
        self.dao: Dao | None = None
        self.vectors: Vectors | None = None
        self.profile: Profile | None = None
        self.dynamic: DynamicState | None = None
        self.clock: Clock | None = None
        self.llm: LLM | None = None
        self.working: WorkingMemory | None = None
        self.memory: MemoryPipeline | None = None
        self.life: LifeEngine | None = None
        self.mood: MoodEngine | None = None
        self.heart: Heartbeat | None = None
        self.panel: PanelApi | None = None

        # N3/N4 阶段接回的服务（心跳与面板对它们做 None 保护）
        self.actions = None
        self.impulses = None
        self.desire = None
        self.planner = None
        self.gallery = None
        self.imagegen = None
        self.voice = None
        self.tgsvc = None
        self.channel_hub = None

    # ==================================================================
    # 生命周期
    # ==================================================================
    async def initialize(self):
        if not self.cfg.enabled:
            logger.info("[AstrLover] 生命模拟层已在配置中关闭，仅运行 presence 功能。")
            return

        for d in (self.persona_dir, self.vec_dir, self.voice_dir, self.gallery_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)

        profile_path = self.persona_dir / "profile.yaml"
        if not profile_path.exists():
            shutil.copy(_PLUGIN_ROOT / "examples" / "persona.example.yaml", profile_path)
            logger.info(f"[AstrLover] 已生成生命档案模板：{profile_path}，请按需编辑。")
        self.profile = Profile.load(profile_path)
        self.dynamic = DynamicState(self.persona_dir / "dynamic.yaml")
        self.dynamic.load()
        if self.profile.met_on:
            self.dynamic.add_milestone(self.profile.met_on, "认识纪念日")
        if self.profile.anniversary:
            self.dynamic.add_milestone(self.profile.anniversary, "在一起的纪念日")

        self.clock = Clock(self.cfg.timezone)
        self.db = Database(self.data_dir / "astrlover.db")
        await self.db.open()
        self.dao = Dao(self.db)
        self.vectors = Vectors(self.vec_dir, self.context, self.cfg.embedding_provider_id)
        self.llm = LLM(self.context, self.cfg)
        umo = await self.dao.kv_get("linked_umo")
        if umo:
            self.llm.owner_umo = umo

        self.working = WorkingMemory(self.dao)
        self.memory = MemoryPipeline(self)
        self.life = LifeEngine(self)
        self.mood = MoodEngine(self.dao)
        self.desire = Desire(self)
        self.impulses = Impulses(self)
        self.actions = ActionExecutor(self)
        self.heart = Heartbeat(self)

        self.panel = PanelApi(self)
        self.panel.register()

        await self._seed_backstory()
        self.heart.start()
        self.ready = True
        logger.info(f"[AstrLover] 生命模拟层就绪：{self.profile.name} 醒来了。")

    async def terminate(self):
        self.ready = False
        if self.heart:
            await self.heart.stop()
        if self.vectors:
            await self.vectors.close()
        if self.db:
            await self.db.close()

    # ==================================================================
    # 管线钩子实现（main.py 委托过来）
    # ==================================================================
    def _is_partner(self, event) -> bool:
        pid = self.cfg.partner_id
        return bool(pid) and str(event.get_sender_id()) == pid

    async def hook_llm_request(self, event, req):
        """把她的"此刻"注入 system prompt；恋人的话进入她的记忆素材。"""
        if not self.ready:
            return
        try:
            is_partner = self._is_partner(event)
            if is_partner:
                self.llm.owner_umo = event.unified_msg_origin
                await self.dao.kv_set("linked_umo", event.unified_msg_origin)
                text = (event.message_str or "").strip()
                if text:
                    await self.working.log_user(text)
                    await self.mood.on_user_message(text)
                await self.dao.kv_set("last_user_ts", int(time.time()))
                # 他回话了：presence 的"连续未回"计数清零
                # （其 note_user_activity 只在自带倒计时开启时处理）
                st = self.star.state.setdefault("proactive", {})
                if st.get("unanswered"):
                    st["unanswered"] = 0
                    self.star._save_state()

            query = (event.message_str or "").strip() or "聊天"
            extra = "" if is_partner else (
                "【注意】现在跟你说话的不是你的恋人本人（可能是陌生人或群聊），"
                "按你的性格和边界应对：礼貌、有分寸、不透露你们的私事。"
            )
            life_context = await self.build_master_prompt(query, extra_note=extra)
            req.system_prompt = (req.system_prompt or "") + "\n\n" + life_context
        except Exception:
            logger.error("[AstrLover] 生命层注入失败：", exc_info=True)

    async def hook_llm_response(self, event, resp):
        """摘走内部标记（编造固化/事件提及），回复文本回流记忆素材。"""
        if not self.ready or not self._is_partner(event):
            return
        try:
            text = getattr(resp, "completion_text", None) or ""
            if not text:
                return
            clean, improvs, told, found = extract_internal(text)
            for note in improvs:
                await self.fix_improvised(note)
            for eid in told:
                await self.dao.set_event_mention(eid, "told")
            for eid in found:
                await self.dao.set_event_mention(eid, "discovered")
            if clean != text:
                resp.completion_text = clean
            if clean.strip():
                await self.working.log_her(clean.strip())
            await self.dao.kv_set("memory_dirty", 1)
        except Exception:
            logger.error("[AstrLover] 生命层响应处理失败：", exc_info=True)

    # ==================================================================
    # 提示词组装
    # ==================================================================
    async def build_master_prompt(self, query_text: str, extra_note: str = "") -> str:
        clock_text = self.clock.describe_now(self.profile.met_on, self.profile.anniversary)
        specials = self.clock.upcoming_specials(
            self.dynamic.milestones, self.profile.birthday, within_days=3
        )
        if specials:
            clock_text += "（" + "；".join(specials) + "）"
        return build_system_prompt(
            self.profile,
            self.dynamic,
            clock_text=clock_text,
            life_text=await self.life.prompt_text() if self.life else "",
            mood_text=await self.mood.prompt_text() if self.mood else "",
            cheatsheet=await self.cheatsheet_text(),
            diaries_text=await self.memory.diaries_text(),
            memories_text=await self.memory.recall(query_text),
            events_text=await self.events_text(),
            extra_note=extra_note,
            pipeline=True,
        )

    async def cheatsheet_text(self) -> str:
        row = await self.dao.latest_cheatsheet()
        return row["content"] if row else ""

    async def events_text(self) -> str:
        rows = await self.dao.unmentioned_events(n=5)
        if not rows:
            return ""
        lines = []
        for r in rows:
            motive = f"（当时的想法：{r['motivation']}）" if r["motivation"] else ""
            lines.append(f"[{r['id']}] {r['description']}{motive}")
        lines.append(
            "这些事他还不知道。想说就自然地讲；不说也行，等他自己发现——发现时要用当时真实的理由回答。"
        )
        return "\n".join(lines)

    # ==================================================================
    # 跨子系统动作
    # ==================================================================
    async def fix_improvised(self, note: str):
        """A6 编造固化：临场发挥立刻成为永久事实。"""
        fact_id = await self.dao.add_fact("self", note, category="编造固化", source="improvise")
        vec_id = await self.vectors.add_memory(note, {"type": "fact", "fact_id": fact_id})
        if vec_id:
            await self.dao.set_fact_vec(fact_id, vec_id)
        logger.info(f"[AstrLover] 编造固化：{note}")

    async def linked_umo(self) -> str:
        return str(await self.dao.kv_get("linked_umo") or "")

    async def _seed_backstory(self):
        if await self.dao.kv_get("backstory_seeded"):
            return
        for item in self.profile.backstory:
            fact_id = await self.dao.add_fact("self", item, category="身世", source="init")
            vec_id = await self.vectors.add_memory(item, {"type": "fact", "fact_id": fact_id})
            if vec_id:
                await self.dao.set_fact_vec(fact_id, vec_id)
        await self.dao.kv_set("backstory_seeded", 1)
