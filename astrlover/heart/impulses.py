"""生活冲动（R2/A2）：换头像、改签名、发动态——情境性触发，不是定时任务。

触发是纯代码的低成本掷签（结合冷却、清醒时段、特别日子加成）；
内容成文才动用模型。所有行为落地后进入事件流（A2 三要素），
成为她的认知、日记素材与可炫耀的话题。
上帝编排（R7）与这里共用同一批执行方法——同一条执行通道。
"""

import random
import re
import time

from astrbot.api import logger

_PIC_RE = re.compile(r"<pic>(.*?)</pic>", re.I | re.S)

_POST_NOTE = (
    "【此刻的特殊情况】你想在自己的频道（相当于你的朋友圈）发一条动态。{topic_line}\n"
    "写出动态正文：像真人发朋友圈——可以是今天的生活、一点心情、一句歌词、对某件小事的碎碎念；"
    "口语、简短（一般不超过80字）、别太满、可以带表情符号、不用称呼任何人；甜蜜的暗示可以有，但不点名。\n"
    "如果这条动态配一张图会更好，就在正文后另起一行写 <pic>画面描述：场景/人物状态/氛围/构图</pic>；"
    "纯文字也完全可以。只输出动态内容（和可选的 pic 标记），不要解释。"
)

_SIGN_NOTE = (
    "【此刻的特殊情况】你想换一下自己资料页的签名。{hint_line}\n"
    "写一句新签名：短（20字以内）、有你的味道、和你最近的心情或生活呼应，可以带表情符号。"
    "只输出签名本身。"
)

_AVATAR_MOTIVE_NOTE = (
    "【此刻的特殊情况】你刚把头像换成了这张：{desc}。{hint_line}\n"
    "用一句话（20字内）说说你换它的真实原因（比如「换季了」「今天心情特别好」），"
    "口语化，只输出这一句。"
)


class Impulses:
    def __init__(self, app):
        self.app = app

    # ==================================================================
    # 心跳掷签（纯代码）
    # ==================================================================
    async def maybe_fire(self):
        app = self.app
        if app.life.sleeping_now():
            return
        now = time.time()
        special = bool(
            app.clock.festivals_on(app.clock.today())
            or app.clock.upcoming_specials(app.dynamic.milestones, app.profile.birthday, 0)
        )

        # 频道动态：无冷却期约 1 条/天的期望频率；特别日子加成
        if app.tgsvc and app.tgsvc.channel_chat() is not None:
            last_post = await app.dao.kv_get("last_post_ts", 0) or 0
            if now - last_post > 16 * 3600:
                p = 0.010 + (0.03 if special else 0.0)
                if random.random() < p:
                    await self.compose_and_post()

        # 头像：天级低频（R2），冷却 3 天
        last_avatar = await app.dao.kv_get("last_avatar_ts", 0) or 0
        if now - last_avatar > 3 * 86400:
            p = 0.0015 + (0.008 if special else 0.0)
            if random.random() < p:
                await self.change_avatar()

        # 签名：冷却 2 天
        last_sign = await app.dao.kv_get("last_signature_ts", 0) or 0
        if now - last_sign > 2 * 86400:
            if random.random() < 0.0025:
                await self.change_signature()

    # ==================================================================
    # 发动态（R2 频道）
    # ==================================================================
    async def compose_and_post(self, topic: str = "", forced_text: str = "") -> bool:
        app = self.app
        if app.tgsvc is None or app.tgsvc.channel_chat() is None:
            return False
        try:
            if forced_text:
                text, pic_desc = forced_text, ""
            else:
                topic_line = f"主题：{topic}。" if topic else ""
                system_prompt = await app.build_master_prompt(
                    query_text=topic or "发动态",
                    extra_note=_POST_NOTE.format(topic_line=topic_line),
                )
                raw = await app.llm.chat(
                    prompt="（你打开频道，想发条动态。）",
                    contexts=await app.working.contexts(),
                    system_prompt=system_prompt,
                )
                m = _PIC_RE.search(raw)
                pic_desc = m.group(1).strip() if m else ""
                text = _PIC_RE.sub("", raw).strip()
            if not text and not pic_desc:
                return False

            images: list[str] = []
            if pic_desc:
                path = await app.provide_picture(pic_desc)
                if path:
                    images.append(path)

            ids = await app.tgsvc.post_channel(text, images)
            if not ids:
                return False

            desc = f"发了条动态：「{text[:60]}」" + ("，配了一张图" if images else "")
            motive = topic or ("今天是特别的日子" if app.clock.festivals_on(app.clock.today()) else "有感而发")
            await app.dao.add_event(
                "post", desc, motivation=motive,
                meta={"message_ids": ids, "text": text, "pic": pic_desc},
            )
            await app.dao.kv_set("last_post_ts", int(time.time()))
            logger.info(f"[AstrLover] 她发了条动态：{text[:40]}")
            return True
        except Exception:
            logger.error("[AstrLover] 发动态失败：", exc_info=True)
            return False

    # ==================================================================
    # 换头像（R2）
    # ==================================================================
    async def change_avatar(self, hint: str = "") -> bool:
        app = self.app
        if app.tgsvc is None:
            return False
        try:
            query = hint or "适合当头像的自拍，状态好看的"
            picked = None
            if app.gallery:
                picked = await app.gallery.pick_for_avatar(query)
            if not picked:
                return False  # 没有可用图库时不硬换
            path, desc = picked

            if not await app.tgsvc.set_avatar(path):
                return False

            hint_line = f"参考：{hint}。" if hint else ""
            try:
                motive = await app.llm.light(
                    "换头像",
                    system_prompt=(await app.build_master_prompt(
                        query_text="换头像",
                        extra_note=_AVATAR_MOTIVE_NOTE.format(desc=desc, hint_line=hint_line),
                    )),
                )
                motive = motive.strip()[:40]
            except Exception:
                motive = "想换个新鲜的"

            app.dynamic.set_avatar_desc(desc)
            await app.dao.add_event(
                "avatar", f"换了头像：{desc}", motivation=motive, meta={"file": path}
            )
            await app.dao.kv_set("last_avatar_ts", int(time.time()))
            logger.info(f"[AstrLover] 她换了头像：{desc}（{motive}）")
            return True
        except Exception:
            logger.error("[AstrLover] 换头像失败：", exc_info=True)
            return False

    # ==================================================================
    # 改签名（R2）
    # ==================================================================
    async def change_signature(self, hint: str = "") -> bool:
        app = self.app
        if app.tgsvc is None:
            return False
        try:
            hint_line = f"参考：{hint}。" if hint else ""
            system_prompt = await app.build_master_prompt(
                query_text="换签名",
                extra_note=_SIGN_NOTE.format(hint_line=hint_line),
            )
            text = (await app.llm.chat(prompt="（你想换个签名。）", system_prompt=system_prompt)).strip()
            text = text.strip("「」\"' \n")[:30]
            if not text:
                return False
            if not await app.tgsvc.set_signature(text):
                return False
            app.dynamic.set_signature(text)
            await app.dao.add_event("signature", f"把签名改成了「{text}」", motivation=hint or "心情使然")
            await app.dao.kv_set("last_signature_ts", int(time.time()))
            logger.info(f"[AstrLover] 她把签名改成了：{text}")
            return True
        except Exception:
            logger.error("[AstrLover] 改签名失败：", exc_info=True)
            return False
