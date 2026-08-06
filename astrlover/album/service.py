"""相册门面：扫描/索引/向量/检索/维护的统一入口，供工具与控制台调用。"""

from astrbot.api import logger

from ..vision.tags import parse_tag_line, scrub_tag_line
from ..vision import validate
from .embed import AlbumEmbedder
from .index import VISION_MAX_FAILS, AlbumIndexer
from .scan import AlbumScanner
from .search import AlbumSearch
from .store import AlbumStore


class Album:
    def __init__(self, app):
        self.app = app
        self.store = AlbumStore(app.db)
        self.scanner = AlbumScanner(self.store, lambda: app.star_conf.get("gallery_dir"))
        self.indexer = AlbumIndexer(app)
        self.embedder = AlbumEmbedder(app)
        self.search_engine = AlbumSearch(app)

    # ---- 便捷转发（app.album.* 即 store.*，减少一层） ----
    def __getattr__(self, name):
        # __init__ 完成前（或名字确实不存在）不能递归到自己身上
        if name.startswith("_") or "store" not in self.__dict__:
            raise AttributeError(name)
        return getattr(self.store, name)

    def abs_path(self, rel: str):
        return self.scanner.abs_path(rel)

    async def search(self, *args, **kwargs):
        return await self.search_engine.search(*args, **kwargs)

    # ------------------------------------------------------------------
    # 维护动作
    # ------------------------------------------------------------------
    async def polish(self) -> str:
        """确定性清洗存量描述，不重跑索引：删字堆段、泛称改角色名、重解析标签。"""
        subject = str(self.app.star_conf.get("subject_name") or "").strip()
        rows = await self.store.all_ok()
        changed = retag = 0
        for row in rows:
            new_desc = scrub_tag_line(row["desc"], subject)
            tags = parse_tag_line(new_desc)
            if new_desc != row["desc"]:
                changed += 1
                await self.store.update_desc(row["id"], new_desc, tags["rating"], tags["season"])
            elif tags["rating"] != row["rating"] or tags["season"] != row["season"]:
                retag += 1
                await self.store.update_desc(row["id"], new_desc, tags["rating"], tags["season"])
        return (
            f"清洗完成：{len(rows)} 张里改写描述 {changed} 张、补/改标签 {retag} 张。"
            + ("\n改过的图向量已标脏，跑 /gallery embed auto 重转。" if changed + retag else "")
        )

    async def clean(self) -> str:
        """揪出拒答、思维链、过短的脏描述（不自动删，只报告）。"""
        min_chars = int(self.app.star_conf.get("vision_min_chars", 0) or 0)
        max_chars = max(100, int(self.app.star_conf.get("vision_max_chars", 600) or 600))
        rows = await self.store.all_ok()
        bad: list[str] = []
        for row in rows:
            reason = validate.junk_reason(row["desc"], min_chars, max_chars)
            if reason:
                bad.append(f"g{row['id']} {row['path'][:40]}｜{reason}")
        if not bad:
            return f"检查了 {len(rows)} 张，没发现脏描述。"
        head = "\n".join(bad[:20])
        more = f"\n…还有 {len(bad) - 20} 张" if len(bad) > 20 else ""
        return (f"发现 {len(bad)} 张脏描述（共 {len(rows)} 张）：\n{head}{more}\n"
                "用 /gallery redo <编号> 退回重跑。")

    async def redo(self, image_id: int) -> str:
        row = await self.store.get(image_id)
        if row is None:
            return f"没有 g{image_id} 这张。"
        await self.store.requeue(image_id)
        return f"g{image_id} 已退回待索引队列。"

    async def audit(self) -> str:
        """关键词行的质量分布。"""
        rows = await self.store.all_ok()
        if not rows:
            return "库里还没有已索引的图。"
        no_tag = no_rating = no_season = 0
        kw_total = 0
        for row in rows:
            tags = parse_tag_line(row["desc"])
            if not tags["keywords"]:
                no_tag += 1
            kw_total += len(tags["keywords"])
            if not row["rating"]:
                no_rating += 1
            if not row["season"]:
                no_season += 1
        n = len(rows)
        return (
            f"已索引 {n} 张：\n"
            f"· 无关键词行 {no_tag} 张（{no_tag / n:.0%}）\n"
            f"· 无分级 {no_rating} 张（{no_rating / n:.0%}）\n"
            f"· 无季节 {no_season} 张（{no_season / n:.0%}，老库跑 /gallery polish 就地补）\n"
            f"· 平均关键词 {kw_total / n:.1f} 个/张"
        )

    async def overview(self) -> str:
        st = await self.store.stats()
        root = self.scanner.root()
        lines = [f"相册目录：{root or '（未配置）'}"]
        lines.append(
            f"登记 {sum(v for k, v in st.items() if k in ('pending', 'ok', 'failed'))} 张："
            f"已索引 {st.get('ok', 0)}、待索引 {st.get('pending', 0)}、失败 {st.get('failed', 0)}"
        )
        lines.append(f"已转向量 {st.get('embedded', 0)} 张")
        if ratings := st.get("ratings"):
            lines.append("分级：" + "　".join(f"{k} {v}" for k, v in list(ratings.items())[:8]))
        if folders := await self.store.folders():
            lines.append("分类：" + "、".join(folders[:10]) + ("…" if len(folders) > 10 else ""))
        if self.indexer.running:
            lines.append("⏳ 后台索引进行中" + (f"（{self.indexer.note}）" if self.indexer.note else ""))
        if self.embedder.running:
            lines.append("⏳ 后台向量转换进行中")
        return "\n".join(lines)

    async def reset(self) -> str:
        await self.store.reset_all()
        await self.app.vectors.rebuild_album()
        logger.info("[AstrLover] 相册库已清空重建。")
        return "整个相册库已清空（描述和向量一起没）。重新 /gallery scan 开始。"

    async def retry(self) -> str:
        n = await self.store.reset_fails()
        return f"失败计数已清零（{n} 张），跳过的图重新排队。"
