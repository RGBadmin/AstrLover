"""工作记忆（A1 第一层）：最近的对话，始终在场。

底层是自管 chat_log 表；输出为 LLM contexts 格式。
"""

from ..store.dao import Dao

_KIND_PREFIX = {
    "voice": "（语音）",
    "photo": "（发了张照片）",
    "sticker": "（表情包）",
}


class WorkingMemory:
    def __init__(self, dao: Dao, max_msgs: int = 30, max_chars: int = 7000):
        self.dao = dao
        self.max_msgs = max_msgs
        self.max_chars = max_chars

    async def log_user(self, content: str, kind: str = "text", meta: dict | None = None):
        await self.dao.add_chat("user", content, kind, meta)

    async def log_her(self, content: str, kind: str = "text", meta: dict | None = None):
        await self.dao.add_chat("her", content, kind, meta)

    async def contexts(self) -> list[dict]:
        rows = await self.dao.recent_chat(self.max_msgs)
        out: list[dict] = []
        total = 0
        for row in rows:
            role = "user" if row["role"] == "user" else "assistant"
            prefix = _KIND_PREFIX.get(row["kind"], "")
            content = f"{prefix}{row['content']}"
            total += len(content)
            out.append({"role": role, "content": content})
        # 超预算时从最旧的开始丢
        while out and total > self.max_chars:
            dropped = out.pop(0)
            total -= len(dropped["content"])
        # contexts 首条必须是 user（部分 provider 严格校验）
        while out and out[0]["role"] != "user":
            total -= len(out[0]["content"])
            out.pop(0)
        return out
