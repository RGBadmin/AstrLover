"""导演 bot：插件自持的独立 Telegram bot（PTB），只认管理员。

跟 AstrBot 的平台系统完全无关——不用在平台配置里多加通道，
也不会两个 bot 抢同一套 getUpdates 报 Conflict。
"""

from astrbot.api import logger

from .console import MENU, DirectorConsole

_TG_LIMIT = 4000


class DirectorBot:
    def __init__(self, app):
        self.app = app
        self.console = DirectorConsole(app)
        self.application = None

    @property
    def configured(self) -> bool:
        return bool(str(self.app.conf.get("console_token") or "").strip())

    def admins(self) -> list[str]:
        raw = self.app.conf.get("console_admins")
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [x.strip() for x in str(raw or "").replace("，", ",").split(",") if x.strip()]

    # ------------------------------------------------------------------
    async def start(self):
        if not self.configured:
            logger.info("[AstrLover] 未配置控制台 Token，导演 bot 停用。")
            return
        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters

            builder = ApplicationBuilder().token(str(self.app.conf["console_token"]).strip())
            if proxy := str(self.app.conf.get("console_proxy") or "").strip():
                builder = builder.proxy(proxy).get_updates_proxy(proxy)
            self.application = builder.build()
            self.application.add_handler(MessageHandler(filters.ALL, self._on_update))

            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            try:  # 注册指令菜单：输入 / 就有提示，不用记
                from telegram import BotCommand

                await self.application.bot.set_my_commands(
                    [BotCommand(k, d[:60]) for k, d in MENU]
                )
            except Exception as e:
                logger.debug(f"[AstrLover] 指令菜单注册失败：{e}")
            me = await self.application.bot.get_me()
            logger.info(
                f"[AstrLover] 导演 bot 已上线：@{me.username}，管理员 {len(self.admins())} 人"
            )
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
            logger.warning("[AstrLover] 导演 bot 停止异常。", exc_info=True)
        finally:
            self.application = None

    # ------------------------------------------------------------------
    async def _on_update(self, update, _context):
        msg = update.effective_message
        user = update.effective_user
        if msg is None or user is None:
            return
        admins = self.admins()
        if not admins:
            logger.warning("[AstrLover] 控制台没配管理员 ID，忽略所有消息")
            return
        if str(user.id) not in admins:
            return  # 只认管理员，其他人静默无视
        text = (msg.text or msg.caption or "").strip()
        if not text:
            return
        reply = await self.console.handle(text, chat_id=msg.chat_id)
        if reply:
            await self.say(msg.chat_id, reply)

    async def say(self, chat_id, text: str):
        if self.application is None or not text:
            return
        try:
            for i in range(0, len(text), _TG_LIMIT):
                await self.application.bot.send_message(chat_id=chat_id, text=text[i:i + _TG_LIMIT])
        except Exception as e:
            logger.warning(f"[AstrLover] 控制台回复失败：{e}")
