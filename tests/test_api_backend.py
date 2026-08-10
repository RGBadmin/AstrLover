"""API 类型的生图后端：协议由地址决定，不猜。

请求体和响应结构照 gemini-3.1-flash-image 的实测文档写死，起真服务器跑——
协议、鉴权、画幅这三类错只有把请求真发出去才验得到。
"""

import asyncio
import base64

import pytest
from aiohttp import web

from astrlover.imagegen.api import ApiBackend, ProtocolError, detect
from astrlover.imagegen.prompt_builder import PromptSpec

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
B64 = base64.b64encode(JPEG).decode()
DATA_URI = "data:image/jpeg;base64," + B64


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 认协议
def test_protocol_comes_from_the_url():
    assert detect("https://x.com/v1/chat/completions") == "openai"
    assert detect("https://x.com/v1beta/models/gemini-3.1-flash-image:generateContent") == "gemini"
    assert detect("https://x.com/v1beta/models/m:generateContent") == "gemini"
    assert detect("https://api.x.ai/v1/images/generations") == "grok"
    assert detect("https://x.com/v1/images/edits") == "grok"
    # 大小写不影响
    assert detect("https://X.com/V1/Chat/Completions") == "openai"


def test_unrecognized_url_says_what_to_write():
    """严格地址：认不出就报错，不去猜、不去挨个试。"""
    with pytest.raises(ProtocolError) as ei:
        detect("https://x.com")
    msg = str(ei.value)
    assert "chat/completions" in msg and "generateContent" in msg and "images/generations" in msg


# ---------------------------------------------------------------- 真请求
class Gateway:
    def __init__(self):
        self.seen = []

    async def openai(self, request):
        self.seen.append(("openai", str(request.url), dict(request.headers), await request.json()))
        # content 恒为 null，图在 images 里
        return web.json_response({"choices": [{"message": {
            "role": "assistant", "content": None,
            "images": [{"type": "image_url", "image_url": {"url": DATA_URI}, "index": 0}]}}]})

    async def gemini(self, request):
        self.seen.append(("gemini", str(request.url), dict(request.headers), await request.json()))
        return web.json_response({"candidates": [{"content": {"role": "model", "parts": [
            {"inlineData": {"mimeType": "image/jpeg", "data": B64}}]}}]})

    async def grok(self, request):
        self.seen.append(("grok", str(request.url), dict(request.headers), await request.json()))
        return web.json_response({"data": [{"b64_json": B64}]})


@pytest.fixture
def gateway():
    holder = {}

    async def start():
        g = Gateway()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", g.openai)
        app.router.add_post("/v1beta/models/{m}:generateContent", g.gemini)
        app.router.add_post("/v1/images/generations", g.grok)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        holder["runner"] = runner
        return f"http://127.0.0.1:{runner.addresses[0][1]}", g

    return start, holder


def _spec(**over):
    d = dict(positive="一只柴犬", negative="模糊", orientation="landscape")
    d.update(over)
    return PromptSpec(**d)


def _backend(url, **over):
    conf = {"api_key": "sk-test", "url": url, "model": "gemini-3.1-flash-image",
            "image_size": "1K"}
    conf.update(over)
    return ApiBackend(conf)


