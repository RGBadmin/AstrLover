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
    # collage 这一串是必须的：描述里带方位词时，模型很容易理解成
    # "把这几块拼成一张"，出来就是九宫格拼图
    "collage, photo collage, grid, photo grid, split screen, multiple panels, "
    "multiple views, contact sheet, montage, borders, "
    "inset, picture in picture, close-up overlay, cropped body part, "
    "lowres, bad anatomy, bad hands, extra fingers, deformed face, blurry, "
    "watermark, text, logo, jpeg artifacts"
)
# 她入镜时才加：拍风景时"different person"之类是无意义的负面词
# 上一轮全身照腿被拉成细杆——比例失真也得压
_NEGATIVE_PERSON = ("different person, inconsistent face, multiple people, "
                    "elongated limbs, disproportionate body, stick thin legs, "
                    "fashion illustration proportions")

_JSON_CONTRACT = (
    "\n\n【输出】\n"
    '只输出这个 JSON：{"orientation": "portrait|landscape|square", '
    '"with_her": true 或 false, '
    '"overview": "总视图，一到两句摄影语言", '
    '"grid": {"左上": "...", "上中": "...", "右上": "...", '
    '"左中": "...", "正中": "...", "右中": "...", '
    '"左下": "...", "下中": "...", "右下": "..."}, '
    '"tags": "英文 danbooru 标签，逗号分隔"}'
)

# NovelAI 没有质量标签时会往草图/线稿漂——两张废图都是这个样子
_NAI_QUALITY = "best quality, amazing quality, very aesthetic, absurdres"
# NAI 同理：标签里出现多个场景词时容易排成分格
_NAI_SINGLE = "solo focus, single image"

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
    tags: str = ""            # 英文 danbooru 标签，只认标签的后端（NovelAI）用


def _clean_tags(raw: str, with_her: bool) -> str:
    """整理模型给的标签串：去空、去重、补主体、补质量标签。

    主体标签必须有——漏了 `no humans` 的话 NovelAI 一定给你画个人；
    质量标签也必须有，没有它会往草图/线稿漂。
    """
    seen: list[str] = []
    for t in raw.replace("，", ",").split(","):
        t = " ".join(t.split()).lower()
        if t and t not in seen:
            seen.append(t)
    subject = "1girl, solo" if with_her else "no humans"
    for t in reversed(subject.split(", ")):
        if t not in seen:
            seen.insert(0, t)
    for q in (_NAI_SINGLE.split(", ") + _NAI_QUALITY.split(", ")):
        if q not in seen:
            seen.append(q)
    return ", ".join(seen)


# 九个格子在正文里的说法。绝对不能写成「九宫格」加分行列表——
# 生图模型会照字面理解，真给你画一张九张照片拼起来的图。
# 九宫格是规划阶段的思考工具，不是发出去的格式。
_CELL_WORDS = {
    "左上": "左上角", "上中": "上方中间", "右上": "右上角",
    "左中": "画面左侧", "正中": "画面正中", "右中": "画面右侧",
    "左下": "左下角", "下中": "下方中间", "右下": "右下角",
}
# 只用正面表述。写「不要拼贴」等于把"拼贴"送进模型的注意力——
# 语言模型驱动的生图没有负向通道，提到就是提到。
_SINGLE_FRAME = "一张照片，整幅是同一个连续空间，一个主体贯穿全画面。"

# 提示词只该写"图上有什么"，不该写"打算怎么拍"——模型看不懂意图，只能瞎猜。
# 这些词一出现就说明规划模型漂回拍摄口吻了，记一条警告让人看得见。
_CAMERA_WORDS = (
    "视角", "机位", "俯拍", "仰拍", "平视", "俯看", "第一人称", "POV",
    "构图", "取景", "镜头", "焦段", "广角", "长焦", "光圈", "景深",
    "快门", "曝光", "呈现", "展现", "突出", "营造", "旨在", "力求",
)


def _camera_talk(text: str) -> list[str]:
    return [w for w in _CAMERA_WORDS if w in text]


def _compose(overview: str, grid: dict, appearance: str, with_her: bool) -> str:
    lines = [_SINGLE_FRAME]
    if with_her and appearance:
        lines.append(f"人物：{appearance}")
    if overview:
        lines.append(f"整体：{overview}")
    # 压成一行分号串：一旦分行成列表状，模型就当成分格布局了
    cells = [f"{_CELL_WORDS[k]}{grid.get(k, '').strip()}"
             for k in _GRID_ORDER if str(grid.get(k, "")).strip()]
    if cells:
        lines.append("画面：" + "；".join(cells) + "。")
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
        reference_images=list(anchors) if appearance else [],
        situation=situation,
        orientation=DEFAULT_ORIENTATION,
        with_her=bool(appearance),
        # 兜底时没法翻标签，至少把质量标签给上，免得 NAI 漂成线稿
        tags=("1girl, solo, " if appearance else "") + _NAI_QUALITY,
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
        logger.warning(f"[AstrLover] 生图规划没给出画面内容，退回直白拼接：{situation}")
        return fallback_spec(await app.appearance_text(), situation, anchors)

    # 她入镜才去取外观——不入镜时连这次生成都不该问她长什么样
    appearance = (await app.appearance_text()).strip() if with_her else ""
    positive = _compose(overview, grid, appearance, with_her)
    # 风格锚只在她入镜时加——拍风景配上"网红美腿"就毁了
    if with_her and (tail := str(app.conf.get("ig_style") or "").strip()):
        positive += "\n" + tail

    if strays := _camera_talk(positive):
        logger.warning(f"[AstrLover] 生图提示词里混进了拍摄用语 {strays}——"
                       "该写图上有什么，不是打算怎么拍。改「生图提示词模板」那一项。")

    tags = _clean_tags(str(plan.get("tags") or ""), with_her)
    w, h = SIZES[orientation]
    logger.info(f"[AstrLover] 生图规划：{orientation} {w}x{h}，"
                f"{'她入镜' if with_her else '不入镜'}｜{situation}")
    logger.debug(f"[AstrLover] 标签：{tags}")
    return PromptSpec(
        positive=positive,
        negative=_NEGATIVE + (", " + _NEGATIVE_PERSON if with_her else ""),
        width=w, height=h,
        # 带不带在这儿定（她入镜才带），带几张由 ImageGen.references 定
        reference_images=list(anchors) if with_her else [],
        situation=situation,
        orientation=orientation,
        with_her=with_her,
        tags=tags,
    )
