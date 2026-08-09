"""生图提示词构建：先想清楚拍什么，再按摄影语言写。

以前是硬拼的——`人物：{外观}；画面：{情境}；…同一位女生`，
不管要什么都先塞一个人进去，写「赤道无风带」出来的也是个女孩。
现在由轻量模型看着情境和最近对话，决定拍什么内容、什么画幅、她入不入镜，
再写成「总视图 + 九宫格」的拍摄稿。

她入镜时才把外观基准和锚点图接上——那两样是为了保证「同一个人」，
拍风景时带着只会污染画面。

模型不可用/输出不成形时退回一份直白的拼接，不让生图整条链断掉。
"""

from dataclasses import dataclass, field

from astrbot.api import logger

# NovelAI 的三个规格
SIZES = {
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "square": (1024, 1024),
}
DEFAULT_ORIENTATION = "portrait"

_NEGATIVE = (
    "lowres, bad anatomy, bad hands, extra fingers, deformed face, blurry, "
    "watermark, text, logo, jpeg artifacts"
)
# 她入镜时才加：拍风景时"different person"之类是无意义的负面词
_NEGATIVE_PERSON = "different person, inconsistent face, multiple people"

_JSON_CONTRACT = (
    "\n\n【输出】\n"
    '只输出这个 JSON：{"orientation": "portrait|landscape|square", '
    '"with_her": true 或 false, '
    '"overview": "总视图，一到两句摄影语言", '
    '"grid": {"左上": "...", "上中": "...", "右上": "...", '
    '"左中": "...", "正中": "...", "右中": "...", '
    '"左下": "...", "下中": "...", "右下": "..."}}'
)

_GRID_ORDER = ("左上", "上中", "右上", "左中", "正中", "右中", "左下", "下中", "右下")


@dataclass
class PromptSpec:
    positive: str
    negative: str
    width: int = 832
    height: int = 1216
    reference_images: list[str] = field(default_factory=list)  # 外观锚点图（一致性）
    situation: str = ""       # 原始情境，回流入库时作为打标素材
    orientation: str = DEFAULT_ORIENTATION
    with_her: bool = True


def _compose(overview: str, grid: dict, appearance: str, with_her: bool) -> str:
    lines = []
    if with_her and appearance:
        lines.append(f"人物：{appearance}")
    if overview:
        lines.append(f"总视图：{overview}")
    cells = [f"{k}：{grid.get(k, '').strip()}" for k in _GRID_ORDER if str(grid.get(k, "")).strip()]
    if cells:
        lines.append("九宫格：\n" + "\n".join(cells))
    return "\n".join(lines)


def fallback_spec(appearance: str, situation: str, anchors: list[str]) -> PromptSpec:
    """轻量模型用不了时的兜底：直白拼一版，至少还能出图。

    这里仍然默认她入镜——兜底是给"她想发张自拍"那条主路用的，
    风景那种本来就该走模型规划，规划不出来就该报错而不是硬画。
    """
    appearance = (appearance or "").strip()
    positive = "；".join(x for x in [
        f"人物：{appearance}" if appearance else "",
        f"画面：{situation}",
        "真实感照片，自然光影，手机随手拍的生活质感",
    ] if x)
    w, h = SIZES[DEFAULT_ORIENTATION]
    return PromptSpec(
        positive=positive,
        negative=_NEGATIVE + (", " + _NEGATIVE_PERSON if appearance else ""),
        width=w, height=h,
        reference_images=anchors[:2] if appearance else [],
        situation=situation,
        orientation=DEFAULT_ORIENTATION,
        with_her=bool(appearance),
    )


async def build_spec(app, situation: str, anchors: list[str]) -> PromptSpec:
    """情境 + 最近对话 → 拍摄稿。"""
    from ..memory import transcript

    parts = [f"情境：{situation}"]
    try:
        rows = await transcript.load(app)
        if convo := transcript.as_script(rows, limit=10, width=100):
            parts.append(f"最近对话：\n{convo}")
    except Exception as e:
        logger.debug(f"[AstrLover] 生图取对话失败，只按情境规划：{e}")

    template = str(app.conf.get("ig_prompt") or "")
    plan = None
    try:
        plan = await app.llm.light_json("\n\n".join(parts), system_prompt=template + _JSON_CONTRACT)
    except Exception as e:
        logger.warning(f"[AstrLover] 生图提示词规划失败，退回直白拼接：{e}")
    if not isinstance(plan, dict):
        return fallback_spec(await app.appearance_text(), situation, anchors)

    orientation = str(plan.get("orientation") or "").strip().lower()
    if orientation not in SIZES:
        orientation = DEFAULT_ORIENTATION
    with_her = bool(plan.get("with_her", True))
    grid = plan.get("grid") if isinstance(plan.get("grid"), dict) else {}
    overview = str(plan.get("overview") or "").strip()

    # 画面内容一个字都没规划出来时必须退回兜底。
    # 否则 positive 里只剩「人物：…」——情境整个丢了，出来的图跟要求毫无关系，
    # 而且因为字符串非空，光判断 positive 是不是空是查不出来的
    if not overview and not any(str(grid.get(k, "")).strip() for k in _GRID_ORDER):
        logger.warning(f"[AstrLover] 生图规划没给出画面内容，退回直白拼接：{situation[:40]}")
        return fallback_spec(await app.appearance_text(), situation, anchors)

    # 她入镜才去取外观——不入镜时连这次生成都不该问她长什么样
    appearance = (await app.appearance_text()).strip() if with_her else ""
    positive = _compose(overview, grid, appearance, with_her)

    w, h = SIZES[orientation]
    logger.info(f"[AstrLover] 生图规划：{orientation} {w}x{h}，"
                f"{'她入镜' if with_her else '不入镜'}｜{situation[:40]}")
    return PromptSpec(
        positive=positive,
        negative=_NEGATIVE + (", " + _NEGATIVE_PERSON if with_her else ""),
        width=w, height=h,
        reference_images=anchors[:2] if with_her else [],
        situation=situation,
        orientation=orientation,
        with_her=with_her,
    )
