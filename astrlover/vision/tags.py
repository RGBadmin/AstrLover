"""分级/季节/标签行：相册的领域词汇与解析规则。

这里沉淀的是实战经验数据（六档分级不许跳级、季节不要求相邻、
标签行两代格式靠"第二段是否合法季节"区分、十字无标点段是模型字堆），
来源于 astrbot_plugin_tg_presence 的长期运行结论。
"""

import re

try:
    from .tag_schema import ALIAS, FIELDS, OWNER
except ImportError:  # 缺表时关掉标签校验，其余照常
    FIELDS, OWNER, ALIAS = [], {}, {}

# ---------------------------------------------------------------- 分级
# 尺度是一条由轻到重的光谱，六档。一张图可以同时占两档，但必须相邻——
# 「OOTD+性感」讲得通，「生活+露点」讲不通，那是判错了而不是跨度大。
# 不用 SFW/NSFW 命名：SFW 是 NSFW 的子串，词面检索会互相误捞。
RATING_TIER_ORDER = ("生活", "OOTD", "性感", "诱惑", "露点", "淫荡")
RATING_ALIAS = {"日常": "生活"}
# 库里只可能出现 11 个值：6 单档 + 5 相邻组合
RATING_VALUES = RATING_TIER_ORDER + tuple(
    f"{a}+{b}" for a, b in zip(RATING_TIER_ORDER, RATING_TIER_ORDER[1:])
)
RATING_TIERS = {v: set(v.split("+")) for v in RATING_VALUES}
RATING_RE = re.compile(
    "|".join(sorted(RATING_TIER_ORDER + tuple(RATING_ALIAS), key=len, reverse=True))
)
RATING_SEPS = "+＋/、,，&和"
# 人嘴里的说法 → 想要哪几档（只影响筛选意图，不影响关键词检索）
RATING_SYNONYMS = {
    "生活": ("生活",), "日常": ("生活",), "平时": ("生活",),
    "正常": ("生活",), "普通": ("生活",), "随手拍": ("生活",),
    "OOTD": ("OOTD",), "穿搭": ("OOTD",), "搭配": ("OOTD",),
    "今日穿搭": ("OOTD",), "试衣": ("OOTD",), "换装": ("OOTD",),
    "性感": ("性感",), "身材": ("性感", "诱惑"), "曲线": ("性感",),
    "诱惑": ("诱惑",), "勾人": ("性感", "诱惑"), "诱人": ("性感", "诱惑"),
    "勾引": ("性感", "诱惑"), "撩": ("性感", "诱惑"),
    "撩人": ("性感", "诱惑"), "挑逗": ("性感", "诱惑"),
    "露点": ("露点",), "淫荡": ("淫荡",),
    "骚": ("露点", "淫荡"), "好骚": ("露点", "淫荡"),
    "骚货": ("露点", "淫荡"), "母狗": ("露点", "淫荡"),
    "婊子": ("露点", "淫荡"), "浪货": ("露点", "淫荡"),
    "发骚": ("露点", "淫荡"), "下流": ("露点", "淫荡"),
    "重口": ("露点", "淫荡"), "露骨": ("露点", "淫荡"),
}

