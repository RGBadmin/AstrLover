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
        reg(f"/{P}/pending", self.pending, ["GET"], "待办队列")
        reg(f"/{P}/pending/cancel", self.pending_cancel, ["POST"], "取消待办")
        reg(f"/{P}/action", self.run_action, ["POST"], "行为编排")
        reg(f"/{P}/gallery/list", self.gallery_list, ["GET"], "图库列表")
        reg(f"/{P}/gallery/image/<image_id>", self.gallery_image, ["GET"], "图库原图")
        reg(f"/{P}/gallery/upload", self.gallery_upload, ["POST"], "上传图片")
        reg(f"/{P}/gallery/scan", self.gallery_scan, ["POST"], "扫描目录")
        reg(f"/{P}/gallery/tagall", self.gallery_tagall, ["POST"], "全量打标")
        reg(f"/{P}/gallery/update", self.gallery_update, ["POST"], "图片操作")
        reg(f"/{P}/export", self.export, ["GET"], "导出档案与记忆包")
        logger.info("[AstrLover] 面板 Web API 已注册。")

    # ------------------------------------------------------------------
    async def overview(self):
        app = self.app
        stats = await app.dao.gallery_stats()
        pending_tags = sum(v for k, v in stats.items() if k.endswith("/pending"))
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        return json_response({
            "ready": app.ready,
            "linked_umo": await app.linked_umo(),
            "name": app.profile.name if app.profile else "",
            "now": app.clock.describe_now(app.profile.met_on, app.profile.anniversary),
            "activity": await app.life.current_activity() if app.life else "",
            "sleeping": app.life.sleeping_now() if app.life else False,
            "mood": await app.mood.prompt_text() if app.mood else "",
            "stage": app.dynamic.stage(str(app.profile.relationship.get("stage", ""))),
            "signature": app.dynamic.signature,
            "avatar_desc": app.dynamic.avatar_desc,
            "capabilities": sorted(app.capabilities()),
            "unanswered": await app.dao.kv_get("proactive_unanswered", 0) or 0,
            "last_user_minutes": int((time.time() - last_user) / 60) if last_user else None,
            "gallery_stats": stats,
            "pending_tags": pending_tags,
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

    async def run_action(self):
        payload = await request.json(default={})
        kind = str(payload.get("kind") or "")
        if kind not in ("say", "voice", "post", "avatar", "signature"):
            return error_response("不支持的动作")
        body = payload.get("payload") or {}
        due_ts = payload.get("due_ts")
        if isinstance(due_ts, (int, float)) and due_ts > time.time():
            aid = await self.app.actions.schedule(kind, body, int(due_ts), source="director")
            return json_response({"scheduled": aid})
        ok = await self.app.actions.run(kind, body)
        return json_response({"ok": ok})

    # ------------------------------------------------------------------
    async def gallery_list(self):
        category = request.query.get("category") or None
        status = request.query.get("status") or None
        limit = request.query.get("limit", 30, type=int)
        offset = request.query.get("offset", 0, type=int)
        rows = await self.app.dao.list_images(category, status, min(limit, 60), offset)
        for row in rows:
            row["thumb"] = await asyncio.to_thread(self._thumb_b64, row)
        return json_response({"items": rows})

    async def export(self):
        from ..store.export import export_all

        include_gallery = request.query.get("gallery", 1, type=int) == 1
        path = await export_all(self.app, include_gallery=include_gallery)
        return file_response(path, filename=path.name, content_type="application/zip")

    def _thumb_b64(self, row: dict) -> str:
        """160px 缩略图 base64（磁盘缓存），供面板内联显示。"""
        import base64

        src = self.app.data_dir / row["file"]
        if not src.exists():
            return ""
        cache_dir = self.app.gallery_dir.parent / ".thumbs"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / f"{row['id']}.jpg"
        try:
            if not cache.exists() or cache.stat().st_mtime < src.stat().st_mtime:
                from PIL import Image

                with Image.open(src) as im:
                    im = im.convert("RGB")
                    im.thumbnail((160, 160))
                    im.save(cache, "JPEG", quality=70)
            return "data:image/jpeg;base64," + base64.b64encode(cache.read_bytes()).decode()
        except Exception:
            return ""

    async def gallery_image(self, image_id: str):
        if not image_id.isdigit():
            return error_response("bad id", status_code=400)
        row = await self.app.dao.get_image(int(image_id))
        if row is None:
            return error_response("not found", status_code=404)
        path = (self.app.data_dir / row["file"]).resolve()
        if not str(path).startswith(str(self.app.data_dir.resolve())) or not path.exists():
            return error_response("not found", status_code=404)
        return file_response(path, filename=path.name)

    async def gallery_upload(self):
        files = await request.files()
        upload = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return error_response("missing file")
        safe_name = f"{int(time.time())}_{upload.filename.replace('/', '_').replace(chr(92), '_')}"
        target_dir = self.app.gallery_dir / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        await upload.save(target)
        rel = str(target.relative_to(self.app.data_dir)).replace("\\", "/")
        image_id = await self.app.dao.add_image(rel, category="life", source="user", status="pending")
        return json_response({"id": image_id, "file": rel})

    async def gallery_scan(self):
        n = await self.app.gallery.ingest.scan_dir()
        return json_response({"added": n})

    async def gallery_tagall(self):
        if self._tagall_task and not self._tagall_task.done():
            return json_response({"running": True})
        self._tagall_task = asyncio.create_task(self._tagall())
        return json_response({"started": True})

    async def _tagall(self):
        try:
            n = await self.app.gallery.ingest.tag_all()
            await self.app.refresh_capabilities()
            logger.info(f"[AstrLover] 面板触发的全量打标完成：{n} 张。")
        except Exception:
            logger.error("[AstrLover] 全量打标异常：", exc_info=True)

    async def gallery_update(self):
        payload = await request.json(default={})
        image_id = payload.get("id")
        op = str(payload.get("op") or "")
        if not isinstance(image_id, int):
            return error_response("缺少 id")
        if op == "anchor":
            await self.app.dao.set_anchor(image_id, bool(payload.get("value")))
        elif op == "delete":
            row = await self.app.dao.get_image(image_id)
            if row:
                await self.app.vectors.delete_gallery_doc(row.get("vec_id", ""))
                await self.app.dao.delete_image(image_id)
        elif op == "retag":
            await self.app.dao.set_image_status(image_id, "pending")
        elif op == "category":
            row = await self.app.dao.get_image(image_id)
            if row:
                await self.app.dao.tag_image(
                    image_id, str(payload.get("value") or row["category"]),
                    row["desc"], row["tags"], row["appearance"], row["vec_id"],
                )
        else:
            return error_response("不支持的操作")
        return json_response({"ok": True})
