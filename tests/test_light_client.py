"""自管的轻量文本模型。

跟向量那边一样起真服务器：三种格式的端点、鉴权头、请求体、取正文
方式各不相同，打桩只能证明"我以为的样子"没写错。
"""

import asyncio

import pytest
from aiohttp import web

from astrlover.light.client import LightClient, LightError


def run(coro):
    return asyncio.run(coro)


class FakeAPI:
    def __init__(self):
        self.seen = []
        self.status = 200
        self.body = None          # 覆盖返回体

    async def openai(self, request):
        self.seen.append((str(request.url), dict(request.headers), await request.json()))
        if self.status != 200:
            return web.json_response({"error": "boom"}, status=self.status)
        return web.json_response(
            self.body or {"choices": [{"message": {"role": "assistant", "content": "好"}}]}
        )

    async def anthropic(self, request):
        self.seen.append((str(request.url), dict(request.headers), await request.json()))
        return web.json_response(
            self.body or {"content": [{"type": "text", "text": "好"}]}
        )

    async def gemini(self, request):
        self.seen.append((str(request.url), dict(request.headers), await request.json()))
        return web.json_response(
            self.body or {"candidates": [{"content": {"parts": [{"text": "好"}]}}]}
        )


@pytest.fixture
def api():
    holder = {}

    async def start():
        a = FakeAPI()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", a.openai)
        app.router.add_post("/v1/messages", a.anthropic)
        app.router.add_post("/v1beta/models/{m}:generateContent", a.gemini)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        holder["runner"] = runner
        return f"http://127.0.0.1:{runner.addresses[0][1]}", a

    return start, holder


def _conf(**over):
    base = {
        "light_api_format": "openai", "light_base_url": "", "light_api_key": "k-1",
        "light_model": "gpt-5-mini", "light_max_tokens": 512, "light_timeout": 10,
    }
    base.update(over)
    return type("C", (), {"get": lambda self, k, d=None: base.get(k, d)})()


def test_openai(api):
    start, holder = api

    async def go():
        base, a = await start()
        c = LightClient(_conf(light_base_url=base + "/v1"))
        assert await c.chat("说好", "你是助手") == "好"
        url, headers, body = a.seen[-1]
        assert url.endswith("/v1/chat/completions")
        assert headers["Authorization"] == "Bearer k-1"
        assert body["messages"] == [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "说好"},
        ]
        assert body["max_tokens"] == 512
        await holder["runner"].cleanup()

    run(go())


def test_anthropic(api):
    start, holder = api

    async def go():
        base, a = await start()
        c = LightClient(_conf(light_api_format="anthropic", light_base_url=base,
                              light_model="claude-haiku-4-5-20251001"))
        assert await c.chat("说好", "你是助手") == "好"
        url, headers, body = a.seen[-1]
        assert url.endswith("/v1/messages")
        assert headers["x-api-key"] == "k-1"
        assert headers["anthropic-version"]
        assert body["system"] == "你是助手"        # system 是顶层字段，不进 messages
        assert body["messages"] == [{"role": "user", "content": "说好"}]
        assert body["max_tokens"] == 512           # anthropic 必填
        await holder["runner"].cleanup()

    run(go())


def test_gemini(api):
    start, holder = api

    async def go():
        base, a = await start()
        c = LightClient(_conf(light_api_format="gemini", light_base_url=base,
                              light_model="gemini-2.5-flash"))
        assert await c.chat("说好", "你是助手") == "好"
        url, headers, body = a.seen[-1]
        assert url.endswith("/v1beta/models/gemini-2.5-flash:generateContent")
        assert headers["x-goog-api-key"] == "k-1"   # 不是 ?key=，免得进日志
        assert body["systemInstruction"]["parts"][0]["text"] == "你是助手"
        await holder["runner"].cleanup()

    run(go())


def test_gemini_blocked_input_is_explained(api):
    """输入侧被判死：HTTP 200、没有候选，得说出 blockReason。"""
    start, holder = api

    async def go():
        base, a = await start()
        a.body = {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
        c = LightClient(_conf(light_api_format="gemini", light_base_url=base))
        with pytest.raises(LightError, match="PROHIBITED_CONTENT"):
            await c.chat("x")
        await holder["runner"].cleanup()

    run(go())


def test_http_error_surfaces(api):
    start, holder = api

    async def go():
        base, a = await start()
        a.status = 401
        c = LightClient(_conf(light_base_url=base + "/v1"))
        with pytest.raises(LightError, match="401"):
            await c.chat("x")
        await holder["runner"].cleanup()

    run(go())


def test_not_configured():
    c = LightClient(_conf(light_base_url="", light_api_key="", light_model=""))
    assert not c.configured
    with pytest.raises(LightError, match="没配全"):
        run(c.chat("x"))


def test_llm_falls_back_when_unconfigured():
    """没配轻量模型时退回会话当前模型，功能不受影响。"""
    from astrlover.llm import LLM

    class _Resp:
        completion_text = "来自会话模型"

    class _Provider:
        async def text_chat(self, **_kw):
            return _Resp()

    class _Ctx:
        def get_using_provider(self, **_kw):
            return _Provider()

    llm = LLM(_Ctx(), cfg=None, conf=_conf(light_base_url="", light_api_key="", light_model=""))
    assert not llm.light_ready
    assert run(llm.light("问点什么")) == "来自会话模型"


def test_llm_falls_back_when_light_broken(api):
    """配了但连不上：要退回去继续干活，同时日志里说清楚。"""
    from astrlover.llm import LLM

    start, holder = api

    async def go():
        base, a = await start()
        a.status = 500

        class _Resp:
            completion_text = "来自会话模型"

        class _Provider:
            async def text_chat(self, **_kw):
                return _Resp()

        class _Ctx:
            def get_using_provider(self, **_kw):
                return _Provider()

        llm = LLM(_Ctx(), cfg=None, conf=_conf(light_base_url=base + "/v1"))
        assert llm.light_ready
        assert await llm.light("问点什么") == "来自会话模型"
        await holder["runner"].cleanup()

    run(go())


def test_url_tolerates_pasted_paths():
    def u(base, fmt="openai", model="m"):
        return LightClient(_conf(light_base_url=base, light_api_format=fmt,
                                 light_model=model))._url()

    assert u("https://x.com/v1") == "https://x.com/v1/chat/completions"
    assert u("https://x.com/v1/chat/completions") == "https://x.com/v1/chat/completions"
    assert u("https://x.com/v1/embeddings") == "https://x.com/v1/chat/completions"
    assert u("https://a.com", "anthropic") == "https://a.com/v1/messages"
    assert u("https://a.com/v1", "anthropic") == "https://a.com/v1/messages"
    assert u("https://a.com/v1/messages", "anthropic") == "https://a.com/v1/messages"
    assert u("https://g.com", "gemini") == "https://g.com/v1beta/models/m:generateContent"
    assert u("https://g.com/v1beta", "gemini") == "https://g.com/v1beta/models/m:generateContent"


def test_error_says_which_url(api):
    start, holder = api

    async def go():
        base, a = await start()
        c = LightClient(_conf(light_base_url=base + "/nope"))
        with pytest.raises(LightError) as ei:
            await c.chat("x")
        assert "/nope/chat/completions" in str(ei.value)
        await holder["runner"].cleanup()

    run(go())
