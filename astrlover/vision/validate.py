"""视觉输出校验：拿到正文不等于拿到了描述。

三种垃圾都是 HTTP 200 正常返回的，不拦就直接进库，还会跟着转成向量
把语义检索一起带偏：模型嘴上拒绝、思维链漏进正文、敷衍的短回复。
另有结束标记机制兜"被 max_tokens 悄悄截断"的场景（上游不给
finishReason 的中转网关才需要）。
"""

import re

# 拒答都很短；长描述里偶然出现某个词不该误判，用长度兜底
REFUSAL_MAX_CHARS = 400
REFUSAL_MARKS = (
    "我无法", "我不能", "无法满足", "无法提供", "无法生成", "无法描述",
    "不能生成", "不能提供", "不能处理", "不便描述", "很抱歉", "抱歉，",
    "我被设定", "作为一个ai", "作为 ai", "违反", "不适当", "不合适的请求",
    "i can't", "i cannot", "i won't", "i'm unable", "i am unable",
    "i'm sorry", "i am sorry", "as an ai", "unable to comply",
    "can't assist", "cannot assist", "against my",
)

# 思维链漏进正文的特征：中文提示词下正常描述绝不会是英文小标题开场
THINKING_MARKS = (
    "i'm now", "i am now", "i've ", "i'll ", "i need to", "let me ",
    "my primary task", "first, i", "i'm focusing", "i'm analyzing",
    "i'm grappling", "i'm considering", "i have re-assessed",
)

# 结束标记允许的形近点号（不含英文句点——正文里出现得太自然）
END_MARK_CHARS = "·・•‧∙⋅"


def cut_at_end_mark(text: str, mark: str) -> tuple[str, bool]:
    """按结束标记裁尾。返回 (正文, 模型有没有写完)。

    没配标记就一律当写完；标记是同一点号重复 N 次时，
    放行形近点号（模型未必挑得准同一个码位）。
    """
    mark = (mark or "").strip()
    if not mark:
        return text, True
    i = text.find(mark)
    if i < 0 and len(set(mark)) == 1 and mark[0] in END_MARK_CHARS:
        m = re.search(f"[{re.escape(END_MARK_CHARS)}]{{{len(mark)},}}", text)
        if m:
            i = m.start()
    return (text[:i].rstrip(), True) if i >= 0 else (text, False)


def junk_reason(text: str, min_chars: int = 0, max_chars: int = 600) -> str:
    """判断这段回复能不能当描述用。返回不能用的原因，能用则空串。"""
    t = (text or "").strip()
    if not t:
        return ""  # 空回有专门的处理路径（失败四分类），不在这儿判
    low = t.lower()

    head = low[:600]
    if re.match(r"^\*\*[a-z]", low) or any(p in head for p in THINKING_MARKS):
        return "像是思维链漏进了正文"

    if len(t) <= REFUSAL_MAX_CHARS:
        for p in REFUSAL_MARKS:
            if p in low:
                return f"像是拒答，命中「{p}」"

    if min_chars:
        # 下限不能高过截断上限，否则解析合格、存库后又被 clean 判脏
        floor = min(min_chars, max(100, max_chars))
        if len(t) < floor:
            return f"只有 {len(t)} 字，不到下限 {floor}"
    return ""
