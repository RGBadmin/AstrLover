"""AstrLover —— 拟真 AI 恋人插件入口。

职责只有三件事：
1. 生命周期：initialize() 装配 App，terminate() 优雅收尾；
2. 事件路由：按平台实例 id 把消息分流给 对话管线 / 上帝控制台 / 频道互动；
3. Web API 注册（面板后端）。

所有业务逻辑都在 astrlover/ 包内，本文件保持薄。
"""

import sys

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .astrlover.app import App

# 高于默认管线，低于内核会话控制（sys.maxsize 级），保证既能接管又不破坏交互会话
ROUTE_PRIORITY = 1_000_000


class AstrLover(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.app: App | None = None

    async def initialize(self):
        try:
            self.app = App(star=self, context=self.context, raw_config=self.config)
            await self.app.initialize()
            logger.info("[AstrLover] 初始化完成，她醒来了。")
        except Exception:
            logger.error("[AstrLover] 初始化失败：", exc_info=True)
            self.app = None

    async def terminate(self):
        if self.app is not None:
            await self.app.terminate()
            self.app = None
        logger.info("[AstrLover] 已停止。")

    # ------------------------------------------------------------------
    # 统一路由：主 bot 与上帝 bot 两个平台实例被本插件完全接管，
    # 其余平台实例的消息不做任何处理（不影响用户的其他 bot 用途）。
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=ROUTE_PRIORITY)
    async def route_all(self, event: AstrMessageEvent):
        app = self.app
        if app is None or not app.ready:
            return

        pid = event.get_platform_id()

        # ---- 上帝 bot：只认主人，其他人静默无视 ----
        if app.cfg.god_platform_id and pid == app.cfg.god_platform_id:
            event.stop_event()
            if app.is_owner(event):
                await app.god.on_message(event)
            return

        # ---- 主 bot ----
        if pid == app.cfg.main_platform_id:
            event.stop_event()  # 主 bot 的一切表现只能来自"她"，绝不落入默认管线
            try:
                if event.is_private_chat():
                    if app.is_owner(event):
                        await app.chat.on_owner_message(event)
                    else:
                        await app.chat.on_stranger_private(event)
                else:
                    # 群聊：只处理频道讨论组（评论区互动），其余群消息忽略
                    await app.channel_hub.on_group_message(event)
            except Exception:
                logger.error("[AstrLover] 消息处理异常：", exc_info=True)
            return
