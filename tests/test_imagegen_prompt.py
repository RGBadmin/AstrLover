"""生图提示词构建。

以前是硬拼的：`人物：{外观}；画面：{情境}；…同一位女生`——
不管要什么都先塞一个人进去，写「赤道无风带」出来的也是个女孩。
这里盯住的就是那件事：不该入镜时，人物和锚点图都不能出现。
"""

import asyncio

import pytest

from astrlover.imagegen.prompt_builder import (
    SIZES,
    PromptSpec,
    build_spec,
    fallback_spec,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def gen_app(app_factory):
    async def build(plan=None, appearance="黑长直，白皮肤，常穿针织衫"):
        app = app_factory()
        await app.initialize()
        await app.records.set_state("appearance", appearance)

        async def fake_light_json(prompt, system_prompt=None):
            app._seen_prompt = prompt
            app._seen_system = system_prompt
            return plan

        app.llm.light_json = fake_light_json
        return app

    return build


_SCENE = {
    "orientation": "landscape",
    "with_her": False,
    "overview": "24mm 广角贴海面平视，正午顶光，硬光高反差，f/8 全景深",
    "grid": {"左上": "积雨云顶", "上中": "惨白天空", "右上": "远处层积云",
             "左中": "空旷海面", "正中": "垂帆的船影", "右中": "镜面海水",
             "左下": "深蓝近黑的近处水", "下中": "船的倒影", "右下": "漂浮的马尾藻"},
}
_SELFIE = {
    "orientation": "portrait",
    "with_her": True,
    "overview": "50mm 平视逆光，日落前的暖金侧逆光，f/1.8 浅景深",
    "grid": {"正中": "她的侧脸", "上中": "橘紫渐层的天空"},
}


# ---------------------------------------------------------------- 不入镜
def test_scene_has_no_person(gen_app):
    """写「赤道无风带」就不该出现人——这是最初那张图的病根。"""

    async def go():
        app = await gen_app(_SCENE)
        spec = await build_spec(app, "代表赤道无风带的图", ["/tmp/anchor1.png"])
        assert "人物：" not in spec.positive
        assert "同一位女生" not in spec.positive
        assert not spec.reference_images, "不入镜就不该带外观锚点图"
        assert "different person" not in spec.negative
        assert not spec.with_her
        await app.terminate()

    run(go())


def test_scene_prompt_keeps_photography_and_grid(gen_app):
    async def go():
        app = await gen_app(_SCENE)
        spec = await build_spec(app, "代表赤道无风带的图", [])
        assert "总视图：" in spec.positive and "24mm" in spec.positive
        assert "九宫格：" in spec.positive
        for cell in ("左上", "正中", "右下"):
            assert cell in spec.positive, f"少了 {cell} 格"
        await app.terminate()

    run(go())


def test_grid_cells_keep_reading_order(gen_app):
    """九宫格要按左上→右下的顺序，不能随字典顺序乱。"""

    async def go():
        app = await gen_app(_SCENE)
        spec = await build_spec(app, "海", [])
        pos = spec.positive
        order = [pos.index(c) for c in ("左上", "上中", "右上", "左中", "正中",
                                        "右中", "左下", "下中", "右下")]
        assert order == sorted(order)
        await app.terminate()

    run(go())


def test_empty_cells_are_dropped(gen_app):
    async def go():
        app = await gen_app(_SELFIE)
        spec = await build_spec(app, "阳台上的晚霞", [])
        assert "左上：" not in spec.positive, "没内容的格子不该占行"
        assert "正中：她的侧脸" in spec.positive
        await app.terminate()

    run(go())


# ---------------------------------------------------------------- 入镜
def test_selfie_carries_appearance_and_anchors(gen_app):
    async def go():
        app = await gen_app(_SELFIE)
        spec = await build_spec(app, "想给他看晚霞", ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"])
        assert spec.positive.startswith("人物：黑长直")
        assert spec.reference_images == ["/tmp/a.png", "/tmp/b.png"], "锚点图最多两张"
        assert "different person" in spec.negative
        assert spec.with_her
        await app.terminate()

    run(go())


# ---------------------------------------------------------------- 画幅
def test_orientation_maps_to_novelai_sizes(gen_app):
    """三个规格是 NovelAI 的：竖 832x1216 / 横 1216x832 / 方 1024x1024。"""
    assert SIZES == {"portrait": (832, 1216), "landscape": (1216, 832),
                     "square": (1024, 1024)}

    async def go():
        for name, (w, h) in SIZES.items():
            app = await gen_app({**_SCENE, "orientation": name})
            spec = await build_spec(app, "随便", [])
            assert (spec.width, spec.height) == (w, h), name
            await app.terminate()

    run(go())


def test_unknown_orientation_falls_back_to_portrait(gen_app):
    async def go():
        app = await gen_app({**_SCENE, "orientation": "斜的"})
        spec = await build_spec(app, "随便", [])
        assert (spec.width, spec.height) == SIZES["portrait"]
        await app.terminate()

    run(go())


# ---------------------------------------------------------------- 上下文与兜底
def test_recent_conversation_is_fed_in(gen_app):
    """要"根据上下文判断需要什么图"，就得先看得到上下文。"""

    async def go():
        app = await gen_app(_SCENE)
        await app.set_target("tg:FriendMessage:123")
        app.context.history = [
            {"role": "user", "content": "在看纪录片，讲赤道无风带的"},
            {"role": "assistant", "content": "[08-09 20:00] 听着就闷"},
        ]
        await build_spec(app, "配张图", [])
        assert "赤道无风带" in app._seen_prompt
        assert "配张图" in app._seen_prompt
        await app.terminate()

    run(go())


def test_template_comes_from_settings(gen_app):
    async def go():
        app = await gen_app(_SCENE)
        await app.conf.save(app.dao, {"ig_prompt": "自定义模板"})
        await build_spec(app, "随便", [])
        assert app._seen_system.startswith("自定义模板")
        assert '"orientation"' in app._seen_system, "输出约定要由代码追加"
        await app.terminate()

    run(go())


def test_broken_plan_falls_back(gen_app):
    """模型抽风时退回直白拼接，不让生图整条链断掉。"""

    async def go():
        for bad in (None, "不是字典", {"orientation": "portrait"}):
            app = await gen_app(bad)
            spec = await build_spec(app, "阳台上的晚霞", ["/tmp/a.png"])
            assert isinstance(spec, PromptSpec) and spec.positive.strip()
            assert "阳台上的晚霞" in spec.positive
            await app.terminate()

    run(go())


def test_fallback_without_appearance_has_no_person_negatives():
    spec = fallback_spec("", "海面", [])
    assert "人物：" not in spec.positive
    assert "different person" not in spec.negative
    assert not spec.with_her


# ---------------------------------------------------------------- 英文标签
def test_tags_are_cleaned_and_deduped(gen_app):
    async def go():
        app = await gen_app({**_SCENE, "tags": "Ocean,  ocean , HORIZON,,sky"})
        spec = await build_spec(app, "海", [])
        tags = spec.tags.split(", ")
        assert tags.count("ocean") == 1, "重复标签没去掉"
        assert "horizon" in tags, "没转小写"
        assert "" not in tags
        await app.terminate()

    run(go())


def test_subject_tag_is_forced(gen_app):
    """漏了 no humans，NovelAI 一定给你画个人——这是两张废图的直接原因。"""

    async def go():
        app = await gen_app({**_SCENE, "tags": "ocean, sky"})
        spec = await build_spec(app, "海", [])
        assert spec.tags.startswith("no humans")

        app2 = await gen_app({**_SELFIE, "tags": "bedroom, pajamas"})
        spec2 = await build_spec(app2, "趴在床上", [])
        assert spec2.tags.startswith("1girl, solo")
        await app.terminate()
        await app2.terminate()

    run(go())


def test_quality_tags_are_appended(gen_app):
    """没有质量标签时 NAI 会往草图/线稿漂。"""

    async def go():
        app = await gen_app({**_SCENE, "tags": "ocean"})
        spec = await build_spec(app, "海", [])
        for q in ("best quality", "very aesthetic", "absurdres"):
            assert q in spec.tags
        await app.terminate()

    run(go())


def test_fallback_still_carries_quality_tags():
    assert "best quality" in fallback_spec("黑长直", "阳台", []).tags
    assert fallback_spec("黑长直", "阳台", []).tags.startswith("1girl, solo")


def test_novelai_payload_uses_tags_and_uc():
    """把请求体真拼一遍：正文是标签、负面是 UC、宽高是画幅、步数是配置值。"""
    import asyncio as _a

    from astrlover.imagegen import novelai as nai

    spec = PromptSpec(
        positive="中文摄影稿", negative="中文负面",
        tags="no humans, ocean, sky, best quality",
        width=1216, height=832,
    )
    seen = {}

    class _Resp:
        status = 200

        async def read(self):
            import io
            import zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("a.png", b"PNG")
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

    b = nai.NovelAIBackend({"api_key": "k", "model": "nai-diffusion-4-5-full", "steps": 30})
    data = _a.run(b.generate(spec))
    assert data == b"PNG"

    p = seen["payload"]["parameters"]
    assert seen["payload"]["input"] == spec.tags, "正文该是标签"
    assert "中文" not in seen["payload"]["input"]
    assert "sketch" in p["negative_prompt"] and "lineart" in p["negative_prompt"]
    assert (p["width"], p["height"]) == (1216, 832)
    assert p["steps"] == 30
    assert p["qualityToggle"] is True
    assert p["v4_prompt"]["caption"]["base_caption"] == spec.tags
