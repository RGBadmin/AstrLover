"""心跳：她的生命循环（D3）。

每个 tick 全部为纯代码：推进日程、衰减情绪、检查该做的事；
只有意愿过阈值 / 到点写日记这类真正的"决策与生成"才调用模型。
挂机一天的模型调用次数是可数的（成本意识）。
"""

import asyncio
import random

from astrbot.api import logger


class Heartbeat:
    def __init__(self, app):
        self.app = app
        self._task: asyncio.Task | None = None
        self._ticks = 0

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self):
        await asyncio.sleep(10)  # 等平台适配器就绪
        logger.info("[AstrLover] 心跳开始。")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("[AstrLover] 心跳异常：", exc_info=True)
            interval = self.app.cfg.heartbeat_minutes * 60
            await asyncio.sleep(interval * random.uniform(0.9, 1.1))

    async def tick(self):
        app = self.app

        # 1. 到期排期（控制台指令重放）——生命层关掉也要跑
        if app.actions:
            for row in await app.dao.due_actions():
                await app.actions.execute(row)

        if not app.ready:
            self._ticks += 1
            return

        # 2. 生活推进（纯代码，零 token）
        await app.life.ensure_today_plan()
        await app.life.advance()

        # 3. 记忆沉淀（对话空闲时）
        await app.memory.maybe_consolidate()

        # 4. 日记 / 周记
        if due := app.life.diary_due():
            await app.memory.write_daily_diary(due)
        now = app.clock.now()
        if now.weekday() == 6 and now.hour >= 21:
            await app.memory.write_weekly(app.clock.week_str())

        # 5. 主动消息（意愿驱动）
        if app.proactive:
            await app.proactive.tick()

        # 6. 生活冲动：发动态/换头像/改签名
        if app.impulses:
            await app.impulses.maybe_fire()

        self._ticks += 1
