"""自管向量模型客户端。

不打桩：起一个真的 aiohttp 服务器，让客户端真发 HTTP 出去。
两种接口格式的 URL、鉴权头、请求体、响应解包都得对得上，
桩过一遍只能证明"我以为的样子"没写错。
"""

import asyncio

import pytest
from aiohttp import web

from astrlover.embed.client import EmbedClient, EmbedError

DIM = 8


def run(coro):
    return asyncio.run(coro)


class FakeAPI:
    """记录收到的请求，按格式返回像样的响应。"""

    def __init__(self):
        self.seen = []
        self.fail_times = 0

    def _vec(self, text, dim=DIM):
        # 内容相关但确定：同样的文本永远同一个向量
        return [((hash(text) >> i) % 100) / 100 for i in range(dim)]

    async def openai(self, request):
        body = await request.json()
        self.seen.append((str(request.url), dict(request.headers), body))
        if self.fail_times > 0:
            self.fail_times -= 1
            return web.json_response({"error": "rate limited"}, status=429)
        texts = body["input"]
        dim = int(body.get("dimensions") or DIM)
        return web.json_response({
            # 故意乱序返回，逼客户端自己按 index 排
            "data": [{"index": i, "embedding": self._vec(t, dim)}
                     for i, t in reversed(list(enumerate(texts)))]
        })

    async def gemini(self, request):
        body = await request.json()
        self.seen.append((str(request.url), dict(request.headers), body))
        if "requests" in body:
            rows = [r["content"]["parts"][0]["text"] for r in body["requests"]]
            dim = int(body["requests"][0].get("outputDimensionality") or DIM)
            return web.json_response(
                {"embeddings": [{"values": self._vec(t, dim)} for t in rows]}
            )
        text = body["content"]["parts"][0]["text"]
        dim = int(body.get("outputDimensionality") or DIM)
        return web.json_response({"embedding": {"values": self._vec(text, dim)}})


@pytest.fixture
def api_server():
    """返回 (base_url, FakeAPI)。"""
    holder = {}

    async def start():
        api = FakeAPI()
        app = web.Application()
        app.router.add_post("/v1/embeddings", api.openai)
        app.router.add_post("/v1beta/models/{model}:embedContent", api.gemini)
        app.router.add_post("/v1beta/models/{model}:batchEmbedContents", api.gemini)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        holder["runner"] = runner
        return f"http://127.0.0.1:{port}", api

    return start, holder


def _conf(**over):
    base = {
        "embed_api_format": "openai", "embed_base_url": "", "embed_api_key": "k-1",
        "embed_model": "text-embedding-3-small", "embed_dimensions": 0,
        "embed_timeout": 10, "embed_batch": 32,
    }
    base.update(over)
    return type("C", (), {"get": lambda self, k, d=None: base.get(k, d)})()


def test_openai_format(api_server):
    start, holder = api_server

    async def go():
        base, api = await start()
        c = EmbedClient(_conf(embed_base_url=base + "/v1"))
        assert await c.resolve_dim() == DIM
        vecs = await c.get_embeddings(["第一句", "第二句", "第三句"])
        assert len(vecs) == 3 and all(len(v) == DIM for v in vecs)
        # 乱序返回也要按入参顺序还原
        assert vecs[0] == api._vec("第一句") and vecs[2] == api._vec("第三句")

        url, headers, body = api.seen[-1]
        assert url.endswith("/v1/embeddings")
        assert headers["Authorization"] == "Bearer k-1"
        assert body["model"] == "text-embedding-3-small"
        assert body["input"] == ["第一句", "第二句", "第三句"]
        await holder["runner"].cleanup()

    run(go())


def test_gemini_format(api_server):
    start, holder = api_server

    async def go():
        base, api = await start()
        c = EmbedClient(_conf(embed_api_format="gemini", embed_base_url=base,
                              embed_model="gemini-embedding-001"))
        assert await c.resolve_dim() == DIM

        url, headers, body = api.seen[-1]
        assert url.endswith("/v1beta/models/gemini-embedding-001:embedContent")
        assert headers["x-goog-api-key"] == "k-1"     # gemini 不用 Bearer
        assert "content" in body and "requests" not in body

        vecs = await c.get_embeddings(["甲", "乙"])
        assert len(vecs) == 2
        url, _, body = api.seen[-1]
        assert url.endswith(":batchEmbedContents")     # 多条走 batch 端点
        assert len(body["requests"]) == 2
        await holder["runner"].cleanup()

    run(go())


