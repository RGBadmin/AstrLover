"""设置：能边看结果边调的东西，全部在插件 UI 里改。

AstrBot 的插件配置页只留「接线」——恋人 id、控制台 token 与管理员、
两个 Provider id、生命层总开关。那几项装机时设一次，之后再不用碰。

其余全部在这里定义，存进数据库，由面板「设置」页编辑：改完即刻生效，
不用重载插件，也不用在两个地方找同一个开关。
"""

from dataclasses import dataclass, field

from .album.prompts import SEARCH_PROMPT
from .imagegen.prompts import IMAGE_PROMPT, STYLE_TAIL


@dataclass(frozen=True)
class S:
    key: str
    group: str
    label: str
    type: str                       # string / text / int / bool / list
    default: object
    hint: str = ""
    options: list = field(default_factory=list)


SPEC: tuple[S, ...] = (
    # ---- 视觉解析 ----
    S(key='vision_api_format', group='视觉解析', label='接口格式', type='string', default='openai', hint='中转站基本都选 openai，除非它明确说转发原生格式。选错会报「字段不认识」。', options=['openai', 'anthropic', 'gemini']),
    S(key='vision_base_url', group='视觉解析', label='API 地址', type='string', default='', hint='填到根即可，插件按格式补路径。'),
    S(key='vision_api_key', group='视觉解析', label='API Key', type='string', default='', hint='按接口格式自动选鉴权头。'),
    S(key='vision_model', group='视觉解析', label='模型 ID', type='string', default='', hint='必须支持图片输入。gemini 直接写模型名，不要写成 models/xxx。'),
    S(key='vision_stream', group='视觉解析', label='流式接收', type='bool', default=False, hint='只在网关对长响应返回 504 或挂断时才需要开。'),
    S(key='vision_context_window', group='视觉解析', label='模型上下文窗口', type='int', default=128000, hint='只用来校验下面那项是否合理，不发给 API。'),
    S(key='vision_max_tokens', group='视觉解析', label='最大输出长度', type='int', default=8192, hint='详细提示词建议 ≥8192。Gemini 的思考 token 与正文共用这个额度。'),
    S(key='vision_extra_body', group='视觉解析', label='附加请求参数', type='text', default='', hint='一段 JSON，合并进请求体顶层。留空则不加。'),
    S(key='gemini_safety', group='视觉解析', label='Gemini 安全阈值', type='string', default='OFF', hint='成人向图库必须设，否则 HTTP 200 空回。优先 OFF，老模型报 400 时退回 BLOCK_NONE。', options=['BLOCK_NONE', 'OFF', '默认']),
    S(key='gemini_thinking_budget', group='视觉解析', label='Gemini 思考预算', type='string', default='512', hint='建议填 512。跑成人向图库时这一项直接决定通过率。'),
    S(key='vision_system_prompt', group='视觉解析', label='系统提示词', type='text', default='', hint='正经提示词填这里（system 位）。留空用内置的。'),
    S(key='vision_prompt', group='视觉解析', label='解析提示词', type='text', default='', hint='user 位。跑成人向图库时这里只放一句「描述这张图片。」'),
    S(key='vision_end_mark', group='视觉解析', label='结束标记', type='string', default='', hint='让模型在末尾写个固定符号，用来判断有没有写完。留空不启用。'),
    S(key='vision_max_chars', group='视觉解析', label='描述最长字数', type='int', default=600, hint='存档时按此截断。用详细提示词建议 3000 以上，否则标签行会被砍掉。'),
    S(key='subject_name', group='视觉解析', label='主体角色名', type='string', default='', hint='把描述里的「一名女性」这类泛称换成她的名字。留空不替换。'),
    S(key='vision_min_chars', group='视觉解析', label='描述最少字数', type='int', default=0, hint='短于此值按敷衍处理并重试。0 = 不检查。'),
    S(key='vision_concurrency', group='视觉解析', label='并发数', type='int', default=2, hint='批量索引时可提到 10~20；开太高中转商会排队反而更慢。'),
    S(key='vision_retries', group='视觉解析', label='上游故障重试次数', type='int', default=4, hint='限流、超时、5xx 这类。重试用尽不算图片自己的失败。'),
    S(key='vision_block_retries', group='视觉解析', label='生成被拦重试次数', type='int', default=2, hint='finishReason 那类。带采样随机性，重试常常能过。'),
    S(key='vision_hard_retries', group='视觉解析', label='输入判死重试次数', type='int', default=1, hint='blockReason 那类。是对图本身的判定，重发基本无效。'),
    # ---- 相册 ----
    S(key='light_api_format', group='轻量模型', label='接口格式', type='string', default='openai', hint='openai / anthropic / gemini。', options=['openai', 'anthropic', 'gemini']),
    S(key='light_base_url', group='轻量模型', label='接口地址', type='string', default='', hint='填到 /v1 即可，插件自动补路径；整条带 /chat/completions 的也认。'),
    S(key='light_api_key', group='轻量模型', label='API Key', type='string', default='', hint=''),
    S(key='light_model', group='轻量模型', label='模型', type='string', default='', hint='记忆沉淀等杂活用，挑便宜的。留空则用会话当前模型。'),
    S(key='light_max_tokens', group='轻量模型', label='最大输出', type='int', default=1024, hint=''),
    S(key='light_timeout', group='轻量模型', label='超时（秒）', type='int', default=60, hint=''),
    S(key='embed_api_format', group='向量模型', label='接口格式', type='string', default='openai', hint='openai 或 gemini。', options=['openai', 'gemini']),
    S(key='embed_base_url', group='向量模型', label='接口地址', type='string', default='', hint='填到 /v1 即可，插件自动补 /embeddings；整条粘过来也认。'),
    S(key='embed_api_key', group='向量模型', label='API Key', type='string', default='', hint=''),
    S(key='embed_model', group='向量模型', label='模型', type='string', default='', hint='如 text-embedding-3-small / gemini-embedding-001。'),
    S(key='embed_dimensions', group='向量模型', label='维度', type='int', default=0, hint='0 = 用模型默认。改了要重建全部向量。'),
    S(key='embed_batch', group='向量模型', label='批量条数', type='int', default=32, hint='撞限流就调小到 8~16。'),
    S(key='embed_timeout', group='向量模型', label='超时（秒）', type='int', default=60, hint=''),
    S(key='gallery_dir', group='相册', label='相册目录', type='string', default='', hint='容器内绝对路径，递归扫描。留空则禁用相册。'),
    S(key='use_snowflake_time', group='相册', label='从文件名还原拍摄时间', type='bool', default=True, hint='推特文件名就是推文 ID，比文件 mtime 准得多。'),
    S(key='season_prefer_now', group='相册', label='优先当季的图', type='bool', default=True, hint='她没指定季节时生效。'),
    S(key='gallery_search_prompt', group='相册', label='检索造词提示词', type='text', default=SEARCH_PROMPT, hint='want_photo 用它把「想发照片」翻成检索词。用词要跟视觉解析提示词对齐。输出格式由代码追加，改不坏。'),
    S(key='gallery_top_k', group='相册', label='返回候选数', type='int', default=10, hint='最终交给她挑的张数。'),
    S(key='gallery_fetch_k', group='相册', label='语义召回数', type='int', default=60, hint='纯计算，加大不花钱。'),
    S(key='sent_recent_days', group='相册', label='「最近发过」窗口（天）', type='int', default=30, hint='影响「上次那张」与「换一张」的判定。'),
    # ---- 图片记忆 ----
    S(key='describe_images', group='图片记忆', label='让她给图片写一句描述', type='bool', default=True, hint='跟正文一次生成，不额外调模型。'),
    S(key='max_context_images', group='图片记忆', label='上下文保留几张真图', type='int', default=0, hint='超出的折叠成文字占位。⚠️ 折叠不可逆，先确认描述链路跑通再开。0 = 不折叠。'),
    # ---- 频道动态 ----
    S(key='channel_id', group='频道动态', label='频道 ID', type='string', default='', hint='@用户名 或 -100 开头的数字。bot 需为频道管理员。留空则禁用动态。'),
    S(key='post_cooldown_minutes', group='频道动态', label='发动态冷却（分钟）', type='int', default=180),
    S(key='post_daily_limit', group='频道动态', label='每天最多发几条', type='int', default=5),
    S(key='inject_history', group='频道动态', label='历史动态注入上下文', type='bool', default=True, hint='按时间戳插进对话时间线，她才记得自己发过什么。'),
    S(key='inject_history_limit', group='频道动态', label='最多注入几条', type='int', default=0, hint='0 = 全部。'),
    # ---- 头像与签名 ----
    S(key='avatar_dir', group='头像与签名', label='头像候选目录', type='string', default='', hint='只读 jpg/jpeg，png 会被忽略。子文件夹名即分类。'),
    S(key='avatar_cooldown_minutes', group='头像与签名', label='换头像冷却（分钟）', type='int', default=720, hint='Telegram 未公布频率上限，别设成 0。'),
    S(key='signature_cooldown_minutes', group='头像与签名', label='改签名冷却（分钟）', type='int', default=360, hint='同上。'),
    # ---- 导演控制台 ----
    S(key='director_context_turns', group='导演控制台', label='生成时带多少轮历史', type='int', default=40, hint='/act 和主动消息用。'),
    S(key='console_proxy', group='导演控制台', label='代理地址', type='string', default='', hint='如 http://127.0.0.1:7890。留空直连。'),
    S(key='stamp_own_messages', group='导演控制台', label='给她的消息打时间戳', type='bool', default=True, hint='写进对话历史时加 [MM-DD HH:MM]。'),
    # ---- 主动消息 ----
    S(key='life_proactive', group='主动消息', label='启用主动消息', type='bool', default=True, hint='由作息、纪念日、想炫耀、想你了驱动。'),
    S(key='life_proactive_min_gap_minutes', group='主动消息', label='最小间隔（分钟）', type='int', default=45, hint='你最后一条消息之后至少隔这么久。'),
    S(key='life_max_silence_hours', group='主动消息', label='最长沉默（小时）', type='int', default=30, hint='超过这么久没聊，她一定会来找你。'),
    S(key='proactive_max_unanswered', group='主动消息', label='连发几条没回就停', type='int', default=3, hint='你一回复就清零。'),
    S(key='proactive_quiet', group='主动消息', label='静默时段', type='string', default='23:30-08:30', hint='如 23:30-08:30，这段时间不打扰。留空则不设。'),
    # ---- 生命模拟 ----
    S(key='life_timezone', group='生命模拟', label='她所在时区', type='string', default='Asia/Shanghai'),
    S(key='life_heartbeat_minutes', group='生命模拟', label='心跳间隔（分钟）', type='int', default=5, hint='推进生活与记忆维护，纯代码不耗 token。'),
    # ---- 生图 ----
    S(key='ig_prompt', group='生图', label='生图提示词模板', type='text', default=IMAGE_PROMPT, hint='先判断拍什么/画幅/她入不入镜，再按摄影语言写总视图+九宫格。输出格式由代码追加。'),
    S(key='ig_main_type', group='生图', label='主用供应商', type='string', default='api', hint='api：任意生图接口，协议看地址。comfyui：自建/云 ComfyUI。novelai：NovelAI 官方。', options=['api', 'comfyui', 'novelai']),
    S(key='ig_main_url', group='生图', label='主用地址', type='string', default='', hint='写完整端点，写什么就发什么。api 三选一：…/v1/chat/completions（openai）、…/v1beta/models/模型:generateContent（gemini）、…/v1/images/generations（grok）。comfyui 填服务根地址；novelai 留空用官方。'),
    S(key='ig_main_key', group='生图', label='主用 API Key', type='string', default='', hint=''),
    S(key='ig_main_model', group='生图', label='主用模型', type='string', default='', hint='如 gemini-3.1-flash-image / nai-diffusion-4-5-full。comfyui 用不到。'),
    S(key='ig_backup_type', group='生图', label='备用供应商', type='string', default='novelai', hint='主用失败才走它。地址或 Key 留空即不启用。', options=['api', 'comfyui', 'novelai']),
    S(key='ig_backup_url', group='生图', label='备用地址', type='string', default='', hint=''),
    S(key='ig_backup_key', group='生图', label='备用 API Key', type='string', default='', hint=''),
    S(key='ig_backup_model', group='生图', label='备用模型', type='string', default='', hint=''),
    S(key='ig_style', group='生图', label='画面质感附加词', type='text', default=STYLE_TAIL, hint='她入镜时固定缀在提示词末尾，拉画风用。拍风景不加。留空则不加。'),
    S(key='ig_reference', group='生图', label='参考形象路径', type='string', default='', hint='她长什么样。填一张图或一个目录（目录取前 3 张）。她入镜时带上它走图生图，这是保证每次都是同一个人的主要手段；拍风景不带。留空则用数据目录下的 anchors/。'),
    S(key='ig_nai_img2img_strength', group='生图', label='NovelAI 图生图强度', type='string', default='0.6', hint='0~1，越大越放飞、越不像参考图。0.5~0.7 一般是保脸和跟提示词的平衡点。'),
    S(key='ig_api_image_size', group='生图', label='API 出图尺寸', type='string', default='1K', hint='仅 api 类型的 Gemini 系有效。1K≈0.5MB / 2K≈2.3MB / 4K≈6.5MB，直接影响计费和耗时。', options=['1K', '2K', '4K']),
    S(key='ig_comfy_workflow', group='生图', label='ComfyUI workflow 文件', type='string', default='comfyui_workflow.json', hint='API 格式 JSON，放数据目录；用 {POSITIVE}/{NEGATIVE}/{SEED}/{WIDTH}/{HEIGHT} 占位。'),
    S(key='ig_nai_steps', group='生图', label='NovelAI 生成步数', type='int', default=24, hint='越多越细也越慢越贵，28 以上收益很小。范围 1~50。'),
)

