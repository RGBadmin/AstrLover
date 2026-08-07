"""资料页：换头像、改签名，以及给消息打表情。

头像和签名是 **bot 全局设置**，不按用户区分——一段关系用一个独立 bot。
Telegram 没公布 setMy* 系列的频率上限，超限返回 429，所以默认给较长冷却。
头像只收 JPEG（png 会被忽略），且明确禁止用 file_id 复用，每次重新上传。
"""

import random
from pathlib import Path

from astrbot.api import logger

AVATAR_EXTS = {".jpg", ".jpeg"}
SIGNATURE_MAX = 120


class ProfileFace:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    def _avatar_root(self) -> Path | None:
        raw = str(self.app.star_conf.get("avatar_dir") or "").strip()
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_dir() else None

    def categories(self) -> list[str]:
        root = self._avatar_root()
        if root is None:
            return []
        return sorted(d.name for d in root.iterdir() if d.is_dir())

    def pick(self, category: str = "") -> Path | None:
        root = self._avatar_root()
        if root is None:
            return None
        base = root / category if category else root
        if not base.is_dir():
            base = root
        files = [p for p in base.rglob("*") if p.suffix.lower() in AVATAR_EXTS]
        return random.choice(files) if files else None

    # ------------------------------------------------------------------
    async def change_avatar(self, client, category: str = "", *, enforce_limits: bool = True) -> str:
        app = self.app
        if client is None:
            return "换头像失败：这个功能只能在 Telegram 上用。"
        if enforce_limits:
            if wait := await app.limits.cooldown_left(
                "avatar", int(app.star_conf.get("avatar_cooldown_minutes", 720) or 0)
            ):
                return f"刚换过头像，再等 {wait} 分钟吧。"
        path = self.pick(category)
        if path is None:
            cats = self.categories()
            hint = f"（可选分类：{'、'.join(cats)}）" if cats else "（头像目录里要放 jpg，png 不算）"
            return f"没有可换的头像{hint}"
        try:
            from telegram import InputProfilePhotoStatic

            with open(path, "rb") as f:
                await client.set_my_profile_photo(photo=InputProfilePhotoStatic(f.read()))
        except ImportError:
            return "换头像需要 python-telegram-bot ≥ 22.7，当前版本太低。"
        except Exception as e:
            logger.warning(f"[AstrLover] 换头像失败：{e}")
            return f"换头像失败了：{e}"

        if enforce_limits:
            await app.limits.mark_done("avatar")
        await app.dao.kv_set("avatar_file", str(path))
        await app.records.set_state("avatar", path.stem)
        await app.dao.add_event("avatar", f"换了头像：{path.name}", motivation="")
        logger.info(f"[AstrLover] 头像已换：{path.name}")
        return f"头像换好了（{path.name}）。可以顺口跟他说一声，也可以等他自己发现。"

    async def update_signature(self, client, text: str, *, enforce_limits: bool = True) -> str:
        app = self.app
        if client is None:
            return "改签名失败：这个功能只能在 Telegram 上用。"
        text = (text or "").strip()[:SIGNATURE_MAX]
        if not text:
            return "签名内容是空的。"
        if enforce_limits:
            if wait := await app.limits.cooldown_left(
                "signature", int(app.star_conf.get("signature_cooldown_minutes", 240) or 0)
            ):
                return f"刚改过签名，再等 {wait} 分钟吧。"
        try:
            await client.set_my_short_description(short_description=text)
        except Exception as e:
            logger.warning(f"[AstrLover] 改签名失败：{e}")
            return f"改签名失败了：{e}"

        if enforce_limits:
            await app.limits.mark_done("signature")
        await app.records.set_state("signature", text)
        await app.dao.add_event("signature", f"把签名改成了「{text}」", motivation="")
        logger.info(f"[AstrLover] 签名已改：{text}")
        return f"签名改成「{text}」了。它会覆盖上一句、没有历史记录——想记录某件事那是动态的活儿。"

    # ------------------------------------------------------------------
    async def react(self, event, emoji: str) -> str:
        """给他刚发的消息打个表情：不说话也让他知道你看到了。"""
        client = getattr(event, "client", None)
        if client is None:
            return "打表情失败：这个功能只能在 Telegram 上用。"
        msg = getattr(getattr(event, "message_obj", None), "message_id", None)
        chat = getattr(getattr(event, "message_obj", None), "group_id", None) or event.get_sender_id()
        if not msg:
            return "找不到要回应的那条消息。"
        try:
            from telegram import ReactionTypeEmoji

            await client.set_message_reaction(
                chat_id=int(chat), message_id=int(msg),
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 表情回应失败：{e}")
            return f"这个表情打不上去：{e}"
        return "已经给他那条消息打上表情了。"
