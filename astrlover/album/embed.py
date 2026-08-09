"""相册向量化：一张图的描述切三段，各转一个向量。

为什么要切：一个向量装不下整篇的细节，长的那部分会把短的淹掉。
描述里身体细节占一半以上，环境那两百字在整篇向量里几乎看不见——
搜「车里」时，所有车里拍的图整篇向量彼此差得很远，谁都不像。
按层切开各转一个、检索时取最大相似度，环境词就能直接撞上环境段。

三段（env / body / act）跟检索侧对齐：提示词让她按环境、身体衣着、
动作体液三类各给几个词，三类词各打一段。
标签行是关键词密度最高的一行，并进动作段。

向量来自插件自管的向量模型（astrlover/embed/client.py）+ FAISS。
"""

import asyncio
import time
import re

from astrbot.api import logger

from ..vision.tags import find_tag_line

# 三段，不是四段。原先还有个 full（整篇），砍掉了：描述一千五到两千字时
# 第三层仍占大头，full ≈ body 加一点噪声，两个向量高度相关，取最大时几乎
# 总是同时高同时低——多花 25% 的调用换一份重复信息。full 还是最长的一段，
# 最容易撞上 embedding 的输入上限（gemini-embedding-001 只有 2048 token），
# 一截断砍掉的正好是排在最后的互动与体液两层。
SEGMENTS = ("env", "body", "act")

# 标题两种写法都认：提示词里写「第一层：环境与背景」，也可能只写裸标题；
# 三个层名还有带不带「与」两种（互动与动作 / 互动动作）。
# 认死一种的下场是三段静默退化成整篇一段，而且不报任何错。
_LAYER_HEAD = re.compile(
    r"(?:^|\n)[\s#*【\[]*"
    r"(?:第[一二三四五六1-6]层[\s:：、.]*)?"
    r"(环境与背景|环境背景|人物整体|身体细节"
    r"|互动与动作|互动动作|物品与道具|物品道具|体液与痕迹|体液痕迹)"
    r"[】\]]*[\s:：]*"
)
_LAYER_OF_SEG = {
    "env": ("环境与背景", "环境背景"),
    "body": ("人物整体", "身体细节"),
    # 道具跟动作体液一段：提示词里第五层写的是玩具、绳子、衣物、液体，
    # 检索侧「跳蛋 假鸡巴 绳子」也归在动作那一类，两边对齐
    "act": ("互动与动作", "互动动作", "物品与道具", "物品道具",
            "体液与痕迹", "体液痕迹"),
}

_FALLBACK_COVER = 0.5   # 切出来的内容不足描述的一半，就当没切明白


def split_layers(desc: str) -> dict[str, str]:
    """按层级标题切成三段。

    切不出来（老式描述、标题被模型写飞了）就整篇一段兜底——
    宁可多转一个向量，也不能让一部分内容根本进不了索引。
    """
    desc = (desc or "").strip()
    if not desc:
        return {}
    out: dict[str, str] = {}
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
    if sum(len(t) for t in out.values()) < len(desc) * _FALLBACK_COVER:
        out["full"] = desc
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
            return f"向量模型用不了：{app.vectors.last_error or '没配（面板「向量模型」组）'}"
        done = 0
        started = last_report = time.time()
        batch = max(4, min(64, int(app.conf.get("embed_batch", 32) or 32)))
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
                # 以前每批都报一次（一批 32 张），几秒一条消息，而且只说
                # "已完成 N 张"——既刷屏又什么都没讲。改成定时 + 讲清楚
                if progress_cb and time.time() - last_report > _REPORT_GAP:
                    last_report = time.time()
                    try:
                        await progress_cb(await self._progress(done, started))
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        st = await app.album.stats()
        left = max(0, int(st.get("ok", 0)) - int(st.get("embedded", 0)))
        return (f"🧮 向量转换结束：本轮 {done} 张（每张最多 {len(SEGMENTS)} 段）。\n"
                f"　全库已转 {st.get('embedded', 0)}/{st.get('ok', 0)}"
                + (f"，还剩 {left} 张——`/gallery embed auto` 接着跑。" if left else "，全部转完了。")
                + (f"\n　最近一次失败：{self.note}" if self.note else ""))

    async def _progress(self, done: int, started: float) -> str:
        st = await self.app.album.stats()
        ok_total = int(st.get("ok", 0))
        embedded = int(st.get("embedded", 0))
        left = max(0, ok_total - embedded)
        rate = done / max(1.0, time.time() - started) * 3600
        eta = f"，按当前速度还要 {left / rate:.1f} 小时" if rate > 0 and left else ""
        lines = [
            f"🧮 转向量中：本轮 {done} 张，全库 {embedded}/{ok_total}，还剩 {left} 张{eta}",
            f"　速度 {rate:.0f} 张/小时，模型 {self.app.vectors.client.model}",
        ]
        if self.note:
            lines.append(f"　最近一次失败：{self.note}")
        lines.append("　`/gallery embed stop` 可以停，进度不丢。")
        return "\n".join(lines)

    async def probe(self) -> str:
        """/gallery embed test：探区分度。低于 0.05 说明模型对这类文本无有效表示。"""
        app = self.app
        if not await app.vectors.ensure():
            return f"向量模型用不了：{app.vectors.last_error or '没配（面板「向量模型」组）'}"
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
