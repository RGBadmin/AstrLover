"""相册检索：词面（IDF 加权）+ 语义（四段取最大）取并集，再确定性重排。

两条要点：
- 词面不取交集。「酒店里穿灰丝踩红底细高跟」拆六七个词，一个词对不上
  （你说"足底"库里写"脚底"）交集就空了；打分则是漏词只降排名。
- 排序完全确定，同分按 id 兜底。同一段词每次搜出来必须是同一批，
  否则"上次那张"会在多次检索间漂移；变化只来自发送历史（可控）。

时间/季节/尺度是分层排序键，不是加权：
  他明说的条件 > 默认偏好 > 匹配度 > 文件时间新 > id
"""

import math
import re
import time
from dataclasses import dataclass, field

from ..vision.tags import (
    GRAM_MIN_LEN,
    RATING_TIERS,
    SEASON_ANY,
    SEASON_OF_MONTH,
    TAG_VOCAB,
    rating_wants,
    season_wants,
)

_CJK = re.compile(r"[一-鿿]+")
_ASCII = re.compile(r"[A-Za-z0-9]+")


def segment(query: str) -> list[str]:
    """中文不写空格：先按标签词典正向最大匹配，切不动的退二元滑窗。"""
    words: list[str] = []
    for chunk in re.split(r"[\s，,、。;；]+", query.strip()):
        if not chunk:
            continue
        for m in _ASCII.finditer(chunk):
            if len(m.group(0)) >= 2:
                words.append(m.group(0))
        for cjk in _CJK.findall(chunk):
            i = 0
            hit_any = False
            while i < len(cjk):
                for size in range(min(6, len(cjk) - i), 1, -1):
                    piece = cjk[i:i + size]
                    if piece in TAG_VOCAB:
                        words.append(piece)
                        i += size
                        hit_any = True
                        break
                else:
                    i += 1
            if not hit_any and len(cjk) >= 2:
                if len(cjk) <= GRAM_MIN_LEN:
                    words.append(cjk)
                else:
                    words.extend(cjk[j:j + 2] for j in range(len(cjk) - 1))
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


@dataclass
class SearchReport:
    words: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    split_note: str = ""
    lexical_hits: int = 0
    semantic_hits: int = 0
    warning: str = ""

    def text(self) -> str:
        lines = [f"切词：{'、'.join(self.words) or '（空）'}"]
        if self.split_note:
            lines.append(self.split_note)
        lines.append(f"候选（词面命中 {self.lexical_hits} · 语义召回 {self.semantic_hits}）")
        if self.warning:
            lines.append(self.warning)
        return "\n".join(lines)


