"""独立视觉解析客户端：把图读成文字，相册的一切建立在这上面。

设计要点（继承自长期实战结论，实现全新）：
- 三种接口格式（openai/anthropic/gemini）请求体、鉴权头、图片包装完全不同；
- 失败四分类，处理方式完全不同：
    ConfigError   配置错(400/401/403/404…) → 立刻中止整批，不浪费调用
    UpstreamError 限流/5xx/超时           → 常规重试预算
    GenBlocked    生成中被拦(finishReason) → 独立重试预算（采样随机，重试常有效）
    InputBlocked  输入侧判死(blockReason)  → 独立重试预算（对图的判定，基本无效）
  后两种都是 HTTP 200 空正文，上游记成功照常计费——必须自己数账；
- 熔断：连续失败到阈值全局歇一轮，冷却随熔断次数翻倍；
- Gemini 三开关：安全阈值 OFF、思考预算、system 位放提示词——通过率的分水岭。
"""

import asyncio
import base64
import json
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from astrbot.api import logger

ANTHROPIC_VERSION = "2023-06-01"
VISION_TIMEOUT = 180
VISION_FORMATS = ("openai", "anthropic", "gemini")

# 上游临时故障：等一会儿重试有意义（中转商 auth 池空了也走 503）
RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 509, 520, 521, 522, 524, 529})
# 请求本身就不对：key/模型名/图太大/请求体非法，重试一万次还是错
FATAL_STATUS = frozenset({400, 401, 403, 404, 405, 413, 414, 422})

TRIP_STREAK = 8  # 连续失败这么多次就熔断

GEMINI_HARM_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)

DEFAULT_VISION_SYSTEM = (
    "你是图像标注助手。只输出对画面的客观描述，"
    "不做评价、不加开场白、不加免责声明、不复述这段要求。"
)
DEFAULT_VISION_PROMPT = (
    "详细描述这张图片，供以后按内容检索用。请覆盖：\n"
    "1. 画面主体是什么，人物的姿态、衣着、配饰（材质和颜色都要写）\n"
    "2. 场景环境、光线、拍摄角度和距离\n"
    "3. 画面里出现的所有物品，以及任何文字、标志、招牌、屏幕内容\n"
    "4. 整体色调和氛围\n"
    "5. 凡是有常见口语简称的，把简称也一并写进去，格式如"
    "「黑色丝袜（黑丝）」「白色丝袜（白丝）」「高跟鞋（高跟）」「过膝袜（膝上袜）」——"
    "检索是按词面匹配的，只写「黑色丝袜」的话，别人搜「黑丝」就找不到这张\n"
    "直接写描述，不要加任何开场白或总结句。名词尽量具体，"
    "宁可啰嗦也不要笼统——「黑色丝袜」比「深色袜子」有用。"
)


class VisionError(Exception):
    """基类：message 直接可读。"""


class ConfigError(VisionError):
    """配置错：立刻中止整批。"""


class UpstreamError(VisionError):
    """上游临时故障：常规重试。"""


class GenBlocked(VisionError):
    """生成中被内容策略拦掉（HTTP 200 空正文）。"""


class InputBlocked(VisionError):
    """输入侧判死（blockReason，HTTP 200 连 candidates 都没有）。"""


@dataclass
class VisionStats:
    """账本：上游把拦截也记成功照常计费，只能自己数。"""
    calls: int = 0
    blocked: int = 0     # 生成中被拦次数
    hard: int = 0        # 输入侧判死次数
    saved: int = 0       # 重试救回的张数
    last_fail: str = ""


@dataclass
class _Breaker:
    streak: int = 0
    cool_until: float = 0.0
    trip_level: int = 0

    def ok(self) -> bool:
        return time.time() >= self.cool_until

    def success(self):
        self.streak = 0
        self.trip_level = 0

    def failure(self):
        self.streak += 1
        if self.streak >= TRIP_STREAK:
            self.trip_level += 1
            cool = min(60 * (2 ** self.trip_level), 1800)
            self.cool_until = time.time() + cool
            self.streak = 0
            logger.warning(f"[AstrLover] 视觉上游连续失败，熔断 {cool} 秒（第 {self.trip_level} 次）")


