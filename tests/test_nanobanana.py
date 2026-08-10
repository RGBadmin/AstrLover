"""NanoBanana（Gemini 系生图）。

请求体和响应结构照 gemini-3.1-flash-image 的实测文档写死，
起真服务器跑——之前一直失败就是因为协议、鉴权、画幅三处全对不上，
而这些只有把请求真发出去才验得到。
"""

import asyncio
import base64

import pytest
from aiohttp import web

from astrlover.imagegen.nanobanana import NanoBananaBackend
from astrlover.imagegen.prompt_builder import PromptSpec

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
DATA_URI = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


def run(coro):
    return asyncio.run(coro)


class FakeGateway:
    """照文档实现：chat 走 Bearer，原生走 x-goog-api-key。"""

    def __init__(self):
        self.seen = []
        self.chat_status = 200

    async def chat(self, request):
        self.seen.append(("chat", str(request.url), dict(request.headers),
                          await request.json()))
        if self.chat_status != 200:
            return web.json_response({"error": {"message": "nope"}}, status=self.chat_status)
        # content 恒为 null，图在 images 里
        return web.json_response({
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": None,
                            "images": [{"type": "image_url",
                                        "image_url": {"url": DATA_URI}, "index": 0}]},
                "finish_reason": "stop",
            }],
        })

    async def native(self, request):
        self.seen.append(("native", str(request.url), dict(request.headers),
                          await request.json()))
        return web.json_response({
            "candidates": [{"content": {"role": "model", "parts": [
                {"inlineData": {"mimeType": "image/jpeg",
                                "data": base64.b64encode(JPEG).decode()}},
            ]}}],
        })


@pytest.fixture
def gateway():
    holder = {}

    async def start():
        g = FakeGateway()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", g.chat)
        app.router.add_post("/v1beta/models/{m}:generateContent", g.native)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        holder["runner"] = runner
        return f"http://127.0.0.1:{runner.addresses[0][1]}", g

    return start, holder


def _spec(**over):
    d = dict(positive="一只柴犬", negative="模糊, 水印",
             orientation="landscape", width=1216, height=832)
    d.update(over)
    return PromptSpec(**d)


def _backend(base, **over):
    conf = {"api_key": "sk-testtesttesttesttest", "base_url": base,
            "model": "gemini-3.1-flash-image", "format": "auto", "image_size": "1K"}
    conf.update(over)
    return NanoBananaBackend(conf)