class AlbumSearch:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------
    async def search(
        self,
        keywords: str = "",
        want: str = "",
        *,
        folder: str = "",
        rating: str = "",
        season: str = "",
        around: str = "",
        prefer_sent: str = "fresh",
        top_k: int = 10,
        fetch_k: int = 60,
    ) -> tuple[list[dict], SearchReport]:
        app = self.app
        report = SearchReport()
        query_text = (want or "").strip() or keywords.strip()
        words = segment(keywords or want)
        report.words = list(words)

        # ---- 词面：IDF 加权打分 ----
        total_rows = await app.album.all_ok()
        total = len(total_rows) or 1
        scores: dict[int, float] = {}
        used: list[str] = []
        for w in words:
            ids = await app.album.ids_like(w)
            if not ids and len(w) > 1:  # 整词一张都没匹配上→拆单字重试
                for ch in w:
                    sub = await app.album.ids_like(ch)
                    if sub:
                        report.split_note = f"实际用：{ch}（{w} 一张都没匹配上，已拆成单字）"
                        ids = sub
                        w = ch
                        break
            if not ids:
                continue
            used.append(w)
            idf = math.log(1 + total / max(1, len(ids)))
            for i in ids:
                scores[i] = scores.get(i, 0.0) + idf
        report.used = used
        report.lexical_hits = len(scores)

        # ---- 语义：四段取最大相似度 ----
        sem: dict[int, float] = {}
        if query_text:
            hits = await app.vectors.search_album(query_text, k=fetch_k)
            for h in hits:
                iid = h["meta"].get("img")
                if isinstance(iid, int):
                    sem[iid] = max(sem.get(iid, 0.0), h["similarity"])
            report.semantic_hits = len(sem)
            if not sem and not app.vectors.available:
                report.warning = "⚠ 语义检索不可用（Embedding 未配置或向量未转）"

        cand_ids = set(scores) | set(sem)
        if not cand_ids:
            return [], report
        rows = await app.album.by_ids(list(cand_ids))

        # ---- 过滤 + 分层排序 ----
        want_tiers = self._tiers(rating)
        want_seasons = self._seasons(season)
        default_season = not want_seasons and bool(
            app.conf.get("season_prefer_now", True)
        )
        now_season = SEASON_OF_MONTH.get(app.clock.now().month, "") if app.clock else ""
        month_key = self._month(around)
        recent_window = int(app.conf.get("sent_recent_days", 30) or 30) * 86400
        now = time.time()

        scored = []
        for iid, row in rows.items():
            if folder and row["folder"] != folder:
                continue
            if want_tiers and not (RATING_TIERS.get(row["rating"], set()) & want_tiers):
                continue
            lex = scores.get(iid, 0.0)
            semv = sem.get(iid, 0.0)
            match = lex + semv * 2.0  # 语义与词面同量级
            # 分层键（大在前）
            month_ok = 1 if (month_key and self._in_month(row["shot_ts"], month_key)) else 0
            sent_ok = self._sent_rank(row, prefer_sent, recent_window, now)
            season_rank = self._season_rank(row["season"], want_seasons, default_season, now_season)
            scored.append((
                month_ok, sent_ok, season_rank, round(match, 6), row["shot_ts"], -iid, row, match
            ))

        scored.sort(key=lambda t: (-t[0], -t[1], -t[2], -t[3], -t[4], t[5]))
        out = []
        for item in scored[:top_k]:
            row = dict(item[6])
            row["_score"] = item[7]
            out.append(row)
        return out, report

    # ------------------------------------------------------------------
    @staticmethod
    def _tiers(rating: str) -> set[str]:
        r = (rating or "").strip()
        if not r:
            return set()
        if r in RATING_TIERS:
            return set(RATING_TIERS[r])
        syn = rating_wants(r)
        return set(syn) if syn else set()

    @staticmethod
    def _seasons(season: str) -> set[str]:
        s = (season or "").strip()
        if not s or s.lower() == "now":
            return set()
        syn = season_wants(s)
        if syn:
            return set(syn)
        return {ch for ch in s if ch in "春夏秋冬"}

    @staticmethod
    def _season_rank(img_season: str, want: set[str], default_now: bool, now_season: str) -> int:
        """三档：合季 2 > 没标出 1 > 明显不合季 0。不知道不等于不合适。"""
        if not img_season:
            return 1
        if want:
            if img_season == SEASON_ANY:
                return 1  # 点名要春秋装时，「四季」不是春秋装，降到中间
            return 2 if set(img_season) & want else 0
        if not default_now or not now_season:
            return 1
        if img_season == SEASON_ANY:
            return 2  # 默认挑当季时，随时能发的算合适
        return 2 if now_season in img_season else 0

    @staticmethod
    def _sent_rank(row: dict, prefer: str, window: int, now: float) -> int:
        recent = row["last_sent_ts"] and (now - row["last_sent_ts"] < window)
        if (prefer or "fresh").strip() == "recent":
            return 1 if recent else 0
        return 0 if recent else 1  # fresh：没发过或早过窗口的优先

    @staticmethod
    def _month(around: str) -> str:
        a = (around or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}", a):
            return a
        if re.fullmatch(r"\d{1,2}", a):
            return f"-{int(a):02d}"
        return ""

    @staticmethod
    def _in_month(ts: int, key: str) -> bool:
        if not ts:
            return False
        stamp = time.strftime("%Y-%m", time.localtime(ts))
        return stamp == key if len(key) == 7 else stamp.endswith(key)
