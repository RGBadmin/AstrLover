"""定时动作执行器（D7）：到期的排期由心跳送到这里重放。

排期的载荷就是一行控制台指令（/say /act /moment /avatar /signature …），
执行时原样走 presence 控制台的派发通道——与你手敲指令完全同一条路，
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
            star = self.app.star
            chat_id = str(payload.get("chat_id") or "").strip()
            uid = str(payload.get("uid") or "").strip()
            if chat_id and chat_id.lstrip("-").isdigit() and uid:
                await star._console_run(int(chat_id), uid, cmd)
            else:
                replies = await star.run_console_line(cmd)
                for r in replies:
                    logger.info(f"[AstrLover] 排期 #{row['id']} 回执：{r[:120]}")
            await self.app.dao.finish_action(row["id"], "done")
            logger.info(f"[AstrLover] 排期 #{row['id']} 已执行：{cmd[:60]}")
        except Exception:
            await self.app.dao.finish_action(row["id"], "failed")
            logger.error(f"[AstrLover] 排期 #{row['id']} 执行异常：", exc_info=True)
