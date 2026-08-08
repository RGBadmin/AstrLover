"""自管的向量模型客户端。

不走 AstrBot 的 Embedding Provider——地址、Key、模型、维度全在插件自己的
设置里，报错也落在插件自己的日志和面板上。AstrBot 那边配没配、配成什么样，
跟这里无关。

鸭子类型对齐 FaissVecDB 要的四个方法（get_embedding / get_embeddings /
get_dim / get_embeddings_batch），不继承 AstrBot 的基类——继承就又接回去了。

两种接口格式：
  openai  POST {base}/embeddings            {"model","input":[...],"dimensions"?}
  gemini  POST {base}/v1beta/models/{model}:batchEmbedContents?key=…

维度不写死：第一次用时真发一次请求问出来（顺便验证配置对不对），
配了 embed_dimensions 就要求那个维度并核对返回是否一致。
"""

import asyncio

import aiohttp

from astrbot.api import logger

PROBE_TEXT = "astrlover embedding probe"


class EmbedError(Exception):
    """配置或上游出了问题，调用方降级为非语义检索。"""


class EmbedClient:
    def __init__(self, conf):
        self.conf = conf
        self._dim = 0
        self._sem: asyncio.Semaphore | None = None

    # ------------------------------------------------------------------ 配置
    @property
    def fmt(self) -> str:
        return str(self.conf.get("embed_api_format") or "openai").lower()

    @property
    def base_url(self) -> str:
        return str(self.conf.get("embed_base_url") or "").strip().rstrip("/")

    @property
    def api_key(self) -> str:
        return str(self.conf.get("embed_api_key") or "").strip()

    @property
    def model(self) -> str:
        return str(self.conf.get("embed_model") or "").strip()

    @property
    def want_dim(self) -> int:
        try:
            return max(0, int(self.conf.get("embed_dimensions", 0) or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def timeout(self) -> int:
        try:
            return max(5, int(self.conf.get("embed_timeout", 60) or 60))
        except (TypeError, ValueError):
            return 60

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def signature(self) -> str:
        """认得出"换了模型"的一串标识，用来判断向量库要不要重建。"""
        return f"{self.fmt}|{self.base_url}|{self.model}|{self._dim}"

    # ------------------------------------------------------------------ 传输
    def _gate(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(3)
        return self._sem

    def _url(self, batch: bool) -> str:
        """对粘贴进来的地址容错。

        地址这一栏最常见的错法是**连路径一起粘过来**——从视觉那栏抄来的
        `.../v1/chat/completions`，或者别处抄来的 `.../v1/embeddings`。
        硬拼就成了 `.../chat/completions/embeddings`，一个空正文的 404，
        看报错完全不知道发生了什么。所以这里跟视觉客户端一样先归一。
        """
        base = self.base_url
        if self.fmt == "gemini":
            if ":embedContent" in base or ":batchEmbedContents" in base:
                return base
            verb = "batchEmbedContents" if batch else "embedContent"
            head = base if base.rsplit("/", 1)[-1].startswith("v1") else base + "/v1beta"
            return f"{head}/models/{self.model}:{verb}"
        base = base.removesuffix("/chat/completions").rstrip("/")
        return base if base.endswith("/embeddings") else base + "/embeddings"

    def _headers(self) -> dict:
        if self.fmt == "gemini":
            return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _payload(self, texts: list[str]) -> dict:
        if self.fmt == "gemini":
            one = lambda t: {  # noqa: E731
                "model": f"models/{self.model}",
                "content": {"parts": [{"text": t}]},
                **({"outputDimensionality": self.want_dim} if self.want_dim else {}),
            }
            if len(texts) == 1:
                body = one(texts[0])
                body.pop("model", None)
                return body
            return {"requests": [one(t) for t in texts]}
        body = {"model": self.model, "input": texts}
        if self.want_dim:
            body["dimensions"] = self.want_dim
        return body

    @staticmethod
    def _unpack(fmt: str, data: dict, n: int) -> list[list[float]]:
        if fmt == "gemini":
            if "embeddings" in data:
                rows = [e.get("values") or [] for e in data["embeddings"]]
            else:
                rows = [(data.get("embedding") or {}).get("values") or []]
        else:
            items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
            rows = [x.get("embedding") or [] for x in items]
        if len(rows) != n or not all(rows):
            raise EmbedError(f"返回了 {len(rows)} 条向量，要 {n} 条；或其中有空向量")
        return [[float(v) for v in r] for r in rows]

    async def _post(self, texts: list[str]) -> list[list[float]]:
        if not self.configured:
            raise EmbedError("向量模型没配全（地址 / Key / 模型三项都要填）")
        url = self._url(batch=len(texts) > 1)
        payload = self._payload(texts)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with self._gate():
            try:
                async with aiohttp.ClientSession(timeout=timeout) as s:
                    async with s.post(url, headers=self._headers(), json=payload) as r:
                        body = await r.text()
                        if r.status != 200:
                            # 404 的正文常常是空的，不带上 URL 就没法自查
                            raise EmbedError(
                                f"HTTP {r.status}（POST {url}）"
                                + (f"：{body[:200]}" if body.strip() else
                                   "，响应是空的——多半是这个地址不对")
                            )
                        import json

                        data = json.loads(body)
            except EmbedError:
                raise
            except Exception as e:
                raise EmbedError(f"{type(e).__name__}: {e}（POST {url}）") from e
        return self._unpack(self.fmt, data, len(texts))

    # ------------------------------------------------------------------ 接口
    async def resolve_dim(self) -> int:
        """真发一次请求问出维度，顺便验证配置。已知则直接返回。"""
        if self._dim:
            return self._dim
        vec = (await self._post([PROBE_TEXT]))[0]
        dim = len(vec)
        if self.want_dim and dim != self.want_dim:
            raise EmbedError(f"要了 {self.want_dim} 维，模型给了 {dim} 维——把维度改对或留空")
        self._dim = dim
        logger.info(f"[AstrLover] 向量模型就绪：{self.model}（{dim} 维）")
        return dim

    def get_dim(self) -> int:
        if not self._dim:
            raise EmbedError("维度还不知道，先 await resolve_dim()")
        return self._dim

    async def get_embedding(self, text: str) -> list[float]:
        return (await self._post([text or ""]))[0]

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        return await self._post(list(texts))

    async def get_embeddings_batch(
        self,
        texts: list[str],
        batch_size: int = 16,
        tasks_limit: int = 3,
        max_retries: int = 3,
        progress_callback=None,
    ) -> list[list[float]]:
        """分批 + 有限并发 + 重试。顺序必须跟入参一致，否则向量配错文本。"""
        if not texts:
            return []
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        out: dict[int, list[list[float]]] = {}
        sem = asyncio.Semaphore(max(1, tasks_limit))
        done = 0

        async def one(idx: int, chunk: list[str]):
            nonlocal done
            async with sem:
                for attempt in range(max_retries):
                    try:
                        out[idx] = await self._post(chunk)
                        break
                    except EmbedError as e:
                        if attempt == max_retries - 1:
                            raise
                        logger.debug(f"[AstrLover] 向量批次 {idx} 第 {attempt + 1} 次失败：{e}")
                        await asyncio.sleep(1.5 * (attempt + 1))
                done += len(chunk)
                if progress_callback:
                    progress_callback(done, len(texts))

        await asyncio.gather(*(one(i, c) for i, c in enumerate(batches)))
        return [v for i in range(len(batches)) for v in out[i]]

    async def test(self) -> None:
        await self.get_embedding(PROBE_TEXT)