DEFAULTS = {s.key: s.default for s in SPEC}
GROUPS = tuple(dict.fromkeys(s.group for s in SPEC))
BY_KEY = {s.key: s for s in SPEC}


class Settings:
    """接线取自 AstrBot 配置页，其余取自数据库（缺省回落到 SPEC 默认值）。

    get() 是同步的——启动时把数据库里的覆盖值整个读进内存，保存时一并更新，
    这样各处调用点不必改成 async。
    """

    def __init__(self, wiring: dict):
        self._wiring = dict(wiring or {})
        self._values: dict = {}

    async def load(self, dao):
        self._values = await dao.kv_get("settings", {}) or {}

    async def save(self, dao, updates: dict) -> list[str]:
        """写入并返回实际改动的键。类型按 SPEC 规范化。"""
        changed = []
        for key, raw in (updates or {}).items():
            spec = BY_KEY.get(key)
            if spec is None:
                continue
            value = _coerce(raw, spec)
            if value == self.get(key):
                continue
            self._values[key] = value
            changed.append(key)
        if changed:
            await dao.kv_set("settings", self._values)
        return changed

    async def reset(self, dao, key: str) -> bool:
        """恢复默认值（把覆盖删掉）。"""
        if key in self._values:
            self._values.pop(key)
            await dao.kv_set("settings", self._values)
            return True
        return False

    def get(self, key, default=None):
        if key in self._wiring:          # 接线优先：AstrBot 配置页说了算
            return self._wiring[key]
        if key in self._values:
            return self._values[key]
        return DEFAULTS.get(key, default)

    def dump(self) -> list[dict]:
        """给面板渲染用：定义 + 当前值 + 是否被改过。"""
        return [
            {
                "key": s.key, "group": s.group, "label": s.label, "type": s.type,
                "hint": s.hint, "options": list(s.options),
                "default": s.default, "value": self.get(s.key),
                "modified": s.key in self._values,
            }
            for s in SPEC
        ]


def _coerce(raw, spec: S):
    if spec.type == "bool":
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "on", "yes", "是")
    if spec.type == "int":
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return spec.default
    if spec.type == "list":
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]
    return str(raw)