# ---------------------------------------------------------------- 季节
# 判据是画面（室外环境+衣着厚薄），不是拍摄日期；室内/特写标「四季」
SEASON_ORDER = ("春", "夏", "秋", "冬")
SEASON_ANY = "四季"
SEASON_ALIAS = {
    "四季皆可": SEASON_ANY, "通用": SEASON_ANY, "不明": SEASON_ANY,
    "看不出": SEASON_ANY, "无法判断": SEASON_ANY, "室内": SEASON_ANY,
    "春季": "春", "夏季": "夏", "秋季": "秋", "冬季": "冬",
    "初春": "春", "早春": "春", "暮春": "春",
    "初夏": "夏", "盛夏": "夏", "仲夏": "夏",
    "初秋": "秋", "深秋": "秋", "晚秋": "秋",
    "初冬": "冬", "深冬": "冬", "隆冬": "冬", "严冬": "冬",
}
SEASON_RE = re.compile(
    "|".join(sorted(SEASON_ORDER + (SEASON_ANY,) + tuple(SEASON_ALIAS), key=len, reverse=True))
)
SEASON_SYNONYMS = {
    "春": ("春",), "春天": ("春",), "开春": ("春",), "春装": ("春",),
    "夏": ("夏",), "夏天": ("夏",), "盛夏": ("夏",), "夏装": ("夏",),
    "热": ("夏",), "炎热": ("夏",), "大热天": ("夏",),
    "秋": ("秋",), "秋天": ("秋",), "秋装": ("秋",),
    "冬": ("冬",), "冬天": ("冬",), "冬装": ("冬",),
    "冷": ("冬",), "寒冷": ("冬",), "大冬天": ("冬",),
    "春秋": ("春", "秋"), "春秋装": ("春", "秋"),
    "换季": ("春", "秋"), "过渡": ("春", "秋"),
}
SEASON_OF_MONTH = {
    3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏",
    9: "秋", 10: "秋", 11: "秋", 12: "冬", 1: "冬", 2: "冬",
}

# ---------------------------------------------------------------- 标签行清洗
JUNK_SEG_MIN = 10  # 十个汉字以上且无标点/字母数字的段，几乎全是模型字堆
SUBJECT_ALIASES = (
    "一名女性主体", "单人女性主体", "画面女性主体", "女性主体",
    "一名女性", "该女性", "这名女性", "此女性", "画面主体",
    "一位女性", "女子", "该女子",
)

_TAG_SEP = "---"
_HAN_ONLY = re.compile(r"^[一-鿿]+$")


def _norm_rating(seg: str) -> str:
    """从标签行首段解析分级。别名归一、双档排序去重、跳级判无效。"""
    found: list[str] = []
    for m in RATING_RE.finditer(seg):
        tier = RATING_ALIAS.get(m.group(0), m.group(0))
        if tier not in found:
            found.append(tier)
    if not found:
        return ""
    if len(found) == 1:
        return found[0]
    a, b = sorted(found[:2], key=RATING_TIER_ORDER.index)
    if RATING_TIER_ORDER.index(b) - RATING_TIER_ORDER.index(a) == 1:
        return f"{a}+{b}"
    return a  # 跳级=判错，取较轻的那档兜底


def _norm_season(seg: str) -> str:
    """解析季节段。任意组合都收（春秋是真实类别），非法则空。"""
    found: list[str] = []
    for m in SEASON_RE.finditer(seg):
        s = SEASON_ALIAS.get(m.group(0), m.group(0))
        if s == SEASON_ANY:
            return SEASON_ANY
        if s not in found:
            found.append(s)
    return "".join(sorted(found, key=SEASON_ORDER.index)) if found else ""


def _looks_like_season(seg: str) -> bool:
    return bool(_norm_season(seg))


def find_tag_line(desc: str) -> str:
    """在描述里找标签行（分级---…），开头或末尾都认。"""
    lines = [ln.strip() for ln in (desc or "").splitlines() if ln.strip()]
    for ln in (lines[:2] + lines[-2:]) if lines else []:
        if _TAG_SEP in ln and RATING_RE.search(ln.split(_TAG_SEP, 1)[0]):
            return ln
    return ""


def parse_tag_line(desc: str) -> dict:
    """解析标签行 → {rating, season, keywords}。

    两代格式：新版 `分级---季节---水印---遮挡---关键词…`，
    旧版没有季节段。靠"第二段是否合法季节值"区分——旧版第二段是
    水印描述，与季节词不重合，不会被误读。
    """
    line = find_tag_line(desc)
    if not line:
        return {"rating": "", "season": "", "keywords": []}
    segs = [s.strip() for s in line.split(_TAG_SEP) if s.strip()]
    rating = _norm_rating(segs[0]) if segs else ""
    season, rest = "", segs[1:]
    if rest and _looks_like_season(rest[0]):
        season, rest = _norm_season(rest[0]), rest[1:]
    keywords: list[str] = []
    for seg in rest:
        for w in re.split(r"[，,、\s]+", seg):
            w = w.strip()
            if w and w not in keywords:
                keywords.append(w)
    return {"rating": rating, "season": season, "keywords": keywords}


