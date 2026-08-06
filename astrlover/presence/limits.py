"""频控：冷却与每日上限。

只约束她的自主行为；你的手动指令随时能用、不消耗当天配额、不重置计时。
触发限制时返回给她一句能看懂的话（"距离上一条还差 47 分钟"），
她知道发不成也知道原因，就不会反复重试。
"""

import time


class Limits:
    def __init__(self, app):
        self.app = app

    async def cooldown_left(self, kind: str, minutes: int) -> int:
        """还差几分钟才到点；0 表示可以做了。"""
        if minutes <= 0:
            return 0
        last = await self.app.dao.kv_get(f"cooldown:{kind}", 0) or 0
        left = last + minutes * 60 - time.time()
        return max(0, int(left // 60) + (1 if left % 60 else 0))

    async def mark_done(self, kind: str):
        await self.app.dao.kv_set(f"cooldown:{kind}", int(time.time()))

    async def daily_left(self, kind: str, cap: int) -> int:
        """今天还能做几次；cap<=0 表示不限（返回一个正数）。"""
        if cap <= 0:
            return 999
        today = self.app.clock.today_str() if self.app.clock else time.strftime("%Y-%m-%d")
        used = await self.app.dao.kv_get(f"daily:{kind}:{today}", 0) or 0
        return max(0, cap - int(used))

    async def bump_daily(self, kind: str):
        today = self.app.clock.today_str() if self.app.clock else time.strftime("%Y-%m-%d")
        key = f"daily:{kind}:{today}"
        used = await self.app.dao.kv_get(key, 0) or 0
        await self.app.dao.kv_set(key, int(used) + 1)

    async def summary(self) -> list[str]:
        out = []
        for kind, label, minutes in (
            ("post", "动态", int(self.app.star_conf.get("post_cooldown_minutes", 180) or 0)),
            ("avatar", "头像", int(self.app.star_conf.get("avatar_cooldown_minutes", 720) or 0)),
            ("signature", "签名", int(self.app.star_conf.get("signature_cooldown_minutes", 240) or 0)),
        ):
            wait = await self.cooldown_left(kind, minutes)
            out.append(f"{label}：{'可以发' if not wait else f'还差 {wait} 分钟'}")
        cap = int(self.app.star_conf.get("post_daily_limit", 5) or 0)
        if cap > 0:
            out.append(f"今日动态余额：{await self.daily_left('post', cap)}/{cap}")
        return out
