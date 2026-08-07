"""生命参数：AstrBot 人格表达不了的那部分「她」。

她是谁、什么性格、怎么说话、有哪些朋友——全部交给 AstrBot 人格页，
单一来源、WebUI 可编辑可切换。这里只留代码要用的结构化字段：

  name / call_me      日记与小抄的生成模板要用，日程随机种子也要用
  birthday / met_on / anniversary   纪念日与「认识第 N 天」
  appearance          生图的外观锚（与动态层的演变合并）
  backstory           分条播种进事实层，聊到才召回，不常驻上下文
  routine             作息与活动池：睡眠判定、日程生成、早晚安窗口的地基
  stage               关系阶段初值，之后由周记复盘推进（存动态层）
"""

from pathlib import Path

import yaml


class ProfileError(Exception):
    pass


class LifeProfile:
    def __init__(self, data: dict):
        self.data = data or {}
        if not self.name:
            raise ProfileError("生命参数缺少 name")

    @classmethod
    def load(cls, path: Path) -> "LifeProfile":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    # ---- 身份最小集 ----
    @property
    def name(self) -> str:
        return str(self.data.get("name", "")).strip()

    @property
    def call_me(self) -> str:
        return str(self.data.get("call_me", "") or "亲爱的").strip()

    @property
    def birthday(self) -> str:
        return str(self.data.get("birthday", "")).strip()

    @property
    def met_on(self) -> str:
        return str(self.data.get("met_on", "")).strip()

    @property
    def anniversary(self) -> str:
        return str(self.data.get("anniversary", "")).strip()

    @property
    def stage(self) -> str:
        return str(self.data.get("stage", "") or "热恋").strip()

    # ---- 结构化区块 ----
    @property
    def appearance(self) -> dict:
        return self.data.get("appearance") or {}

    @property
    def backstory(self) -> list[str]:
        return [str(x) for x in (self.data.get("backstory") or [])]

    @property
    def routine(self) -> dict:
        return self.data.get("routine") or {}

    # ---- 生图用：外观基准 + 动态演变 ----
    def appearance_text(self, dynamic_state: dict | None = None) -> str:
        a = self.appearance
        dyn = dynamic_state or {}
        parts = []
        if a.get("face"):
            parts.append(f"长相：{a['face']}")
        if a.get("body"):
            parts.append(f"身材：{a['body']}")
        if hair := (dyn.get("hair") or a.get("hair", "")):
            parts.append(f"发型：{hair}")
        if a.get("style"):
            parts.append(f"穿衣风格：{a['style']}")
        for extra in dyn.get("extras", []):
            parts.append(str(extra))
        return "；".join(parts)

    # ---- 迁移：认得旧版 profile.yaml 的嵌套结构 ----
    @classmethod
    def from_legacy(cls, data: dict) -> dict:
        """把旧版生命档案里仍然需要的字段抽出来，人设文字丢弃（交给人格页）。"""
        identity = data.get("identity") or {}
        rel = data.get("relationship") or {}
        out = {
            "name": identity.get("name", ""),
            "call_me": rel.get("call_me", ""),
            "birthday": identity.get("birthday", ""),
            "met_on": rel.get("met_on", ""),
            "anniversary": rel.get("anniversary", ""),
            "stage": rel.get("stage", "热恋"),
            "appearance": data.get("appearance") or {},
            "backstory": data.get("backstory") or [],
            "routine": data.get("routine") or {},
        }
        return {k: v for k, v in out.items() if v}