def scrub_tag_line(desc: str, subject_name: str = "") -> str:
    """确定性清洗（/gallery polish 的核心）：
    - 删标签行里十字以上、无标点无字母数字的模型字堆段；
    - 把「一名女性」等泛称换成主体角色名（配置了才换）。
    """
    line = find_tag_line(desc)
    if line:
        segs = line.split(_TAG_SEP)
        kept = [
            s for s in segs
            if not (_HAN_ONLY.match(s.strip()) and len(s.strip()) >= JUNK_SEG_MIN)
        ]
        if len(kept) != len(segs):
            desc = desc.replace(line, _TAG_SEP.join(kept))
    if subject_name:
        for alias in SUBJECT_ALIASES:
            desc = desc.replace(alias, subject_name)
    return desc


def rating_wants(word: str) -> tuple[str, ...]:
    """口语说法 → 想要的档位集合；不认识返回空。"""
    return RATING_SYNONYMS.get(word.strip(), ())


def season_wants(word: str) -> tuple[str, ...]:
    return SEASON_SYNONYMS.get(word.strip(), ())


# 词典切不动的长片段退回二元滑窗；短的整体保留
GRAM_MIN_LEN = 4


# 标签候选值只有「淫水拉丝」「液体-淫水外溢」这种复合形态，裸词进不来——
# 可描述正文里写的就是裸词，人搜的时候打的也是裸词。这批词补上，
# 否则「淫水」会被退成二元滑窗切成「淫水」以外的碎片，命中率差一大截。
# 来源是视觉解析提示词末尾那份词表，跟正文用词同源。
_BODY_WORDS = """
鸡巴 龟头 肉棒 睾丸 蛋蛋 骚逼 阴户 小穴 阴唇 小阴唇 大阴唇 阴蒂 豆豆
阴道口 奶子 乳头 奶头 乳晕 乳房 乳沟 侧乳 下乳 屁股 屁眼 肛门 骚屁眼
阴毛 腰窝 翘臀 臀缝 腹部 肚脐 锁骨 腋下 大腿 小腿 脚背 脚趾 美甲
"""
_ACTION_WORDS = """
抽插 扒开 掰开 撑开 插入 自慰 口交 骑乘 后入 正常位 舔弄 吮吸
张开 夹紧 揉捏 抠弄 掐住 跪坐 弯腰 手撑 蹲着 仰躺 俯趴 侧躺
"""
_FLUID_WORDS = """
精液 淫水 骚水 白浆 口水 汗水 前列腺液 拉丝 外溢 内射 颜射
"""
_STATE_WORDS = """
勃起 半硬 湿了 水多 泛滥 张开 合拢 撑开 红肿 充血 挺立 凸起 潮红
勒肉 破洞 勾丝 半脱 全裸 露点
"""


# ---------------------------------------------------------------- 分词词典
def build_vocab() -> frozenset:
    """拿标签候选值凑中文分词词典：领域最常用词的集合，比通用分词库贴题。
    「无」「空」这类占位词剔掉——库里遍地都是，切出来纯噪声。"""
    stop = {"无", "空", "有", "没有", "未知", "其它", "其他", "无法判断", "不可见"}
    vocab: set[str] = set()

    def put(s: str) -> None:
        s = s.strip()
        if len(s) >= 2 and s not in stop:
            vocab.add(s)

    for _name, cand in FIELDS:
        for v in str(cand).split("|"):
            for one in v.replace("，", ",").split(","):
                one = one.strip()
                put(one)
                if "-" in one:  # 「室内-酒店」把两截也收进来
                    for piece in one.split("-"):
                        put(piece)
    for k in list(OWNER) + list(ALIAS):
        put(str(k))
    for group in (_BODY_WORDS, _ACTION_WORDS, _FLUID_WORDS, _STATE_WORDS):
        for one in group.split():
            put(one)
    return frozenset(vocab)


TAG_VOCAB = build_vocab()
