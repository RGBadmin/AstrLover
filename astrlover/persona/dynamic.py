"""动态层：系统演化出来的"她的状态"，与静态档案分离（R1 静动分离）。

存放：外观演变（A9）、关系阶段（周记复盘推进）、当前签名/头像描述、
关系里程碑（A12）。原子写入，进程崩溃不损档案。
"""

import os
import tempfile
from pathlib import Path

import yaml

_DEFAULT = {
    "appearance_state": {"hair": "", "extras": []},  # hair 为空 = 沿用档案基线
    "relationship_stage": "",                        # 为空 = 沿用档案基线
    "signature": "",
    "avatar_desc": "",
    "milestones": [],  # [{date: "YYYY-MM-DD", title: str, recurring: bool}]
}


class DynamicState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = dict(_DEFAULT)

    def load(self):
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            merged = dict(_DEFAULT)
            merged.update(loaded)
            self.data = merged
        else:
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---- 外观（A9：演变要经由事件发生）----
    @property
    def appearance_state(self) -> dict:
        return self.data.get("appearance_state") or {}

    def set_hair(self, hair: str):
        self.data.setdefault("appearance_state", {})["hair"] = hair
        self.save()

    def add_appearance_extra(self, note: str):
        self.data.setdefault("appearance_state", {}).setdefault("extras", []).append(note)
        self.save()

    # ---- 关系 ----
    def stage(self, fallback: str) -> str:
        return self.data.get("relationship_stage") or fallback

    def set_stage(self, stage: str):
        self.data["relationship_stage"] = stage
        self.save()

    # ---- 资料页 ----
    @property
    def signature(self) -> str:
        return self.data.get("signature", "")

    def set_signature(self, text: str):
        self.data["signature"] = text
        self.save()

    @property
    def avatar_desc(self) -> str:
        return self.data.get("avatar_desc", "")

    def set_avatar_desc(self, desc: str):
        self.data["avatar_desc"] = desc
        self.save()

    # ---- 里程碑（A12）----
    @property
    def milestones(self) -> list[dict]:
        return list(self.data.get("milestones") or [])

    def add_milestone(self, date: str, title: str, recurring: bool = True):
        ms = self.data.setdefault("milestones", [])
        if not any(m.get("date") == date and m.get("title") == title for m in ms):
            ms.append({"date": date, "title": title, "recurring": recurring})
            self.save()
