"""发照片：把相册里的 g123 或聊过的 #3 发出去，并记账。

编号两种来源：
  g123  相册（album_images）——按画面内容检索得来
  #3    聊过的旧图（photo_archive）——之前出现在对话里的，随时能重发
"""

import re

from astrbot.api import logger
from astrbot.api.event import MessageChain

_ID_RE = re.compile(r"^\s*(?P<kind>[g#])?(?P<num>\d+)\s*$", re.I)


def parse_photo_id(raw: str) -> tuple[str, int] | None:
    """'g123' → ('album', 123)；'#3'/'3' → ('archive', 3)。"""
    m = _ID_RE.match(str(raw or ""))
    if not m:
        return None
    kind = (m.group("kind") or "#").lower()
    return ("album" if kind == "g" else "archive", int(m.group("num")))


class PhotoSender:
    def __init__(self, app):
        self.app = app

    async def resolve(self, photo_id: str):
        """返回 (kind, row, 绝对路径)；找不到返回 (kind, None, None)。"""
        app = self.app
        parsed = parse_photo_id(photo_id)
        if parsed is None:
            return "", None, None
        kind, num = parsed
        if kind == "album":
            row = await app.album.get(num)
            path = app.album.abs_path(row["path"]) if row else None
            return kind, row, path
        row = await app.photos.get(num)
        return kind, row, (app.photos.abs_path(row) if row else None)

    async def send(self, event, photo_id: str, caption: str = "") -> str:
        """发出去并记账。返回给她看的一句话。"""
        kind, row, path = await self.resolve(photo_id)
        if row is None:
            return f"没有 {photo_id} 这张照片。相册里的用 browse_gallery 找，聊过的旧图用 find_photo。"
        if path is None:
            return f"{photo_id} 的文件不在了（可能被移动或删除）。"

        chain = MessageChain()
        if caption:
            chain.message(caption)
        chain.file_image(str(path))
        try:
            await event.send(chain)
        except Exception as e:
            logger.warning(f"[AstrLover] 发照片失败：{e}")
            return f"照片没发出去：{e}"

        if kind == "album":
            await self.app.album.mark_sent(int(row["id"]))
            # 发出去的相册图也进聊天存档，之后能用 #N 重发、能被 find_photo 找到
            try:
                data = path.read_bytes()
                pid = await self.app.photos.register_bytes(data)
                if pid and row["desc"]:
                    cur = await self.app.photos.get(pid)
                    if cur and not cur["catalog"]:
                        await self.app.photos.set_catalog(pid, row["desc"][:200])
            except OSError:
                pass
        logger.info(f"[AstrLover] 已发送照片 {photo_id}")
        return "照片已经发出去了。照常继续说你的话，别把发照片当成一次汇报。"
