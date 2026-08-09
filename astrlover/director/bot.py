"""导演 bot：插件自持的独立 Telegram bot（PTB），只认管理员。

跟 AstrBot 的平台系统完全无关——不用在平台配置里多加通道，
也不会两个 bot 抢同一套 getUpdates 报 Conflict。
"""

import asyncio
import re

from astrbot.api import logger

from .console import MENU, DirectorConsole
from .keyboard import Callbacks, markup

_TG_LIMIT = 4000
_ACK_AFTER = 3      # 超过这么多秒还没跑完，就补一条「执行中」
# 成对且不跨行——落单的星号是正文自带的，不该吃掉
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")


class DirectorBot:
    def __init__(self, app):
        self.app = app
        self.console = DirectorConsole(app)
        self.application = None
        self.callbacks = Callbacks()   # 长指令 ↔ 短令牌（回调数据 64 字节上限）

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
            from telegram.ext import (
                ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters,
            )

            builder = ApplicationBuilder().token(str(self.app.conf.get("console_token")).strip())
            if proxy := str(self.app.conf.get("console_proxy") or "").strip():
                builder = builder.proxy(proxy).get_updates_proxy(proxy)
            self.application = builder.build()
            self.application.add_handler(CallbackQueryHandler(self._on_callback))
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

        # 先给个"收到了"的信号：typing 是原生的、零噪声，
        # 超过几秒还没完才补一条明说在跑什么——不然长指令发出去石沉大海，
        # 分不清是在跑还是挂了
        try:
            await self.application.bot.send_chat_action(msg.chat_id, "typing")
        except Exception:
            pass

        task = asyncio.create_task(self.console.handle(text, chat_id=msg.chat_id))
        try:
            reply = await asyncio.wait_for(asyncio.shield(task), timeout=_ACK_AFTER)
        except asyncio.TimeoutError:
            await self.say(msg.chat_id, f"⏳ 执行中：`{text}`")
            try:
                reply = await task
            except Exception as e:
                logger.error("[AstrLover] 控制台执行出错：", exc_info=True)
                reply = f"执行出错：{type(e).__name__}: {e}"
        except Exception as e:
            logger.error("[AstrLover] 控制台执行出错：", exc_info=True)
            reply = f"执行出错：{type(e).__name__}: {e}"
        if reply:
            await self.say(msg.chat_id, reply)

    @staticmethod
    def _html(text: str) -> str:
        """`xxx` → <code>，**xxx** → <b>，其余 HTML 转义。

        不走 Telegram 的 Markdown 模式：那边正文里任何落单的 * _ [ 都会让
        整条消息 400，而控制台回执里全是 UMO、路径、文件名，防不胜防。
        HTML 只要转义三个字符，可控得多。
        代码段里的 ** 不当加粗——命令行参数里带星号是常事。
        反引号落单时（数量为奇数）最后一段按普通文本处理，不吞内容。
        """
        parts = (text or "").split("`")
        closed = len(parts) % 2 == 1     # 成对时段数是奇数
        out = []
        for i, seg in enumerate(parts):
            esc = seg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if i % 2 == 1 and (closed or i < len(parts) - 1):
                out.append(f"<code>{esc}</code>")
            else:
                out.append(_BOLD.sub(r"<b>\1</b>", esc))
        # split 把分隔符吃掉了。反引号落单时它不该消失——那多半是正文自带的字符
        if not closed and len(out) >= 2:
            out[-1] = "`" + out[-1]
        return "".join(out)

    async def say(self, chat_id, text: str):
        """回消息。超长切段——切完再逐段转 HTML，每段各自闭合，
        否则一对反引号被切在两条消息里会各留半个标签。

        text 是 Reply 时最后一段挂上按钮：按钮只该出现一次，
        挂在最后一条消息上，正好在用户视线落点。
        """
        if self.application is None or not text:
            return
        chunks = [text[i:i + _TG_LIMIT] for i in range(0, len(text), _TG_LIMIT)]
        kb = markup(getattr(text, "buttons", None), self.callbacks)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id, text=self._html(chunk), parse_mode="HTML",
                    reply_markup=kb if last else None,
                )
            except Exception as e:
                logger.warning(f"[AstrLover] 控制台回复失败（改发纯文本重试）：{e}")
                try:
                    await self.application.bot.send_message(
                        chat_id=chat_id, text=chunk, reply_markup=kb if last else None
                    )
                except Exception as e2:
                    logger.warning(f"[AstrLover] 控制台回复彻底失败：{e2}")

    # ------------------------------------------------------------------
    async def _on_callback(self, update, _context):
        """按钮点击 = 替他把那行指令发出去，走同一条执行通道。"""
        q = update.callback_query
        if q is None:
            return
        try:
            await q.answer()      # 不应答的话按钮会一直转圈
        except Exception:
            pass
        if str(q.from_user.id) not in self.admins():
            return
        cmd = self.callbacks.decode(q.data or "")
        chat_id = q.message.chat_id if q.message else None
        if not cmd:
            # 令牌表是内存的，插件重载后旧消息上的按钮就失效了
            await self.say(chat_id, "这个按钮过期了（插件重载过），重发一次指令吧。")
            return
        await self.say(chat_id, f"▶️ `{cmd}`")
        try:
            reply = await self.console.handle(cmd, chat_id=chat_id)
        except Exception as e:
            logger.error("[AstrLover] 按钮执行出错：", exc_info=True)
            reply = f"执行出错：{type(e).__name__}: {e}"
        if reply:
            await self.say(chat_id, reply)