# ---------------------------------------------------------------- chat 协议
def test_chat_is_tried_first(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        data = await _backend(base).generate(_spec())
        assert data == JPEG
        kind, url, headers, body = g.seen[0]
        assert kind == "chat", "auto 应该先试 OpenAI 兼容的 chat 接口"
        assert url.endswith("/v1/chat/completions")
        assert headers["Authorization"] == "Bearer sk-testtesttesttesttest"
        assert body["model"] == "gemini-3.1-flash-image"
        await holder["runner"].cleanup()

    run(go())


def test_aspect_ratio_goes_into_top_level_generation_config(gateway):
    """画幅只能靠 generationConfig.imageConfig.aspectRatio。

    以前是把 "832x1216" 写进提示词文字——文档实测那是被静默忽略的，
    不报错、图照出、尺寸不对，最难查的那种。
    """
    start, holder = gateway

    async def go():
        base, g = await start()
        for orient, ratio in (("portrait", "3:4"), ("landscape", "4:3"), ("square", "1:1")):
            await _backend(base).generate(_spec(orientation=orient))
            _, _, _, body = g.seen[-1]
            cfg = body["generationConfig"]["imageConfig"]
            assert cfg["aspectRatio"] == ratio, orient
            assert cfg["imageSize"] == "1K"
            # 尺寸不该再出现在提示词文字里
            text = body["messages"][0]["content"][0]["text"]
            assert "1216x832" not in text and "832x1216" not in text
        await holder["runner"].cleanup()

    run(go())


def test_image_size_is_configurable(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        await _backend(base, image_size="4K").generate(_spec())
        assert g.seen[-1][3]["generationConfig"]["imageConfig"]["imageSize"] == "4K"
        # 乱填的值退回 1K
        await _backend(base, image_size="8K").generate(_spec())
        assert g.seen[-1][3]["generationConfig"]["imageConfig"]["imageSize"] == "1K"
        await holder["runner"].cleanup()

    run(go())


def test_reference_images_ride_as_data_uris(gateway, tmp_path):
    start, holder = gateway

    async def go():
        base, g = await start()
        ref = tmp_path / "anchor.png"
        ref.write_bytes(b"\x89PNGfake")
        await _backend(base).generate(_spec(reference_images=[str(ref), "/不存在.png"]))
        content = g.seen[-1][3]["messages"][0]["content"]
        images = [c for c in content if c.get("type") == "image_url"]
        assert len(images) == 1, "不存在的文件要跳过，不能整条请求废掉"
        assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
        await holder["runner"].cleanup()

    run(go())


# ---------------------------------------------------------------- 原生协议
def test_falls_back_to_native(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        g.chat_status = 404          # 有的站只开原生
        data = await _backend(base).generate(_spec())
        assert data == JPEG
        kinds = [k for k, *_ in g.seen]
        assert kinds == ["chat", "native"]
        _, url, headers, body = g.seen[-1]
        assert url.endswith("/v1beta/models/gemini-3.1-flash-image:generateContent")
        assert headers["x-goog-api-key"] == "sk-testtesttesttesttest"
        assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "4:3"
        await holder["runner"].cleanup()

    run(go())


def test_format_can_be_pinned(gateway):
    start, holder = gateway

    async def go():
        base, g = await start()
        await _backend(base, format="gemini").generate(_spec())
        assert [k for k, *_ in g.seen] == ["native"], "钉死 gemini 就不该先试 chat"
        await holder["runner"].cleanup()

    run(go())


# ---------------------------------------------------------------- 地址容错
def test_base_url_tolerates_pasted_paths():
    def chat(b):
        return _backend(b)._chat_url()

    def native(b):
        return _backend(b)._native_url()

    assert chat("https://x.com") == "https://x.com/v1/chat/completions"
    assert chat("https://x.com/v1") == "https://x.com/v1/chat/completions"
    assert chat("https://x.com/v1/") == "https://x.com/v1/chat/completions"
    assert chat("https://x.com/v1/chat/completions") == "https://x.com/v1/chat/completions"

    assert native("https://x.com").endswith(
        "/v1beta/models/gemini-3.1-flash-image:generateContent")
    assert native("https://x.com/v1beta").endswith(
        "/v1beta/models/gemini-3.1-flash-image:generateContent")


# ---------------------------------------------------------------- 取图与报错
def test_content_is_null_but_images_carries_the_picture():
    """文档明说 content 恒为 null，去 content 里找图永远找不到。"""
    data = NanoBananaBackend._pick_from_chat({
        "choices": [{"message": {"content": None,
                                 "images": [{"image_url": {"url": DATA_URI}}]}}],
    })
    assert data == JPEG


def test_other_relays_embedding_image_in_content_still_work():
    md = f"这是图：![img]({DATA_URI})"
    assert NanoBananaBackend._pick_from_chat(
        {"choices": [{"message": {"content": md}}]}) == JPEG
    assert NanoBananaBackend._pick_from_chat(
        {"choices": [{"message": {"content": [
            {"type": "image_url", "image_url": {"url": DATA_URI}}]}}]}) == JPEG


def test_native_data_has_no_data_uri_prefix():
    assert NanoBananaBackend._pick_from_native({
        "candidates": [{"content": {"parts": [
            {"inlineData": {"data": base64.b64encode(JPEG).decode()}}]}}],
    }) == JPEG


def test_error_names_both_attempts(gateway):
    """两条协议都挂时，报错要说清各自是什么错，不能只剩一句"生图失败"。"""
    start, holder = gateway

    async def go():
        base, g = await start()
        b = _backend(base + "/nope")
        with pytest.raises(RuntimeError) as ei:
            await b.generate(_spec())
        msg = str(ei.value)
        assert "chat:" in msg and "native:" in msg
        assert "404" in msg and "/nope/v1/chat/completions" in msg
        await holder["runner"].cleanup()

    run(go())
