"""参考形象：她长什么样，走图生图保证每次是同一个人。

面板填一个路径（文件或目录），她入镜时带上；拍风景不带——
带着只会污染画面。
"""

import asyncio
import base64

import pytest

from astrlover.imagegen.prompt_builder import PromptSpec


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def pics(tmp_path):
    d = tmp_path / "形象"
    d.mkdir()
    for i, name in enumerate(("a.jpg", "b.png", "c.webp", "d.jpg", "note.txt")):
        (d / name).write_bytes(b"\x89PNG" + bytes([i]))
    return d


@pytest.fixture
def gen_app(app_factory):
    async def build(**over):
        app = app_factory(over)
        await app.initialize()
        return app

    return build


# ---------------------------------------------------------------- 路径解析
def test_single_file(gen_app, pics):
    async def go():
        app = await gen_app(ig_reference=str(pics / "a.jpg"))
        assert app.imagegen.references() == [str(pics / "a.jpg")]
        await app.terminate()

    run(go())


def test_directory_takes_first_three(gen_app, pics):
    async def go():
        app = await gen_app(ig_reference=str(pics))
        refs = app.imagegen.references()
        assert len(refs) == 3, "目录取前 3 张，再多只是撑大请求体"
        assert not any(r.endswith(".txt") for r in refs), "非图片要滤掉"
        await app.terminate()

    run(go())


def test_missing_path_warns_and_degrades(gen_app):
    """路径填错不该让生图整个失败——少个参考图而已。"""

    async def go():
        app = await gen_app(ig_reference="/根本不存在/形象.jpg")
        assert app.imagegen.references() == []
        await app.terminate()

    run(go())


def test_blank_falls_back_to_anchors_dir(gen_app):
    async def go():
        app = await gen_app(ig_reference="")
        anchors = app.data_dir / "anchors"
        anchors.mkdir(parents=True, exist_ok=True)
        (anchors / "x.jpg").write_bytes(b"j")
        assert app.imagegen.references() == [str(anchors / "x.jpg")]
        await app.terminate()

    run(go())


def test_non_image_file_is_rejected(gen_app, pics):
    async def go():
        app = await gen_app(ig_reference=str(pics / "note.txt"))
        assert app.imagegen.references() == []
        await app.terminate()

    run(go())


# ---------------------------------------------------------------- 只在她入镜时带
def test_reference_only_when_she_is_in_frame(gen_app, pics):
    from astrlover.imagegen.prompt_builder import build_spec

    async def go():
        app = await gen_app(ig_reference=str(pics))
        await app.records.set_state("appearance", "黑长直")
        refs = app.imagegen.references()

        async def plan(with_her):
            async def fake(prompt, system_prompt=None):
                return {"orientation": "portrait", "with_her": with_her,
                        "overview": "50mm 平视", "grid": {"正中": "内容"},
                        "tags": "bedroom"}
            app.llm.light_json = fake
            return await build_spec(app, "随便", refs)

        assert (await plan(True)).reference_images == refs
        assert (await plan(False)).reference_images == [], "拍风景不该带她的照片"
        await app.terminate()

    run(go())


# ---------------------------------------------------------------- 各后端真用上
def test_api_backend_sends_reference_as_data_uri(tmp_path):
    from astrlover.imagegen.api import ApiBackend

    ref = tmp_path / "a.png"
    ref.write_bytes(b"\x89PNGxx")
    b = ApiBackend({"api_key": "k", "url": "https://x/v1/chat/completions", "model": "m"})
    payload, _h = b._payload_openai(PromptSpec(
        positive="p", negative="n", reference_images=[str(ref)]))
    imgs = [c for c in payload["messages"][0]["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 1
    assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")
    # 提示词里也要交代这是同一个人
    text = payload["messages"][0]["content"][0]["text"]
    assert "同一人" in text


def test_gemini_shape_sends_inline_data(tmp_path):
    from astrlover.imagegen.api import ApiBackend

    ref = tmp_path / "a.jpg"
    ref.write_bytes(b"jpegbytes")
    b = ApiBackend({"api_key": "k", "url": "https://x/v1beta/models/m:generateContent",
                    "model": "m"})
    payload, headers = b._payload_gemini(PromptSpec(
        positive="p", negative="n", reference_images=[str(ref)]))
    parts = payload["contents"][0]["parts"]
    inline = [p for p in parts if "inline_data" in p]
    assert len(inline) == 1
    assert inline[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert base64.b64decode(inline[0]["inline_data"]["data"]) == b"jpegbytes"
    assert headers["x-goog-api-key"] == "k"


def test_novelai_switches_to_img2img(tmp_path):
    """NovelAI 以前完全无视参考图——有参考形象就该走 img2img。"""
    import json

    from astrlover.imagegen import novelai as nai

    ref = tmp_path / "a.png"
    ref.write_bytes(b"\x89PNGref")
    seen = {}

    class _Resp:
        status = 200

        async def read(self):
            import io
            import zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("a.png", b"OUT")
            return buf.getvalue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Sess:
        def post(self, url, json=None, headers=None):
            seen["payload"] = json
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    nai.aiohttp = type("M", (), {
        "ClientSession": staticmethod(lambda **_k: _Sess()),
        "ClientTimeout": staticmethod(lambda **_k: None),
    })()

    b = nai.NovelAIBackend({"api_key": "k", "model": "nai-diffusion-4-5-full",
                            "steps": 24, "img2img_strength": 0.55})

    # 带参考图 → img2img
    assert run(b.generate(PromptSpec(positive="p", negative="n", tags="1girl",
                                     reference_images=[str(ref)]))) == b"OUT"
    p = seen["payload"]
    assert p["action"] == "img2img"
    assert base64.b64decode(p["parameters"]["image"]) == b"\x89PNGref"
    assert p["parameters"]["strength"] == 0.55

    # 不带 → 还是文生图，不能凭空多出 image 字段
    assert run(b.generate(PromptSpec(positive="p", negative="n", tags="no humans"))) == b"OUT"
    p2 = seen["payload"]
    assert p2["action"] == "generate"
    assert "image" not in p2["parameters"] and "strength" not in p2["parameters"]
    assert json.dumps(p2)          # 可序列化


def test_strength_is_clamped():
    from astrlover.imagegen.novelai import NovelAIBackend

    def s(v):
        return NovelAIBackend({"img2img_strength": v})._strength()

    assert s(0.6) == 0.6
    assert s("0.8") == 0.8
    assert s(5) == 0.99 and s(-1) == 0.05
    assert s("不是数字") == 0.6
