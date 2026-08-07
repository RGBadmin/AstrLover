"""R6 生图提示词构建：外观基准（保证同一个人）+ 当前外观状态（A9）+ 情境需求。

产出与后端无关的 PromptSpec，各适配器自行格式化。
"""

from dataclasses import dataclass, field


@dataclass
class PromptSpec:
    positive: str
    negative: str
    reference_images: list[str] = field(default_factory=list)  # 外观锚点图（一致性）
    situation: str = ""  # 原始情境需求描述（回流入库时作为打标素材）


_NEGATIVE = (
    "lowres, bad anatomy, bad hands, extra fingers, deformed face, blurry, "
    "watermark, text, logo, different person, inconsistent face"
)

_QUALITY = "真实感照片，自然光影，手机随手拍的生活质感，同一位女生"


def build_spec(appearance: str, situation: str, anchors: list[str]) -> PromptSpec:
    """外观基准（记录，缺失时自动生成一次）+ 情境 → 提示词。"""
    appearance = (appearance or "").strip()
    positive = "；".join(
        x for x in [
            f"人物：{appearance}" if appearance else "",
            f"画面：{situation}",
            _QUALITY,
        ] if x
    )
    return PromptSpec(
        positive=positive,
        negative=_NEGATIVE,
        reference_images=anchors[:2],
        situation=situation,
    )
