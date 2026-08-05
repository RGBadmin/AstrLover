"""AstrLover —— 拟真 AI 恋人插件入口。

职责只有三件事：
1. 生命周期：initialize() 装配 App（含插件自持的导演 bot），terminate() 优雅收尾；
2. 事件路由：把"绑定对话"的消息交给对话管线，主 bot 其余消息按接管策略处理；
3. Web API 注册（面板后端，在 App 内完成）。

所有业务逻辑都在 astrlover/ 包内，本文件保持薄。
"""

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
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
    # 统一路由：
    # 1) 绑定对话（导演 bot /link 指定，可在任意平台）→ 对话管线，她全权接管；
    # 2) 主 bot 平台实例的其余消息 → 按接管策略处理（她是这个 bot 的唯一人格）；
    # 3) 其他平台实例的消息不做任何处理，不影响用户的其他 bot 用途。
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=ROUTE_PRIORITY)
    async def route_all(self, event: AstrMessageEvent):
        app = self.app
        if app is None or not app.ready:
            return

        try:
            umo = event.unified_msg_origin
            linked = await app.linked_umo()

            # ---- 绑定对话：她生活的地方 ----
            if linked and umo == linked:
                event.stop_event()
                await app.chat.on_partner_message(event)
                return

            # ---- 主 bot 平台实例：完全接管，绝不落入默认管线 ----
            if event.get_platform_id() == app.cfg.main_platform_id:
                event.stop_event()
                if event.is_private_chat():
                    if app.is_owner(event) and not linked:
                        # 尚未绑定任何对话：管理员私聊主 bot 时自动绑定到这里
                        await app.set_linked_umo(umo)
                        await app.chat.on_partner_message(event)
                    elif app.is_owner(event):
                        await self._notice_linked_elsewhere(event, linked)
                    else:
                        await app.chat.on_stranger_private(event)
                else:
                    # 群聊：只处理频道讨论组（评论区互动），其余群消息忽略
                    await app.channel_hub.on_group_message(event)
        except Exception:
            logger.error("[AstrLover] 消息处理异常：", exc_info=True)

    async def _notice_linked_elsewhere(self, event: AstrMessageEvent, linked: str):
        """管理员私聊主 bot，但她被绑定在别的对话里：每天提示一次。"""
        key = f"elsewhere_notice:{self.app.clock.today_str()}"
        if await self.app.dao.kv_get(key):
            return
        await self.app.dao.kv_set(key, 1)
        await event.send(
            MessageChain().message(
                f"（她现在生活在另一个对话里：{linked}。"
                "去导演 bot 用 /umo 查看、/link 切换回来。）"
            )
        )
