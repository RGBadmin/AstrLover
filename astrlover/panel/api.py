"""Web 管理面板后端（系统性需求·双控制台）。

经 AstrBot Plugin Pages 机制暴露：所有请求都在 Dashboard JWT 鉴权之后
（面板等同导演权限，绝不公开暴露）。路由前缀 = 插件名 astrlover。
"""

import asyncio
import time

import yaml

from astrbot.api import logger
from astrbot.api.web import (
    PluginUploadFile,
    error_response,
    file_response,
    json_response,
    request,
)

from ..persona.profile import Profile

P = "astrlover"


class PanelApi:
    def __init__(self, app):
        self.app = app
        self._tagall_task: asyncio.Task | None = None

    def register(self):
        reg = self.app.context.register_web_api
        reg(f"/{P}/overview", self.overview, ["GET"], "运行总览")
        reg(f"/{P}/profile", self.get_profile, ["GET"], "读取生命档案")
        reg(f"/{P}/profile/save", self.save_profile, ["POST"], "保存生命档案")
        reg(f"/{P}/diaries", self.diaries, ["GET"], "日记列表")
        reg(f"/{P}/facts", self.facts, ["GET"], "事实记忆")
        reg(f"/{P}/cheatsheet", self.cheatsheet, ["GET"], "核心小抄")
        reg(f"/{P}/chatlog", self.chatlog, ["GET"], "对话记录")
        reg(f"/{P}/events", self.events, ["GET"], "生活事件流")
        reg(f"/{P}/pending", self.pending, ["GET"], "排期队列")
        reg(f"/{P}/pending/cancel", self.pending_cancel, ["POST"], "取消排期")
        reg(f"/{P}/export", self.export, ["GET"], "导出档案与记忆包")
        logger.info("[AstrLover] 面板 Web API 已注册。")

    # ------------------------------------------------------------------
    async def overview(self):
        app = self.app
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        st = app.star.state.get("proactive") or {}
        return json_response({
            "ready": app.ready,
            "linked_umo": str(app.star.state.get("director_target") or ""),
            "name": app.profile.name if app.profile else "",
            "now": app.clock.describe_now(app.profile.met_on, app.profile.anniversary),
            "activity": await app.life.current_activity() if app.life else "",
            "sleeping": app.life.sleeping_now() if app.life else False,
            "mood": await app.mood.prompt_text() if app.mood else "",
            "stage": app.dynamic.stage(str(app.profile.relationship.get("stage", ""))),
            "signature": app.dynamic.signature,
            "avatar_desc": app.dynamic.avatar_desc,
            "unanswered": int(st.get("unanswered", 0) or 0),
            "last_user_minutes": int((time.time() - last_user) / 60) if last_user else None,
            "vector_ok": app.vectors.available,
            "imagegen_ok": bool(app.imagegen and app.imagegen.available),
            "tts_ok": bool(app.voice and app.voice.tts_ready),
            "schedule": await app.dao.day_schedule(app.clock.today_str()),
        })

    # ------------------------------------------------------------------
    async def get_profile(self):
        p = self.app.persona_dir / "profile.yaml"
        d = self.app.persona_dir / "dynamic.yaml"
        return json_response({
            "profile": p.read_text(encoding="utf-8") if p.exists() else "",
            "dynamic": d.read_text(encoding="utf-8") if d.exists() else "",
        })

    async def save_profile(self):
        payload = await request.json(default={})
        text = str(payload.get("profile") or "")
        try:
            data = yaml.safe_load(text) or {}
            Profile(data)  # 校验必填
        except Exception as e:
            return error_response(f"档案格式不合法：{e}", status_code=400)
        path = self.app.persona_dir / "profile.yaml"
        path.write_text(text, encoding="utf-8")
        self.app.profile = Profile(data)
        return json_response({"saved": True})

    # ------------------------------------------------------------------
    async def diaries(self):
        limit = request.query.get("limit", 14, type=int)
        dtype = request.query.get("type", "daily")
        rows = await self.app.dao.recent_diaries(limit, dtype)
        return json_response({"items": rows})

    async def facts(self):
        subject = request.query.get("subject") or None
        rows = await self.app.dao.list_facts(subject=subject, limit=300)
        return json_response({"items": rows})

    async def cheatsheet(self):
        row = await self.app.dao.latest_cheatsheet()
        return json_response({"item": row})

    async def chatlog(self):
        limit = request.query.get("limit", 100, type=int)
        rows = await self.app.dao.recent_chat(limit)
        return json_response({"items": rows})

    async def events(self):
        limit = request.query.get("limit", 50, type=int)
        rows = await self.app.dao.recent_events(limit)
        return json_response({"items": rows})

    # ------------------------------------------------------------------
    async def pending(self):
        return json_response({"items": await self.app.dao.pending_list(50)})

    async def pending_cancel(self):
        payload = await request.json(default={})
        aid = payload.get("id")
        if not isinstance(aid, int):
            return error_response("缺少 id")
        await self.app.dao.finish_action(aid, "cancelled")
        return json_response({"ok": True})


    # ------------------------------------------------------------------

    async def export(self):
        from ..store.export import export_all

        include_gallery = request.query.get("gallery", 1, type=int) == 1
        path = await export_all(self.app, include_gallery=include_gallery)
        return file_response(path, filename=path.name, content_type="application/zip")


