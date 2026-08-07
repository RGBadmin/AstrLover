"""生活冲动：发动态、换头像、改签名——情境性触发，不是定时任务。

纯代码掷签（冷却 + 清醒时段 + 特别日子加成），内容由她自己想
（走导演桥生成，与控制台留空时同一条路）。落地后进入生活事件流，
成为她的认知与可炫耀的话题。
"""

import random

from astrbot.api import logger


class Impulses:
    def __init__(self, app):
        self.app = app

    async def maybe_fire(self):
        app = self.app
        if app.life and await app.life.sleeping_now():
            return
        if not app.state_target:
            return  # 没绑定会话，借不到她的身份
        special = bool(
            app.clock.festivals_on(app.clock.today())
            or app.clock.upcoming_specials(await app.records.milestones(), 0)
        )
        client = app.bridge.platform_client(app.state_target)

        # 频道动态：期望约 1 条/天；特别日子加成。冷却与每日上限由 limits 兜底
        if app.moments.channel() and random.random() < (0.010 + (0.03 if special else 0.0)):
            await self._post(client)

        # 头像：天级低频
        if random.random() < (0.0015 + (0.008 if special else 0.0)):
            result = await app.face.change_avatar(client)
            logger.info(f"[AstrLover] 生活冲动·换头像：{result[:60]}")

        # 签名
        if random.random() < 0.0025:
            await self._signature(client)

    async def _post(self, client):
        app = self.app
        try:
            text = await app.bridge.generate(
                "你现在想发一条动态到自己的频道。写出正文即可，像发朋友圈那样，"
                "短一点、有你的味道，和你此刻的生活或心情呼应。只输出正文。",
                instruct="",
            )
        except Exception as e:
            logger.debug(f"[AstrLover] 动态构思失败：{e}")
            return
        # 自主行为受冷却与每日上限约束
        result = await app.moments.post(client, text, quiet=random.random() < 0.5)
        logger.info(f"[AstrLover] 生活冲动·发动态：{result[:60]}")

    async def _signature(self, client):
        app = self.app
        try:
            text = await app.bridge.generate(
                "你想换一句资料页签名（120 字以内），和你最近的心情呼应。只输出签名本身。",
                instruct="",
            )
        except Exception as e:
            logger.debug(f"[AstrLover] 签名构思失败：{e}")
            return
        result = await app.face.update_signature(client, text)
        logger.info(f"[AstrLover] 生活冲动·改签名：{result[:60]}")