def test_openai_shape(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        assert await _backend(base + "/v1/chat/completions").generate(_spec()) == JPEG
        proto, url, headers, body = g.seen[-1]
        assert proto == "openai"
        assert headers["Authorization"] == "Bearer sk-test"
        assert body["model"] == "gemini-3.1-flash-image"
        # 画幅只能靠顶层 generationConfig，写进提示词文字会被静默忽略
        assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "4:3"
        await holder["runner"].cleanup()

    run(go())


def test_gemini_shape(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        url = base + "/v1beta/models/gemini-3.1-flash-image:generateContent"
        assert await _backend(url).generate(_spec()) == JPEG
        proto, _u, headers, body = g.seen[-1]
        assert proto == "gemini"
        assert headers["x-goog-api-key"] == "sk-test"       # 不是 Bearer
        assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
        assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "4:3"
        await holder["runner"].cleanup()

    run(go())


def test_grok_shape(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        assert await _backend(base + "/v1/images/generations",
                              model="grok-imagine-image").generate(_spec()) == JPEG
        proto, _u, headers, body = g.seen[-1]
        assert proto == "grok"
        assert headers["Authorization"] == "Bearer sk-test"
        assert body["prompt"].startswith("一只柴犬") and body["n"] == 1
        assert body["response_format"] == "b64_json"
        await holder["runner"].cleanup()

    run(go())


def test_only_the_written_endpoint_is_called(gateway):
    """严格地址：填了 chat 就只发 chat，不会偷偷再试别的。"""
    start, holder = gateway

    async def go():
        base, g = await start()
        await _backend(base + "/v1/chat/completions").generate(_spec())
        assert len(g.seen) == 1
        await holder["runner"].cleanup()

    run(go())


def test_aspect_ratio_per_orientation(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        for orient, ratio in (("portrait", "3:4"), ("landscape", "4:3"), ("square", "1:1")):
            await _backend(base + "/v1/chat/completions").generate(_spec(orientation=orient))
            assert g.seen[-1][3]["generationConfig"]["imageConfig"]["aspectRatio"] == ratio
        await holder["runner"].cleanup()

    run(go())


def test_reference_images_ride_along(gateway, tmp_path):
    start, holder = gateway

    async def go():
        base, g = await start()
        ref = tmp_path / "a.png"
        ref.write_bytes(b"\x89PNG")
        await _backend(base + "/v1/chat/completions").generate(
            _spec(reference_images=[str(ref), "/不存在.png"]))
        content = g.seen[-1][3]["messages"][0]["content"]
        imgs = [c for c in content if c.get("type") == "image_url"]
        assert len(imgs) == 1, "不存在的文件跳过，不能整条请求废掉"
        assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")
        await holder["runner"].cleanup()

    run(go())


def test_http_error_names_protocol_and_url(gateway):
    start, holder = gateway

    async def go():
        base, _g = await start()
        with pytest.raises(RuntimeError) as ei:
            await _backend(base + "/nope/v1/chat/completions").generate(_spec())
        msg = str(ei.value)
        assert "404" in msg and "openai" in msg and "/nope/v1/chat/completions" in msg
        await holder["runner"].cleanup()

    run(go())


def test_not_configured_without_url_or_key():
    assert not _backend("", api_key="k").configured()
    assert not _backend("https://x/v1/chat/completions", api_key="").configured()
    assert _backend("https://x/v1/chat/completions").configured()


# ---------------------------------------------------------------- 取图
def test_pick_handles_each_shape():
    assert ApiBackend._pick_openai(
        {"choices": [{"message": {"content": None,
                                  "images": [{"image_url": {"url": DATA_URI}}]}}]}) == JPEG
    assert ApiBackend._pick_openai(
        {"choices": [{"message": {"content": f"图：![x]({DATA_URI})"}}]}) == JPEG
    assert ApiBackend._pick_gemini(
        {"candidates": [{"content": {"parts": [{"inlineData": {"data": B64}}]}}]}) == JPEG
    assert ApiBackend._pick_grok({"data": [{"b64_json": B64}]}) == JPEG
    assert ApiBackend._pick_grok({"data": [{"url": DATA_URI}]}) == JPEG


# ---------------------------------------------------------------- 两槽装配
def test_main_and_backup_only(app_factory):
    """一主一备，没有第三层；未配的自动不算。"""

    async def go():
        app = app_factory({
            "ig_main_type": "api", "ig_main_key": "k",
            "ig_main_url": "https://x.com/v1/chat/completions",
            "ig_backup_type": "novelai", "ig_backup_key": "nk",
        })
        await app.initialize()
        names = [(b.slot, b.name) for b in app.imagegen.backends]
        assert names == [("主", "API"), ("备", "novelai")]
        await app.terminate()

    run(go())


def test_backup_left_blank_is_skipped(app_factory):
    async def go():
        app = app_factory({
            "ig_main_type": "api", "ig_main_key": "k",
            "ig_main_url": "https://x.com/v1/chat/completions",
        })
        await app.initialize()
        assert [b.slot for b in app.imagegen.backends] == ["主"]
        await app.terminate()

    run(go())


def test_unknown_type_is_ignored(app_factory):
    async def go():
        app = app_factory({"ig_main_type": "乱写", "ig_main_key": "k",
                           "ig_main_url": "https://x/v1/chat/completions"})
        await app.initialize()
        assert not app.imagegen.available
        await app.terminate()

    run(go())
