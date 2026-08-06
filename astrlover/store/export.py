"""数据导出（数据主权）：换服务器，她还是她。

导出格式 = 人格档案（静态+动态）+ 记忆包（SQLite 快照 + 向量库）
+ 可选图库文件。解包到新机器的 data/plugin_data/astrlover/ 即完成迁移。
"""

import time
import zipfile
from pathlib import Path

from astrbot.api import logger


async def export_all(app, include_gallery: bool = True) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = app.export_dir / f"astrlover_export_{ts}.zip"
    app.export_dir.mkdir(parents=True, exist_ok=True)

    # SQLite 一致性快照（WAL 下不能直接拷文件）
    snapshot = app.export_dir / f".db_snapshot_{ts}.db"
    await app.db.conn.commit()
    escaped = str(snapshot).replace("'", "''")
    await app.db.conn.execute(f"VACUUM INTO '{escaped}'")

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, "astrlover.db")
            for sub in ("persona", "vec"):
                base = app.data_dir / sub
                if base.exists():
                    for p in base.rglob("*"):
                        if p.is_file():
                            zf.write(p, str(p.relative_to(app.data_dir)))
            if include_gallery:
                # 相册原图在用户自己的目录里，不进导出包；这里只带聊天图片存档
                ctx = app.data_dir / "context_photos"
                if ctx.exists():
                    for p in ctx.rglob("*"):
                        if p.is_file():
                            zf.write(p, str(p.relative_to(app.data_dir)))
        logger.info(f"[AstrLover] 导出完成：{out.name}（{out.stat().st_size // 1024} KB）")
        return out
    finally:
        snapshot.unlink(missing_ok=True)
