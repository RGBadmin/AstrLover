"""Web 管理面板后端（AstrBot Plugin Pages）。

所有请求都在 Dashboard JWT 鉴权之后——面板等同最高权限，绝不公开暴露。
重交互（相册索引、行为编排）在控制台 bot 里做；面板负责"看"与"编辑档案"。
"""

import time

from astrbot.api import logger
from astrbot.api.web import error_response, file_response, json_response, request

from ..records import Records
from ..settings import GROUPS

P = "astrlover"

class PanelApi:
    def __init__(self, app):
        self.app = app

    def register(self):
        reg = self.app.context.register_web_api
        reg(f"/{P}/overview", self.overview, ["GET"], "运行总览")
        reg(f"/{P}/records", self.records_list, ["GET"], "列出记录")
        reg(f"/{P}/records/kinds", self.records_kinds, ["GET"], "记录类型")
        reg(f"/{P}/records/mutate", self.records_mutate, ["POST"], "增删改记录")
        reg(f"/{P}/settings", self.settings_get, ["GET"], "读取设置")
        reg(f"/{P}/settings/save", self.settings_save, ["POST"], "保存设置")
        reg(f"/{P}/probe", self.probe, ["POST"], "就地测试（视觉/向量）")
        reg(f"/{P}/export", self.export, ["GET"], "导出记忆包")
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
    async def records_kinds(self):
        return json_response({"kinds": [{"key": k, "label": v} for k, v in Records.KINDS]})

    async def records_list(self):
        kind = request.query.get("kind", "f")
        limit = request.query.get("limit", 50, type=int)
        return json_response({"rows": await self.app.records.rows(kind, limit)})

    async def records_mutate(self):
        payload = await request.json(default={})
        return json_response({"message": await self.app.records.mutate(
            op=str(payload.get("op") or ""),
            rid=str(payload.get("rid") or ""),
            kind=str(payload.get("kind") or ""),
            text=str(payload.get("text") or ""),
        )})

    # ------------------------------------------------------------------
    async def settings_get(self):
        return json_response({"groups": list(GROUPS), "items": self.app.conf.dump()})

    async def settings_save(self):
        payload = await request.json(default={})
        if reset_key := str(payload.get("reset") or ""):
            ok = await self.app.conf.reset(self.app.dao, reset_key)
            return json_response({"message": "已恢复默认值" if ok else "本来就是默认值"})
        changed = await self.app.conf.save(self.app.dao, payload.get("values") or {})
        if not changed:
            return json_response({"message": "没有改动"})
        await self.app.on_settings_changed(changed)
        return json_response({"message": f"已保存 {len(changed)} 项，即时生效", "changed": changed})

    async def probe(self):
        """设置页上的「测一下」：改完当场验证，不用切到控制台。"""
        payload = await request.json(default={})
        what = str(payload.get("what") or "")
        if what == "vision":
            return json_response({"message": await self.app.vision_command("test")})
        if what == "embed":
            return json_response({"message": await self.app.album.embedder.probe()})
        return error_response("what 必须是 vision 或 embed")

    async def export(self):
        from ..store.export import export_all

        path = await export_all(self.app, include_gallery=False)
        return file_response(path, filename=path.name, content_type="application/zip")
