"""生活冲动（R2/A2）：发动态、换头像、改签名——情境性触发，不是定时任务。

触发是纯代码的低成本掷签（冷却 + 清醒时段 + 特别日子加成）；
执行走 presence 控制台同一条通道（run_console_line）：
  /moment（留空=她自己想发什么）、/avatar（随机挑）、/signature（她自己想）。
落地后记入生活事件流（A2 三要素），成为她的认知与可炫耀的话题。
"""

import random
import time

from astrbot.api import logger


class Impulses:
    def __init__(self, app):
        self.app = app

    async def maybe_fire(self):
        app = self.app
        star = app.star
        if app.life and app.life.sleeping_now():
            return
        if not (star.state.get("director_target") or "").strip():
            return  # 没绑定角色会话，借不到她的身份
        now = time.time()
        special = bool(
            app.clock.festivals_on(app.clock.today())
            or app.clock.upcoming_specials(app.dynamic.milestones, app.profile.birthday, 0)
        )

        # 频道动态：期望约 1 条/天；特别日子加成
        if str(star.conf.get("channel_id") or "").strip():
            last_post = await app.dao.kv_get("last_post_ts", 0) or 0
            if now - last_post > 16 * 3600 and random.random() < (0.010 + (0.03 if special else 0.0)):
                await self._fire("moment", "/moment", "post", "发了条动态")

        # 头像：天级低频，冷却 3 天
        last_avatar = await app.dao.kv_get("last_avatar_ts", 0) or 0
        if now - last_avatar > 3 * 86400 and random.random() < (0.0015 + (0.008 if special else 0.0)):
            await self._fire("avatar", "/avatar", "avatar", "换了头像")

        # 签名：冷却 2 天
        last_sign = await app.dao.kv_get("last_signature_ts", 0) or 0
        if now - last_sign > 2 * 86400 and random.random() < 0.0025:
            await self._fire("signature", "/signature", "signature", "改了签名")

    async def _fire(self, name: str, cmd: str, event_kind: str, desc_prefix: str):
        app = self.app
        try:
            replies = await app.star.run_console_line(cmd)
            summary = (replies[-1] if replies else "")[:80]
            await app.dao.kv_set(f"last_{'post' if name == 'moment' else name}_ts", int(time.time()))
            await app.dao.add_event(
                event_kind,
                f"{desc_prefix}：{summary}" if summary else desc_prefix,
                motivation="今天是特别的日子" if app.clock.festivals_on(app.clock.today()) else "心血来潮",
            )
            logger.info(f"[AstrLover] 生活冲动 {name} 已执行：{summary}")
        except Exception:
            logger.error(f"[AstrLover] 生活冲动 {name} 失败：", exc_info=True)
