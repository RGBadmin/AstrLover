"""导演 bot 传输层：插件自持的独立 Telegram bot（不经过 AstrBot 平台管理）。

- 由插件直接用 python-telegram-bot 注册与轮询，生命周期随插件启停；
- 只接受、只回复管理员（wiring.owner_id）一个人；其他任何人静默无视；
- 文本交给 DirectorConsole 解析执行，回复原路发回。
"""

from astrbot.api import logger

from .console import DirectorConsole

_TG_MSG_LIMIT = 4000  # Telegram 单条消息上限 4096，留余量


class DirectorBot:
    def __init__(self, app):
        self.app = app
        self.console = DirectorConsole(app)
        self.application = None

    @property
    def configured(self) -> bool:
        return bool(self.app.cfg.director_token)

    # ------------------------------------------------------------------
    async def start(self):
        if not self.configured:
            logger.info("[AstrLover] 未配置导演 bot token，导演控制台停用。")
            return
        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters

            builder = ApplicationBuilder().token(self.app.cfg.director_token)
            proxy = self.app.cfg.director_proxy
            if proxy:
                builder = builder.proxy(proxy).get_updates_proxy(proxy)
            self.application = builder.build()
            self.application.add_handler(MessageHandler(filters.ALL, self._on_update))

            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            me = await self.application.bot.get_me()
            logger.info(f"[AstrLover] 导演 bot 已上线：@{me.username}")
        except Exception:
            logger.error("[AstrLover] 导演 bot 启动失败：", exc_info=True)
            self.application = None

    async def stop(self):
        if self.application is None:
            return
        try:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
        except Exception:
            logger.warning("[AstrLover] 导演 bot 停止时出现异常。", exc_info=True)
        finally:
            self.application = None

    # ------------------------------------------------------------------
    async def _on_update(self, update, context):
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if str(user.id) != self.app.cfg.owner_id:
            return  # 只认管理员，其他人静默无视
        text = (message.text or message.caption or "").strip()
        if not text:
            return
        reply = await self.console.handle(text)
        if not reply:
            return
        try:
            for i in range(0, len(reply), _TG_MSG_LIMIT):
                await message.reply_text(reply[i:i + _TG_MSG_LIMIT])
        except Exception:
            logger.warning("[AstrLover] 导演 bot 回复失败。", exc_info=True)
