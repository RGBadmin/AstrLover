"""相册向量化：一张图的描述切四段，各转一个向量。

为什么要切：整篇三千字里九成是身体细节，环境那两百字会被彻底稀释——
「黑色反光桌面，大片水渍」在全文向量里几乎看不见。按层切开各转一个，
检索时取最大相似度，环境词就能直接撞上环境段。
标签行是关键词密度最高的一段，无论在描述开头还是末尾都并进动作段。

向量走 AstrBot Embedding Provider + FAISS（供应商中立，随平台配置）。
"""

import asyncio
import re

from astrbot.api import logger

from ..vision.tags import find_tag_line

SEGMENTS = ("full", "env", "body", "act")

_LAYER_HEAD = re.compile(
    r"(?:^|\n)[\s#*【\[]*(环境与背景|物品道具|人物整体|身体细节|互动动作|体液痕迹)[】\]]*[:：]?\s*"
)
_LAYER_OF_SEG = {
    "env": ("环境与背景", "物品道具"),
    "body": ("人物整体", "身体细节"),
    "act": ("互动动作", "体液痕迹"),
}


def split_layers(desc: str) -> dict[str, str]:
    """按层级标题切描述；没有标题的老式描述只有 full 段。"""
    out = {"full": desc.strip()}
    pieces: dict[str, list[str]] = {}
    matches = list(_LAYER_HEAD.finditer(desc))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(desc)
        pieces.setdefault(m.group(1), []).append(desc[m.end():end].strip())
    tag = find_tag_line(desc)
    for seg, layers in _LAYER_OF_SEG.items():
        text = "\n".join(t for name in layers for t in pieces.get(name, []) if t)
        if seg == "act" and tag and tag not in text:
            text = (text + "\n" + tag).strip()
        if text.strip():
            out[seg] = text.strip()
    return out


class AlbumEmbedder:
    def __init__(self, app):
        self.app = app
        self._task: asyncio.Task | None = None
        self.note = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def stop(self):
        if self.running:
            self._task.cancel()
        self._task = None

    def start_auto(self, progress_cb=None):
        if self.running:
            return False
        self._task = asyncio.create_task(self._run(None, progress_cb))
        return True

    async def run_count(self, count: int, progress_cb=None) -> str:
        return await self._run(count, progress_cb)

    async def redo_all(self) -> str:
        n = await self.app.album.clear_embedded()
        # FAISS 里旧向量作废：直接重建库文件最干净
        await self.app.vectors.rebuild_album()
        return f"已清空 {n} 张的向量标记并重建向量库，跑 /gallery embed auto 重转。"

    # ------------------------------------------------------------------
    async def _run(self, count: int | None, progress_cb) -> str:
        app = self.app
        if not await app.vectors.ensure():
            return "Embedding Provider 未配置或初始化失败（life_models 组）"
        done = 0
        batch = max(4, min(64, int(app.star_conf.get("embed_batch", 16) or 16)))
        try:
            while count is None or done < count:
                limit = batch if count is None else min(batch, count - done)
                rows = await app.album.next_unembedded(limit=limit)
                if not rows:
                    break
                for row in rows:
                    segs = split_layers(row["desc"])
                    try:
                        for seg, text in segs.items():
                            await app.vectors.add_album_segment(
                                text, {"img": row["id"], "seg": seg}
                            )
                    except Exception as e:
                        self.note = f"{type(e).__name__}: {e}"[:120]
                        logger.warning(f"[AstrLover] 向量写入失败（#{row['id']}）：{e}")
                        return f"向量接口出错，已停在 {done} 张：{self.note}"
                    await app.album.mark_embedded(row["id"])
                    done += 1
                if progress_cb:
                    try:
                        await progress_cb(f"向量转换：已完成 {done} 张")
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        return f"向量转换结束：{done} 张（每张最多 {len(SEGMENTS)} 段）。"

    async def probe(self) -> str:
        """/gallery embed test：探区分度。低于 0.05 说明模型对这类文本无有效表示。"""
        app = self.app
        if not await app.vectors.ensure():
            return "Embedding Provider 未配置或初始化失败"
        a = "酒店房间落地窗前，黑色丝袜配红底细高跟，倚在窗边回头看镜头"
        b = "卧室大床上仰躺，白色过膝袜，双腿抬起弯曲，逆光剪影"
        c = "一碗热气腾腾的牛肉面，葱花香菜，木质餐桌"
        try:
            va, vb, vc = [await app.vectors.embed_text(t) for t in (a, b, c)]
        except Exception as e:
            return f"向量接口出错：{type(e).__name__}: {e}"

        def cos(x, y):
            num = sum(i * j for i, j in zip(x, y))
            dx = sum(i * i for i in x) ** 0.5
            dy = sum(j * j for j in y) ** 0.5
            return num / (dx * dy) if dx and dy else 0.0

        same, diff = cos(va, vb), max(cos(va, vc), cos(vb, vc))
        gap = same - diff
        verdict = "✅ 区分度正常" if gap >= 0.05 else "⚠️ 区分度过低——这个模型对这类文本没有有效表示，检索会一直不准，建议换模型"
        return (f"同类相似 {same:.3f} / 异类相似 {diff:.3f} / 区分度 {gap:.3f}\n{verdict}")
