"""Web 管理面板后端（AstrBot Plugin Pages）。

所有请求都在 Dashboard JWT 鉴权之后——面板等同最高权限，绝不公开暴露。
重交互（相册索引、行为编排）在控制台 bot 里做；面板负责"看"与"编辑档案"。
"""

import time

from astrbot.api import logger
from astrbot.api.web import error_response, file_response, json_response, request

P = "astrlover"


class PanelApi:
    def __init__(self, app):
        self.app = app

    def register(self):
        reg = self.app.context.register_web_api
        reg(f"/{P}/overview", self.overview, ["GET"], "运行总览")
        reg(f"/{P}/records", self.records_list, ["GET"], "列出记录")
        reg(f"/{P}/records/mutate", self.records_mutate, ["POST"], "增删改记录")
        reg(f"/{P}/diaries", self.diaries, ["GET"], "日记列表")
        reg(f"/{P}/facts", self.facts, ["GET"], "事实记忆")
        reg(f"/{P}/cheatsheet", self.cheatsheet, ["GET"], "核心小抄")
        reg(f"/{P}/chatlog", self.chatlog, ["GET"], "对话素材")
        reg(f"/{P}/events", self.events, ["GET"], "生活事件流")
        reg(f"/{P}/pending", self.pending, ["GET"], "排期队列")
        reg(f"/{P}/pending/cancel", self.pending_cancel, ["POST"], "取消排期")
        reg(f"/{P}/export", self.export, ["GET"], "导出档案与记忆包")
        logger.info("[AstrLover] 面板 Web API 已注册。")

    # ------------------------------------------------------------------
    async def overview(self):
        app = self.app
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        album = await app.album.stats()
        photos = await app.photos.stats()
        data = {
            "ready": app.ready,
            "booted": app.booted,
            "linked_umo": app.state_target,
            "album": album,
            "photos": photos,
            "moments": await app.moments.count(),
            "unanswered": int(await app.dao.kv_get("unanswered", 0) or 0),
            "last_user_minutes": int((time.time() - last_user) / 60) if last_user else None,
            "vision_ok": app.vision.ready(),
            "vector_ok": app.vectors.available,
            "imagegen_ok": bool(app.imagegen and app.imagegen.available),
            "tts_ok": bool(app.voice and app.voice.tts_ready),
            "channel_ok": bool(app.moments.channel()),
        }
        if app.ready:
            data.update({
                "now": app.clock.describe_now(await app.records.milestones()),
                "activity": await app.life.current_activity(),
                "sleeping": await app.life.sleeping_now(),
                "mood": await app.mood.prompt_text(),
                "stage": await app.records.get_state("stage"),
                "signature": await app.records.get_state("signature"),
                "avatar_desc": await app.records.get_state("avatar"),
                "appearance": await app.records.get_state("appearance"),
                "schedule": await app.dao.day_schedule(app.clock.today_str()),
            })
        return json_response(data)

    # ------------------------------------------------------------------
    async def records_list(self):
        kind = request.query.get("kind", "f")
        limit = request.query.get("limit", 50, type=int)
        return json_response({"text": await self.app.records.listing(kind, limit)})

    async def records_mutate(self):
        payload = await request.json(default={})
        op = str(payload.get("op") or "")
        if op == "add":
            out = await self.app.records.add(str(payload.get("kind") or ""), str(payload.get("text") or ""))
        elif op == "edit":
            out = await self.app.records.edit(str(payload.get("rid") or ""), str(payload.get("text") or ""))
        elif op == "del":
            out = await self.app.records.delete(str(payload.get("rid") or ""))
        elif op == "state":
            out = await self.app.records.set_state_cmd(str(payload.get("key") or ""), str(payload.get("text") or ""))
        else:
            return error_response("op 必须是 add/edit/del/state")
        return json_response({"message": out})

    # ------------------------------------------------------------------
    async def diaries(self):
        rows = await self.app.dao.recent_diaries(
            request.query.get("limit", 14, type=int), request.query.get("type", "daily")
        )
        return json_response({"items": rows})

    async def facts(self):
        rows = await self.app.dao.list_facts(
            subject=request.query.get("subject") or None, limit=300
        )
        return json_response({"items": rows})

    async def cheatsheet(self):
        return json_response({"item": await self.app.dao.latest_cheatsheet()})

    async def chatlog(self):
        rows = await self.app.dao.recent_chat(request.query.get("limit", 100, type=int))
        return json_response({"items": rows})

    async def events(self):
        rows = await self.app.dao.recent_events(request.query.get("limit", 50, type=int))
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

    async def export(self):
        from ..store.export import export_all

        path = await export_all(self.app, include_gallery=False)
        return file_response(path, filename=path.name, content_type="application/zip")
