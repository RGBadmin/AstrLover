"""相册后台索引：把图逐张交给视觉模型读成文字。

- 并发受信号量约束；配置错立刻中止整批（不浪费一夜调用）；
- 重试用尽不算图片自己的失败（上游的锅不让图片背）；
- 拒答/思维链/截断/过短按图片失败累计，到上限跳过；
- 批次报告把账算清：调用次数、被拦次数、重试救回几张。
"""

import asyncio
import time

from astrbot.api import logger

from ..vision import validate
from ..vision.client import ConfigError, GenBlocked, InputBlocked, UpstreamError
from ..vision.tags import parse_tag_line, scrub_tag_line

VISION_MAX_FAILS = 3
_IDLE_ROUNDS = 20  # 后台空转多少轮（无可做任务）才收工


class AlbumIndexer:
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

    # ------------------------------------------------------------------
    async def _run(self, count: int | None, progress_cb) -> str:
        app = self.app
        vision = app.vision
        if not vision.ready():
            return "视觉 API 未配置（vision 组三项：地址/Key/模型）"
        subject = str(app.star_conf.get("subject_name") or "").strip()
        end_mark = str(app.star_conf.get("vision_end_mark") or "").strip()
        min_chars = int(app.star_conf.get("vision_min_chars", 0) or 0)
        max_chars = max(100, int(app.star_conf.get("vision_max_chars", 600) or 600))

        stat0 = (vision.stats.calls, vision.stats.blocked, vision.stats.hard, vision.stats.saved)
        done = failed = 0
        idle = 0
        last_report = time.time()
        try:
            while count is None or done + failed < count:
                batch_limit = 8 if count is None else min(8, count - done - failed)
                rows = await app.album.next_pending(VISION_MAX_FAILS, limit=batch_limit)
                if not rows:
                    idle += 1
                    if count is not None or idle >= _IDLE_ROUNDS:
                        break
                    await asyncio.sleep(30)
                    continue
                idle = 0

                async def one(row):
                    nonlocal done, failed
                    path = app.album.abs_path(row["path"])
                    if path is None:
                        await app.album.mark_fail(row["id"], own_fault=True)
                        failed += 1
                        return
                    async with vision.gate():
                        try:
                            text, last = await vision.describe(str(path))
                        except ConfigError:
                            raise  # 配置错整批中止
                        except (UpstreamError, GenBlocked, InputBlocked) as e:
                            own = isinstance(e, (GenBlocked, InputBlocked))
                            # 生成中被拦/判死是对这张图的判定 → 算图片失败；
                            # 上游故障重试用尽不算
                            await app.album.mark_fail(row["id"], own_fault=own)
                            failed += 1
                            self.note = str(e)[:120]
                            return
                    text, complete = validate.cut_at_end_mark(text, end_mark)
                    reason = "" if complete else "结束标记缺失（像是被截断）"
                    if not reason and vision.truncated(
                        str(app.star_conf.get("vision_api_format") or "openai"), last
                    ):
                        reason = "被 max_tokens 掐断"
                    if not reason:
                        reason = validate.junk_reason(text, min_chars, max_chars)
                    if reason:
                        await app.album.mark_fail(row["id"], own_fault=True)
                        failed += 1
                        self.note = reason
                        return
                    text = scrub_tag_line(text, subject)[:max_chars]
                    tags = parse_tag_line(text)
                    await app.album.mark_ok(row["id"], text, tags["rating"], tags["season"])
                    done += 1

                await asyncio.gather(*(one(r) for r in rows))
                if progress_cb and time.time() - last_report > 60:
                    last_report = time.time()
                    try:
                        await progress_cb(f"索引进行中：本轮已成 {done}、失败 {failed}")
                    except Exception:
                        pass
        except ConfigError as e:
            return f"配置错误，整批中止：{e}"
        except asyncio.CancelledError:
            pass

        calls = vision.stats.calls - stat0[0]
        blocked = vision.stats.blocked - stat0[1]
        hard = vision.stats.hard - stat0[2]
        saved = vision.stats.saved - stat0[3]
        report = (
            f"索引结束：成功 {done} 张，失败 {failed} 张。\n"
            f"API 调用 {calls} 次"
            + (f"，其中 {blocked + hard} 次被内容策略拦掉"
               f"（生成中 {blocked} / 输入侧 {hard}，这些上游算成功照常计费）" if blocked + hard else "")
            + (f"；重试救回 {saved} 张" if saved else "")
        )
        logger.info(f"[AstrLover] {report}")
        return report
