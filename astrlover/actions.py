"""排期执行器：到期的排期由心跳送到这里重放。

载荷就是一行控制台指令（/say /act /moment /photo /avatar /signature…），
执行时原样走控制台的派发通道——与你手敲指令完全同一条路，
回执发回你排期时所在的控制台会话。
"""

from astrbot.api import logger


class ActionExecutor:
    def __init__(self, app):
        self.app = app

    async def execute(self, row: dict):
        payload = row.get("payload") or {}
        cmd = str(payload.get("cmd") or "")
        try:
            if row["kind"] != "console_cmd" or not cmd:
                await self.app.dao.finish_action(row["id"], "failed")
                return
            bot = self.app.director_bot
            if bot is None:
                await self.app.dao.finish_action(row["id"], "failed")
                return
            chat_id = payload.get("chat_id")
            reply = await bot.console.handle(cmd, chat_id=chat_id)
            if chat_id is not None and reply:
                await bot.say(chat_id, f"⏰ 排期 #{row['id']} 已执行\n{reply}")
            await self.app.dao.finish_action(row["id"], "done")
            logger.info(f"[AstrLover] 排期 #{row['id']} 已执行：{cmd[:60]}")
        except Exception:
            await self.app.dao.finish_action(row["id"], "failed")
            logger.error(f"[AstrLover] 排期 #{row['id']} 执行异常：", exc_info=True)
