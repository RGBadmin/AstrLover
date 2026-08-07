"""导出记忆包：换服务器，她还是她。

包里是「她记得的一切」——记录库（SQLite 快照）+ 记忆向量库 + 聊天图片存档。
解包到新机器的 data/plugin_data/astrlover/ 即可。

不在包里的：人设（在 AstrBot 人格设定里）、聊天记录（在 AstrBot 对话管理里）、
相册原图（在你自己的相册目录里，插件只存路径和描述）。
"""

import time
import zipfile
from pathlib import Path

from astrbot.api import logger


async def export_all(app) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    app.export_dir.mkdir(parents=True, exist_ok=True)
    out = app.export_dir / f"astrlover_export_{ts}.zip"

    # WAL 模式下不能直接拷文件，VACUUM INTO 出一份一致性快照
    snapshot = app.export_dir / f".db_snapshot_{ts}.db"
    snapshot.unlink(missing_ok=True)
    await app.db.conn.commit()
    await app.db.conn.execute(f"VACUUM INTO '{str(snapshot).replace(chr(39), chr(39) * 2)}'")

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, "astrlover.db")
            for sub in ("vec", "context_photos", "anchors"):
                base = app.data_dir / sub
                if not base.exists():
                    continue
                for p in base.rglob("*"):
                    if p.is_file():
                        zf.write(p, str(p.relative_to(app.data_dir)))
        logger.info(f"[AstrLover] 导出完成：{out.name}（{out.stat().st_size // 1024} KB）")
        return out
    finally:
        snapshot.unlink(missing_ok=True)
