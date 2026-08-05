"""生命档案（静态基线）加载与取用。

档案是"她是谁"的只读基线（R1）；运行期演化一律写入 dynamic 层，
换档案不丢"我们的过去"。
"""

from pathlib import Path

import yaml


class ProfileError(Exception):
    pass


class Profile:
    def __init__(self, data: dict):
        self.data = data or {}
        if not self.identity.get("name"):
            raise ProfileError("生命档案缺少 identity.name")

    @classmethod
    def load(cls, path: Path) -> "Profile":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    # ---- 原始区块 ----
    @property
    def identity(self) -> dict:
        return self.data.get("identity") or {}

    @property
    def appearance(self) -> dict:
        return self.data.get("appearance") or {}

    @property
    def personality(self) -> dict:
        return self.data.get("personality") or {}

    @property
    def backstory(self) -> list[str]:
        return [str(x) for x in (self.data.get("backstory") or [])]

    @property
    def npcs(self) -> list[dict]:
        return [x for x in (self.data.get("social_circle") or []) if isinstance(x, dict)]

    @property
    def routine(self) -> dict:
        return self.data.get("routine") or {}

    @property
    def voice(self) -> dict:
        return self.data.get("voice") or {}

    @property
    def relationship(self) -> dict:
        return self.data.get("relationship") or {}

    # ---- 常用字段 ----
    @property
    def name(self) -> str:
        return str(self.identity.get("name", ""))

    @property
    def nickname(self) -> str:
        return str(self.identity.get("nickname") or self.name)

    @property
    def call_me(self) -> str:
        return str(self.relationship.get("call_me", "亲爱的"))

    @property
    def anniversary(self) -> str:
        return str(self.relationship.get("anniversary", ""))

    @property
    def met_on(self) -> str:
        return str(self.relationship.get("met_on", ""))

    @property
    def birthday(self) -> str:
        return str(self.identity.get("birthday", ""))

    # ---- 提示词区块 ----
    def identity_block(self) -> str:
        i = self.identity
        parts = [f"你是{i.get('name', '')}"]
        if i.get("nickname"):
            parts.append(f"（亲近的人叫你{i['nickname']}）")
        if i.get("age"):
            parts.append(f"，{i['age']}岁")
        if i.get("occupation"):
            parts.append(f"，{i['occupation']}")
        if i.get("city"):
            parts.append(f"，住在{i['city']}")
        if i.get("birthday"):
            parts.append(f"，生日{i['birthday']}")
        return "".join(parts) + "。"

    def personality_block(self) -> str:
        p = self.personality
        lines = []
        if p.get("traits"):
            lines.append(f"性格：{str(p['traits']).strip()}")
        if p.get("speaking_style"):
            lines.append(f"说话方式：{str(p['speaking_style']).strip()}")
        if p.get("catchphrases"):
            lines.append("口癖：" + "、".join(str(c) for c in p["catchphrases"]))
        if p.get("humor"):
            lines.append(f"幽默感：{p['humor']}")
        if p.get("likes"):
            lines.append("喜欢：" + "、".join(str(c) for c in p["likes"]))
        if p.get("dislikes"):
            lines.append("雷点：" + "、".join(str(c) for c in p["dislikes"]))
        return "\n".join(lines)

    def npc_block(self) -> str:
        if not self.npcs:
            return ""
        lines = ["你生活里的固定人物（名字与设定永远一致，不可改动）："]
        for npc in self.npcs:
            lines.append(f"- {npc.get('name', '?')}（{npc.get('relation', '')}）：{npc.get('persona', '')}")
        return "\n".join(lines)

    def appearance_text(self, dynamic_state: dict | None = None) -> str:
        """外观基准 + 动态演变合并后的当前外观描述（A9）。"""
        a = dict(self.appearance)
        dyn = dynamic_state or {}
        hair = dyn.get("hair") or a.get("hair", "")
        parts = []
        if a.get("face"):
            parts.append(f"长相：{a['face']}")
        if a.get("body"):
            parts.append(f"身材：{a['body']}")
        if hair:
            parts.append(f"发型：{hair}")
        if a.get("style"):
            parts.append(f"穿衣风格：{a['style']}")
        for extra in dyn.get("extras", []):
            parts.append(str(extra))
        return "；".join(parts)

    def relationship_block(self, stage: str, call_me: str | None = None) -> str:
        r = self.relationship
        lines = [
            f"对方是你的恋人，你叫他「{call_me or self.call_me}」，你们现在处于「{stage}」阶段。"
        ]
        if r.get("boundaries"):
            lines.append(f"相处边界：{str(r['boundaries']).strip()}")
        return "\n".join(lines)
