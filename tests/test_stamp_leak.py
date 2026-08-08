"""时间戳不能漏进发出去的话里。

导演桥给她自己的消息打 [MM-DD HH:MM] 存进历史，是为了插件能读出时间。
但模型下一轮读到的是同一份文本，就照着开头写一个——戳漏进了真正发给
他的消息；写回历史时还会再叠一层。
"""

import asyncio

from astrlover.markers import strip_stamp


def run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, text):
        self.completion_text = text


class _Ev:
    def __init__(self, sender="123"):
        self._sender = sender
        self.unified_msg_origin = "tg:FriendMessage:123"

    def get_sender_id(self):
        return self._sender


def test_strip_stamp_cases():
    cases = [
        ("[08-08 10:44] 消失两天的人突然冒出来叫我宝宝", "消失两天的人突然冒出来叫我宝宝"),
        ("[8-8 9:04] 补零不全也要剥", "补零不全也要剥"),
        ("[08-08 10:44] [08-08 10:44] 叠了两层", "叠了两层"),
        ("[08-08 10:44] 第一行\n[08-08 10:45] 第二行", "第一行\n第二行"),
        ("  [08-08 10:44]   前面有空格", "前面有空格"),
        # 不该动的
        ("今天 [08-08 10:44] 这个戳在句中", "今天 [08-08 10:44] 这个戳在句中"),
        ("[通知] 这不是时间戳", "[通知] 这不是时间戳"),
        ("[08-08] 只有日期不算", "[08-08] 只有日期不算"),
        ("没有戳", "没有戳"),
        ("", ""),
    ]
    for raw, want in cases:
        assert strip_stamp(raw) == want, f"{raw!r} -> {strip_stamp(raw)!r}，应为 {want!r}"


def test_reply_going_out_has_no_stamp(app_factory):
    """on_llm_response：她照着历史写的戳，发出去之前要摘掉。"""

    async def go():
        app = app_factory()
        await app.initialize()
        resp = _Resp("[08-08 10:44] 消失两天的人突然冒出来叫我宝宝")
        await app.on_llm_response(_Ev(), resp)
        assert resp.completion_text == "消失两天的人突然冒出来叫我宝宝"
        await app.terminate()

    run(go())


def test_history_write_does_not_double_stamp(app_factory):
    """写回历史时先剥再打，不能叠成两层戳。"""

    async def go():
        app = app_factory()
        await app.initialize()
        umo = "tg:FriendMessage:123"
        await app.bridge.append_assistant(umo, "[08-08 10:44] 她自己写的戳")

        cm = app.context.conversation_manager
        cid = await cm.get_curr_conversation_id(umo)
        conv = await cm.get_conversation(umo, cid)
        import json
        last = json.loads(conv.history)[-1]["content"]

        import re
        stamps = re.findall(r"\[\d{1,2}-\d{1,2} \d{1,2}:\d{2}\]", last)
        assert len(stamps) == 1, f"戳叠了 {len(stamps)} 层：{last!r}"
        assert last.endswith("她自己写的戳")
        await app.terminate()

    run(go())