def test_dimensions_requested_and_checked(api_server):
    start, holder = api_server

    async def go():
        base, _ = await start()
        c = EmbedClient(_conf(embed_base_url=base + "/v1", embed_dimensions=4))
        assert await c.resolve_dim() == 4

        # 要 5 维但服务器只按请求给——这里改成服务器不听话的情形
        c2 = EmbedClient(_conf(embed_base_url=base + "/v1", embed_dimensions=4))
        c2._payload = lambda texts: {"model": "m", "input": texts}   # 不带 dimensions
        with pytest.raises(EmbedError, match="维"):
            await c2.resolve_dim()
        await holder["runner"].cleanup()

    run(go())


def test_batch_keeps_order_and_retries(api_server):
    start, holder = api_server

    async def go():
        base, api = await start()
        c = EmbedClient(_conf(embed_base_url=base + "/v1"))
        texts = [f"第{i}句" for i in range(10)]
        api.fail_times = 2                      # 前两批 429，要靠重试救回来
        vecs = await c.get_embeddings_batch(texts, batch_size=3, tasks_limit=2)
        assert len(vecs) == 10
        for t, v in zip(texts, vecs):
            assert v == api._vec(t), "批次拼接顺序错了，向量会配错文本"
        await holder["runner"].cleanup()

    run(go())


def test_not_configured_is_clear():
    async def go():
        c = EmbedClient(_conf(embed_base_url="", embed_api_key="", embed_model=""))
        assert not c.configured
        with pytest.raises(EmbedError, match="没配全"):
            await c.get_embedding("x")

    run(go())


def test_http_error_surfaces(api_server):
    start, holder = api_server

    async def go():
        base, api = await start()
        c = EmbedClient(_conf(embed_base_url=base + "/v1"))
        api.fail_times = 1
        with pytest.raises(EmbedError, match="429"):
            await c.get_embedding("x")
        await holder["runner"].cleanup()

    run(go())


def test_signature_changes_with_model(api_server):
    start, holder = api_server

    async def go():
        base, _ = await start()
        a = EmbedClient(_conf(embed_base_url=base + "/v1", embed_model="m-a"))
        b = EmbedClient(_conf(embed_base_url=base + "/v1", embed_model="m-b"))
        await a.resolve_dim()
        await b.resolve_dim()
        assert a.signature() != b.signature(), "换了模型必须认得出来，否则不会重建向量库"
        await holder["runner"].cleanup()

    run(go())


def test_url_tolerates_pasted_paths():
    """地址栏最常见的错法是连路径一起粘过来，硬拼就是个空正文 404。"""
    def u(base, fmt="openai", batch=False, model="m"):
        return EmbedClient(_conf(embed_base_url=base, embed_api_format=fmt,
                                 embed_model=model))._url(batch)

    # openai：补、不重复补、从视觉那栏抄来的也认
    assert u("https://x.com/v1") == "https://x.com/v1/embeddings"
    assert u("https://x.com/v1/") == "https://x.com/v1/embeddings"
    assert u("https://x.com/v1/embeddings") == "https://x.com/v1/embeddings"
    assert u("https://x.com/v1/chat/completions") == "https://x.com/v1/embeddings"
    assert u("https://x.com") == "https://x.com/embeddings"

    # gemini：v1beta 自动补，已经带版本段就不再补
    assert u("https://g.com", "gemini") == "https://g.com/v1beta/models/m:embedContent"
    assert u("https://g.com/v1beta", "gemini") == "https://g.com/v1beta/models/m:embedContent"
    assert u("https://g.com", "gemini", batch=True).endswith(":batchEmbedContents")
    already = "https://g.com/v1beta/models/m:embedContent"
    assert u(already, "gemini") == already


def test_error_says_which_url(api_server):
    """404 的正文常常是空的，不带 URL 根本没法自查。"""
    start, holder = api_server

    async def go():
        base, _ = await start()
        c = EmbedClient(_conf(embed_base_url=base + "/nope"))
        with pytest.raises(EmbedError) as ei:
            await c.get_embedding("x")
        msg = str(ei.value)
        assert "404" in msg and "/nope/embeddings" in msg, msg
        await holder["runner"].cleanup()

    run(go())


def test_empty_body_error_says_so(api_server):
    """网关返回空正文的 404——用户看到的就是这个，得点破是地址问题。"""
    start, holder = api_server

    async def go():
        base, _ = await start()

        async def blank(_req):
            return web.Response(status=404, text="")

        # 换一个只回空正文的路由
        app = web.Application()
        app.router.add_post("/v1/embeddings", blank)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{runner.addresses[0][1]}/v1"

        c = EmbedClient(_conf(embed_base_url=url))
        with pytest.raises(EmbedError) as ei:
            await c.get_embedding("x")
        msg = str(ei.value)
        assert "404" in msg and "/v1/embeddings" in msg and "地址不对" in msg, msg
        await runner.cleanup()
        await holder["runner"].cleanup()

    run(go())
