"""主动消息的成文与投递：意愿过了阈值，才轮到模型开口。

主动消息与被动回复共用同一套人格提示与投递器（D4：一条执行通道），
所以"她主动找你"和"她回你消息"是同一个她。
"""

import time

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ..chat.composer import parse_reply
from ..chat.delivery import Deliverer
from .desire import reason_cn

_PROACTIVE_NOTE = (
    "【此刻的特殊情况】现在他没有给你发消息——是你自己想找他了。你的理由：{reasons}。\n"
    "主动开口要像你这个人：一两条短消息就好，说的话要跟你此刻的生活和心情接得上；"
    "如果理由是想分享你做的某件事，就自然地讲出来（记得 <told> 标记）。"
    "可以只是文字，也可以按你的心情用语音、照片或表情包。"
    "不要用「好久没聊了」这种机械开场，除非真的隔了很久而且你确实委屈了。\n"
    "（对话记录末尾括号里的引导语是你自己的内心活动，不是他发的消息。）"
)


class Planner:
    def __init__(self, app):
        self.app = app

    async def proactive_message(self, reasons: list[str]) -> bool:
        app = self.app
        umo = await app.owner_umo()
        if not umo:
            return False

        readable = "；".join(reason_cn(r) for r in reasons)
        system_prompt = await app.build_master_prompt(
            query_text=readable,
            extra_note=_PROACTIVE_NOTE.format(reasons=readable),
        )
        contexts = await app.working.contexts()
        try:
            raw = await app.llm.chat(
                prompt="（你拿起手机，决定主动给他发消息。）",
                contexts=contexts,
                system_prompt=system_prompt,
            )
        except Exception as e:
            logger.warning(f"[AstrLover] 主动消息生成失败：{e}")
            return False

        parsed = parse_reply(raw, max_segments=3)
        if not parsed.segments:
            return False

        async def send(chain: MessageChain):
            await app.context.send_message(umo, chain)

        await Deliverer(app, send).deliver(parsed)

        # 计数与标记
        unanswered = (await app.dao.kv_get("proactive_unanswered", 0) or 0) + 1
        await app.dao.kv_set("proactive_unanswered", unanswered)
        for r in reasons:
            await app.desire.mark(r)
        for eid in parsed.told_events:
            await app.dao.set_event_mention(eid, "told")
        await app.dao.add_event(
            "proactive",
            f"主动给他发了消息：{parsed.plain_text()[:60]}",
            motivation=readable,
            meta={"ts": int(time.time())},
        )
        logger.info(f"[AstrLover] 她主动发了消息（{readable}）。")
        return True
