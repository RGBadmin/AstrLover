"""上帝视角状态报告（R7 配置台之"查看她的运行状态"）。纯代码，零 token。"""

import time
from datetime import datetime


async def build_status(app) -> str:
    lines = ["📋 AstrLover 运行状态", ""]

    # 她的此刻
    lines.append(f"🕒 {app.clock.describe_now(app.profile.met_on, app.profile.anniversary)}")
    if app.life:
        cur = await app.life.current_activity()
        lines.append(f"🧍 此刻：{cur}" + ("（睡眠时段）" if app.life.sleeping_now() else ""))
        sched = await app.dao.day_schedule(app.clock.today_str())
        if sched:
            lines.append("📅 今日：" + "；".join(f"{s['start_hm']} {s['activity']}[{s['status']}]" for s in sched))
    if app.mood:
        mood = await app.mood.prompt_text()
        lines.append(f"💭 {mood or '心情平静。'}")

    # 互动状态
    last_user = await app.dao.kv_get("last_user_ts", 0) or 0
    unanswered = await app.dao.kv_get("proactive_unanswered", 0) or 0
    if last_user:
        ago = int((time.time() - last_user) / 60)
        lines.append(f"💬 距他上次说话：{ago} 分钟；主动未回计数：{unanswered}/{app.cfg.max_unanswered}")

    # 模块健康
    checks = []
    checks.append(("对话模型", app.llm.chat_provider() is not None))
    checks.append(("轻量模型", app.llm.light_provider() is not None))
    checks.append(("向量库", app.vectors.available or not app.vectors._init_failed))
    checks.append(("生图", bool(app.imagegen and app.imagegen.available)))
    checks.append(("TTS", bool(app.voice and app.voice.tts_ready)))
    checks.append(("STT", bool(app.voice and app.voice._stt_provider() is not None)))
    checks.append(("频道", app.tgsvc is not None and app.tgsvc.channel_chat() is not None))
    lines.append("🔌 " + "  ".join(f"{name}{'✅' if ok else '❌'}" for name, ok in checks))

    # 图库
    stats = await app.dao.gallery_stats()
    if stats:
        lines.append("🖼 图库：" + "  ".join(f"{k}:{v}" for k, v in sorted(stats.items())))
    else:
        lines.append("🖼 图库：空（把图片放进 data/plugin_data/astrlover/gallery/files 后发 /scan）")

    # 待办
    pending = await app.dao.pending_list(10)
    if pending:
        lines.append("⏰ 待办：")
        for p in pending:
            due = datetime.fromtimestamp(p["due_ts"]).strftime("%m-%d %H:%M")
            lines.append(f"  #{p['id']} [{p['kind']}] {due} {str(p['payload'])[:40]}")

    caps = "、".join(sorted(app.capabilities())) or "无"
    lines.append(f"✨ 当前解锁能力：{caps}")
    return "\n".join(lines)