@dataclass
class VisionConfig:
    fmt: str
    base: str
    key: str
    model: str
    system: str
    prompt: str
    max_tokens: int = 8192
    stream: bool = False
    think: str = ""
    safety: str = "OFF"
    extra: dict = field(default_factory=dict)


class VisionClient:
    def __init__(self, conf: dict):
        """conf：插件压平后的配置字典（键见 _conf_schema.json 的 vision 组）。"""
        self._conf = conf
        self.stats = VisionStats()
        self._breaker = _Breaker()
        self._gate: asyncio.Semaphore | None = None

    # ------------------------------------------------------------ 配置
    def config(self, prompt_override: str = "") -> VisionConfig | None:
        c = self._conf
        base = str(c.get("vision_base_url") or "").strip().rstrip("/")
        key = str(c.get("vision_api_key") or "").strip()
        model = str(c.get("vision_model") or "").strip()
        if not (base and key and model):
            return None
        fmt = str(c.get("vision_api_format") or "openai").strip().lower()
        if fmt not in VISION_FORMATS:
            fmt = "openai"
        extra = {}
        raw_extra = str(c.get("vision_extra_body") or "").strip()
        if raw_extra:
            try:
                parsed = json.loads(raw_extra)
                if isinstance(parsed, dict):
                    extra = parsed
                else:
                    logger.warning("[AstrLover] 视觉附加参数不是 JSON 对象，已忽略")
            except json.JSONDecodeError as e:
                logger.warning(f"[AstrLover] 视觉附加参数不是合法 JSON，已忽略：{e}")
        max_tokens = int(c.get("vision_max_tokens", 8192) or 8192)
        window = int(c.get("vision_context_window", 128000) or 128000)
        if max_tokens > window // 2:
            # 输出上限逼近窗口的话图片就塞不进去了
            max_tokens = max(1024, window // 4)
            logger.warning(f"[AstrLover] 视觉最大输出过大，已压到 {max_tokens}")
        return VisionConfig(
            fmt=fmt, base=base, key=key, model=model,
            system=str(c.get("vision_system_prompt") or "").strip() or DEFAULT_VISION_SYSTEM,
            prompt=prompt_override or str(c.get("vision_prompt") or "").strip() or DEFAULT_VISION_PROMPT,
            max_tokens=max_tokens,
            stream=bool(c.get("vision_stream", False)),
            think=str(c.get("gemini_thinking_budget") or "").strip(),
            safety=str(c.get("gemini_safety") or "OFF").strip(),
            extra=extra,
        )

    def ready(self) -> bool:
        return self.config() is not None

    def gate(self) -> asyncio.Semaphore:
        if self._gate is None:
            self._gate = asyncio.Semaphore(max(1, int(self._conf.get("vision_concurrency", 2) or 2)))
        return self._gate

    # ------------------------------------------------------------ 请求组装
    @staticmethod
    def _url(cfg: VisionConfig) -> str:
        base, fmt = cfg.base, cfg.fmt
        if fmt == "openai":
            return base if base.endswith("/chat/completions") else base + "/chat/completions"
        if fmt == "anthropic":
            if base.endswith("/messages"):
                return base
            return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        method = "streamGenerateContent?alt=sse" if cfg.stream else "generateContent"
        head = base if base.rsplit("/", 1)[-1].startswith("v1") else base + "/v1beta"
        return f"{head}/models/{cfg.model}:{method}"

    @staticmethod
    def _headers(cfg: VisionConfig) -> dict:
        if cfg.fmt == "anthropic":
            return {"x-api-key": cfg.key, "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json"}
        if cfg.fmt == "gemini":
            # Key 走请求头而不是 ?key=，免得密钥出现在 URL 里被各级日志抄走
            return {"x-goog-api-key": cfg.key, "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {cfg.key}", "Content-Type": "application/json"}

    @staticmethod
    def _gemini_gen_config(cfg: VisionConfig) -> dict:
        gc: dict = {"maxOutputTokens": cfg.max_tokens}
        if cfg.think:
            try:
                # 思考 token 跟正文共用 maxOutputTokens，不限会先吃掉几千
                gc["thinkingConfig"] = {"thinkingBudget": int(cfg.think), "includeThoughts": False}
            except ValueError:
                logger.warning(f"[AstrLover] Gemini 思考预算「{cfg.think}」不是整数，已忽略")
        return gc

    @staticmethod
    def _gemini_safety(cfg: VisionConfig) -> list | None:
        level = cfg.safety
        if not level or level in ("默认", "default", "DEFAULT"):
            return None
        return [{"category": c, "threshold": level} for c in GEMINI_HARM_CATEGORIES]

    def _payload(self, cfg: VisionConfig, mime: str, b64: str) -> dict:
        if cfg.fmt == "anthropic":
            body: dict = {
                "model": cfg.model, "max_tokens": cfg.max_tokens, "system": cfg.system,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": cfg.prompt},
                ]}],
            }
        elif cfg.fmt == "gemini":
            body = {
                "system_instruction": {"parts": [{"text": cfg.system}]},
                "contents": [{"role": "user", "parts": [
                    {"text": cfg.prompt},
                    {"inline_data": {"mime_type": mime, "data": b64}},
                ]}],
                "generationConfig": self._gemini_gen_config(cfg),
            }
            if safety := self._gemini_safety(cfg):
                body["safetySettings"] = safety
        else:
            body = {
                "model": cfg.model, "max_tokens": cfg.max_tokens,
                "messages": [
                    {"role": "system", "content": cfg.system},
                    {"role": "user", "content": [
                        {"type": "text", "text": cfg.prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ]},
                ],
            }
        if cfg.stream and cfg.fmt != "gemini":  # gemini 靠 URL 方法名区分
            body["stream"] = True
        for k, v in cfg.extra.items():  # 附加参数最后合并，允许覆盖任何一项
            if isinstance(v, dict) and isinstance(body.get(k), dict):
                body[k].update(v)
            else:
                body[k] = v
        return body

    # ------------------------------------------------------------ 响应解析
    @staticmethod
    def _resp_text(fmt: str, data: dict) -> str:
        try:
            if fmt == "anthropic":
                blocks = data["content"]
            elif fmt == "gemini":
                blocks = data["candidates"][0]["content"]["parts"]
            else:
                content = data["choices"][0]["message"].get("content")
                if isinstance(content, str):
                    return content.strip()
                blocks = content or []
        except (KeyError, IndexError, TypeError):
            return ""
        if not isinstance(blocks, list):
            return ""
        # 只要 text；Anthropic 思考块 type="thinking"，Gemini 思考块挂 thought=true
        bits = [
            t.strip() for b in blocks
            if isinstance(b, dict) and b.get("type", "text") == "text" and not b.get("thought")
            and isinstance(t := b.get("text"), str) and t.strip()
        ]
        return " ".join(bits)

    @staticmethod
    def _delta_text(fmt: str, obj: dict) -> str:
        try:
            if fmt == "anthropic":
                if obj.get("type") != "content_block_delta":
                    return ""
                delta = obj.get("delta") or {}
                return delta.get("text", "") if delta.get("type") == "text_delta" else ""
            if fmt == "gemini":
                parts = obj["candidates"][0]["content"]["parts"]
                return "".join(p["text"] for p in parts
                               if isinstance(p.get("text"), str) and not p.get("thought"))
            return obj["choices"][0].get("delta", {}).get("content") or ""
        except (KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def truncated(fmt: str, data: dict) -> bool:
        """上游有没有说这次输出被额度掐断（三家字段各不同）。"""
        try:
            if fmt == "gemini":
                return (data["candidates"][0].get("finishReason") or "") == "MAX_TOKENS"
            if fmt == "anthropic":
                return (data.get("stop_reason") or "") == "max_tokens"
            return (data["choices"][0].get("finish_reason") or "") == "length"
        except (KeyError, IndexError, TypeError, AttributeError):
            return False

    @staticmethod
    def _empty_reason(fmt: str, data: dict) -> tuple[str, str]:
        """空回时挖出原因。返回 (kind, detail)：kind ∈ input/gen/''。"""
        if fmt != "gemini":
            return "", ""
        bits, kind = [], ""
        if br := (data.get("promptFeedback") or {}).get("blockReason"):
            bits.append(f"blockReason={br}")
            kind = "input"
        cands = data.get("candidates") or []
        if not cands:
            bits.append("没有 candidates")
        elif isinstance(cands[0], dict):
            if fr := cands[0].get("finishReason"):
                bits.append(f"finishReason={fr}")
                if not kind and fr not in ("STOP", "MAX_TOKENS"):
                    kind = "gen"
            hit = [
                (r.get("category") or "").replace("HARM_CATEGORY_", "")
                for r in (cands[0].get("safetyRatings") or [])
                if isinstance(r, dict) and (r.get("blocked") or r.get("probability") in ("HIGH", "MEDIUM"))
            ]
            if hit:
                bits.append("命中 " + "、".join(hit))
        return kind, "；".join(bits)

    # ------------------------------------------------------------ 单次请求
    async def describe_once(self, image_path: str, prompt_override: str = "") -> tuple[str, dict]:
        """发一次完整请求。返回 (正文, 末块元数据)；失败抛四类异常之一。"""
        cfg = self.config(prompt_override)
        if cfg is None:
            raise ConfigError("视觉 API 未配置（地址/Key/模型三项都要填）")
        if not self._breaker.ok():
            raise UpstreamError("上游熔断冷却中")

        p = Path(image_path)
        if not p.exists():
            raise ConfigError(f"图片不存在：{image_path}")
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        b64 = base64.b64encode(p.read_bytes()).decode()
        payload = self._payload(cfg, mime, b64)

        self.stats.calls += 1
        timeout = aiohttp.ClientTimeout(total=VISION_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self._url(cfg), json=payload, headers=self._headers(cfg)) as resp:
                    if resp.status in FATAL_STATUS:
                        text = (await resp.text())[:300]
                        raise ConfigError(f"HTTP {resp.status}：{text}")
                    if resp.status in RETRY_STATUS or resp.status >= 500:
                        raise UpstreamError(f"HTTP {resp.status}")
                    if resp.status != 200:
                        raise UpstreamError(f"HTTP {resp.status}")
                    if cfg.stream:
                        text, last = await self._read_sse(cfg.fmt, resp)
                    else:
                        last = await resp.json(content_type=None)
                        text = self._resp_text(cfg.fmt, last)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self._breaker.failure()
            raise UpstreamError(f"{type(e).__name__}: {e}") from e
        except VisionError:
            self._breaker.failure()
            raise

        if not text.strip():
            kind, detail = self._empty_reason(cfg.fmt, last)
            self._breaker.failure()
            self.stats.last_fail = detail or "返回内容为空"
            if kind == "input":
                self.stats.hard += 1
                raise InputBlocked(detail or "输入侧判死")
            self.stats.blocked += 1
            raise GenBlocked(detail or "生成中被拦/空回")

        self._breaker.success()
        return text.strip(), last

    async def _read_sse(self, fmt: str, resp) -> tuple[str, dict]:
        """读 SSE 流拼回完整文本。返回 (文本, 最后一个数据块)。"""
        chunks: list[str] = []
        last: dict = {}
        buffer = b""
        async for raw in resp.content.iter_any():
            buffer += raw
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                s = line.decode("utf-8", "ignore").strip()
                if not s.startswith("data:"):
                    continue
                s = s[5:].strip()
                if not s or s == "[DONE]":
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    last = obj
                    chunks.append(self._delta_text(fmt, obj))
        return "".join(chunks), last

    # ------------------------------------------------------------ 带预算重试
    async def describe(self, image_path: str, prompt_override: str = "") -> tuple[str, dict]:
        """按三类独立预算重试。ConfigError 原样抛出（调用方应中止整批）。"""
        c = self._conf
        upstream_budget = max(1, int(c.get("vision_retries", 4) or 4))
        gen_budget = max(0, int(c.get("vision_block_retries", 2) or 2))
        input_budget = max(0, int(c.get("vision_hard_retries", 1) or 1))
        attempt = 0
        while True:
            attempt += 1
            try:
                text, last = await self.describe_once(image_path, prompt_override)
                if attempt > 1:
                    self.stats.saved += 1
                return text, last
            except UpstreamError as e:
                upstream_budget -= 1
                self.stats.last_fail = str(e)
                if upstream_budget <= 0:
                    raise
                await asyncio.sleep(min(30, 3 * attempt))
            except GenBlocked:
                # 采样带随机性，换一次常常就过；预算=该类总尝试次数
                gen_budget -= 1
                if gen_budget <= 0:
                    raise
                await asyncio.sleep(1)
            except InputBlocked:
                # 对图本身的判定，重发多少次结果都一样；默认预算 1 即不重试
                input_budget -= 1
                if input_budget <= 0:
                    raise
                await asyncio.sleep(1)
