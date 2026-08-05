# AstrLover 架构设计

> v1.0（2026-08-05）· 基于 ailover.md 需求定义 v0.1，目标平台 AstrBot v4.27+（Telegram）
> 需求编号（R1–R7 / A1–A16 / P1 情绪原则）均指向 ailover.md。

---

## 一、总体形态

AstrLover 是**单个 AstrBot 插件**（仓库即插件目录，克隆到 `data/plugins/AstrLover` 即可加载），内部按子系统分包。运行于 AstrBot 主进程，不引入独立服务；Web 面板复用 AstrBot 官方 Plugin Pages 机制。

```
┌────────────────────────── AstrBot 主进程 ──────────────────────────┐
│                                                                     │
│  Telegram 主 bot 实例 ──┐                       ┌── Telegram 上帝 bot 实例
│  (platform_id=A)        │                       │   (platform_id=B, 只认主人)
│                         ▼                       ▼                   │
│              ┌───────── main.py 事件路由(按 platform_id 分流) ─────┐ │
│              │                                                     │ │
│  ┌───────────┴──────────┐   ┌──────────────┐   ┌─────────────────┐│ │
│  │ chat 对话管线         │   │ god 上帝控制台│   │ panel Web面板    ││ │
│  │ (接管私聊,stop_event) │   │ (说/做/定时)  │   │ (Pages+web_api) ││ │
│  └───────────┬──────────┘   └──────┬───────┘   └─────────────────┘│ │
│              │      共 用 执 行 通 道 (actions)                     │ │
│  ┌───────────▼───────────────────────▼──────────────────────────┐ │ │
│  │ heart 心跳引擎(纯代码tick) → desire 意愿 → planner 轻模型决策  │ │ │
│  └───┬──────────┬──────────┬──────────┬──────────┬──────────────┘ │ │
│      ▼          ▼          ▼          ▼          ▼                │ │
│  persona    memory      life       events     gallery/imagegen    │ │
│  生命档案    四层记忆    虚拟生活    事件流     图库+生图           │ │
│      │          │          │          │          │                │ │
│  ┌───▼──────────▼──────────▼──────────▼──────────▼─────────────┐  │ │
│  │ store: SQLite(aiosqlite) + FaissVecDB×2(记忆/图库) + 文件区  │  │ │
│  │   data/plugin_data/astrlover/                                │  │ │
│  └──────────────────────────────────────────────────────────────┘  │ │
│      tg 原生能力封装(头像/签名/频道/相册) ← ExtBot(python-telegram-bot)│
│      voice(TTS→ogg语音条 / STT)  llm(主模型/轻模型/VLM 路由)         │
└─────────────────────────────────────────────────────────────────────┘
```

## 二、与 AstrBot 的集成点（均已源码核实）

| 用途 | API | 出处 |
|------|-----|------|
| 接管主人私聊 | `@filter.event_message_type(ALL, priority=高)` + `event.stop_event()` | core/star/filter |
| 主动消息 | `context.send_message(umo, MessageChain)`，umo=`platform_id:FriendMessage:chat_id` | core/star/context.py |
| 区分双 bot | `event.get_platform_id()`；umo 首段路由实例 | platform/astr_message_event.py |
| 原生 TG API | `context.get_platform_inst(id).client`（ExtBot，PTB≥22.6） | sources/telegram/tg_adapter.py |
| LLM 调用 | `provider.text_chat(prompt, contexts, system_prompt, model=...)`；`context.get_provider_by_id()` | core/provider/provider.py |
| 语音条 | `Comp.Record`（需 ogg/opus，官方镜像含 ffmpeg） | sources/telegram/tg_event.py |
| TTS/STT | `context.get_provider_by_id()` / `get_using_tts_provider(umo)` 等 | core/star/context.py |
| 向量检索 | 直接实例化 `FaissVecDB(doc_store_path, index_store_path, embedding_provider)` | core/db/vec_db/faiss_impl/vec_db.py |
| 后台任务 | `initialize()` 中 `asyncio.create_task`，`terminate()` 取消 | 官方插件指南 |
| Web 面板 | 插件根 `pages/<name>/index.html`（iframe+bridge）+ `context.register_web_api()` | dashboard/services/plugin_page_service.py |
| 数据目录 | `StarTools.get_data_dir()` → `data/plugin_data/astrlover/` | core/star/star_tools.py |
| KV 存储 | `self.put_kv_data / get_kv_data`（Star 基类） | v4.9.2+ |

**刻意不用的机制**：AstrBot Persona 体系（人格由生命档案系统自行组装 system prompt）、默认对话管线（主人私聊完全接管，保证上下文与回复形态的完全控制）、`add_active_job` 主动 Agent（自建心跳，成本可控）。

## 三、目录结构

```
AstrLover/
├── main.py                  # Star 入口：装配子系统、事件路由、生命周期
├── metadata.yaml            # name: astrlover
├── requirements.txt
├── _conf_schema.json        # 系统级配置（接线+防打扰最小集合）
├── pages/panel/index.html   # Web 管理面板（单文件 SPA，经 bridge 调插件 API）
├── examples/persona.example.yaml  # 生命档案模板（首启复制到数据目录）
├── docs/design.md           # 本文档
└── astrlover/
    ├── config.py            # 配置封装与校验
    ├── llm.py               # 模型路由：主模型/轻模型/VLM，统一入口与重试
    ├── security.py          # 外部输入包裹、防注入
    ├── actions.py           # 统一执行通道：自主行为与上帝编排共用
    ├── scheduler.py         # 延时/定时任务（自持久化，重启恢复）
    ├── store/
    │   ├── db.py            # aiosqlite 连接与迁移
    │   ├── dao.py           # 各表 DAO
    │   ├── vectors.py       # FaissVecDB 封装（memory / gallery 两库）
    │   └── export.py        # 备份导出（档案+记忆包）
    ├── persona/
    │   ├── profile.py       # 静态基线加载(YAML)
    │   ├── dynamic.py       # 动态层：编造固化(A6)/外观状态(A9)/关系阶段
    │   └── prompt.py        # system prompt 组装器（含 P1 永不清单）
    ├── memory/
    │   ├── working.py       # 工作记忆（自管对话窗口）
    │   ├── cheatsheet.py    # 核心小抄（她自修订）
    │   ├── facts.py         # 结构化事实（沉淀/更新/失效）
    │   ├── episodic.py      # 日记/周记 + 向量召回 + 衰减(A15)
    │   └── pipeline.py      # 对话后沉淀
    ├── life/
    │   ├── clock.py         # A5 时间感知：时区/节日/纪念日/认识天数
    │   ├── schedule.py      # A4 日程生成与推进
    │   ├── state.py         # "此刻在干什么"状态机
    │   ├── npc.py           # A8 社交圈
    │   └── mood.py          # P1 情绪状态+半衰期
    ├── events/stream.py     # A2 生活事件流（内容/动机/提及状态）
    ├── heart/
    │   ├── heartbeat.py     # 心跳循环（纯代码，默认5分钟tick）
    │   ├── desire.py        # A3 意愿计算+防打扰三参数
    │   └── planner.py       # 轻模型决策（主动消息/换头像/发动态…）
    ├── chat/
    │   ├── handler.py       # 主 bot 私聊入口：STT→组装→调用→编排
    │   └── composer.py      # 回复标记协议解析：分段/表情包/语音/图片
    ├── gallery/
    │   ├── ingest.py        # 批量入库+VLM结构化打标(R4)
    │   ├── search.py        # 情境语义检索(R5)
    │   └── stickers.py      # A7 表情包
    ├── imagegen/
    │   ├── base.py          # 适配器接口+降级链
    │   ├── nanobanana.py    # Gemini 生图（支持参考图保一致性）
    │   ├── comfyui.py       # 云 ComfyUI（workflow 模板可配）
    │   ├── novelai.py       # NovelAI
    │   └── prompt_builder.py# R6 外观基准+当前状态+情境 → 提示词
    ├── voice/
    │   ├── tts.py           # TTS→ffmpeg 转 ogg/opus→Record
    │   └── stt.py
    ├── tg/
    │   ├── profile.py       # R2 换头像(set_my_profile_photo)/改签名
    │   ├── channel.py       # 频道发帖(单图/相册 send_media_group)/评论互动
    │   └── reactions.py     # 表情回应感知（尽力而为，见风险#3）
    ├── god/
    │   ├── handler.py       # R7 意图解析：说/做/定时/查状态/改配置/看日记
    │   └── status.py
    └── panel/api.py         # register_web_api 各端点
```

## 四、关键设计决策

### D1 对话完全接管（而非挂在默认管线上）
主 bot 上来自主人的私聊消息由 `chat/handler.py` 全权处理并 `stop_event()`：
上下文组装（档案+时间+生活状态+情绪+小抄+召回记忆+未提及事件）→ 主模型 → 标记协议解析 → 多形态回复（分段文字/语音/图/表情包）→ 记忆沉淀。
其他人发给主 bot 的私聊：礼貌拒绝（她是"专一的"）；上帝 bot 上非主人消息：静默忽略。

### D2 回复标记协议（provider 无关，不依赖 function calling）
主模型在 system prompt 约定下输出带轻量标记的回复，composer 解析执行：
- `<seg/>` 分段（模拟真人多条短消息，含打字延迟节奏）
- `<voice>…</voice>` 该段转语音条
- `<sticker>情绪/语境描述</sticker>` 表情包语义检索
- `<photo>情境需求描述(场景/状态/情绪/构图)</photo>` R5 检索→不足降级生图
- `<recall …/>`、`<fact …/>` 等由沉淀管线处理的内部标记
解析失败时整体降级为纯文本发送，永不吞消息。

### D3 心跳引擎：纯代码 tick + 轻模型按需决策（成本意识）
默认每 5 分钟 tick 一次，全部为纯代码：推进日程、更新"正在做什么"、情绪半衰期衰减、检查日记/周记到点、计算主动意愿分。
**只有**意愿分过阈值且防打扰三参数放行时，才调轻模型做一次"要不要/说什么/什么形式"的决策生成。日记每天 1 次、周记每周 1 次。挂机一天的模型调用可数得过来。

### D4 事件流是拟真三支柱的枢纽（A2）
一切自主行为（换头像/改签名/发动态/主动消息/剪头发…）统一经 `actions.py` 执行，执行成功即写入事件流（内容描述+真实动机+提及状态），并成为：日记素材、聊天可提及话题、"被我发现"的应答依据。上帝编排走同一通道，故"她恰好做了我想让她做的事"。

### D5 图库与生图共用"情境需求描述"这一中间语言（R5）
选图不检索字面，而是先由主模型产出下一幕的情境需求描述（场景/人物状态/情绪/构图/类别），
再: ① FAISS 语义检索（打标文本与需求描述同一语言空间，入库怎么描述检索就怎么问）→ ② 相似度不足降级生图（同一份描述交给 prompt_builder）→ ③ 生成图回流入库。
外观一致性：NanoBanana 走参考图（外观锚点图，从图库标记）、ComfyUI 走用户提供的 workflow（可内嵌 LoRA/IPAdapter）、NovelAI 走角色描述词；外观演变状态（A9）注入所有路径并作为检索过滤条件。

### D6 双库 FAISS
- `memory` 库：日记、事实、事件摘要的语义召回；
- `gallery` 库：图片打标文本检索；
复用 AstrBot 自带 faiss 依赖与 Embedding Provider，不引入新向量服务。

### D7 定时任务自持久化
上帝编排的"定时"与她自己的"打算"（如"晚上发动态"）写入 `pending_actions` 表（due_at+payload），由心跳扫描执行；不依赖 cron_manager 的内存 handler（重启即失效的问题），重启后天然恢复。

### D8 安全边界
- 频道评论/讨论组等外部文本一律经 `security.wrap_external()` 包裹为"她读到的内容"，附防注入护栏说明，绝不进入 system prompt 层；
- 上帝 bot 与面板等同上帝权限：上帝 bot 仅认 `owner_id`，面板在 AstrBot Dashboard JWT 鉴权之后；
- 仓库/代码零密钥，一切凭据在 AstrBot 配置或插件配置中。

## 五、数据模型（SQLite）

| 表 | 用途 | 关键字段 |
|----|------|---------|
| chat_log | 自管对话历史（工作记忆底层） | ts, role, kind(text/voice/photo/sticker), content, meta |
| facts | 结构化事实（A1/A6） | subject(user/self/npc:小雅), key, content, status(active/expired), confidence, ts |
| diary | 日记/周记（A1） | date, type(daily/weekly), content, mood, vec_id |
| events | 生活事件流（A2） | ts, kind(avatar/sign/post/proactive/appearance/…), description, motivation, mention_status(未提及/已主动讲/已被发现), meta |
| schedule | 日程（A4） | date, start, end, activity, status(planned/ongoing/done), notes |
| mood | 情绪状态（P1） | kind, intensity, cause, started_at, half_life_min |
| cheatsheet | 核心小抄（版本化） | version, content, updated_at, reason |
| relationship | 关系状态与里程碑（A12） | stage, anniversary dates, milestones(json) |
| gallery | 图库（R4） | file, category(自拍/生活照/场景图/表情包), tags(json), desc, appearance(json), source(user/gen), vec_id, last_used |
| pending_actions | 待执行动作（D7） | due_at, kind, payload(json), status, source(self/god) |
| kv 杂项 | 计数器/游标类 | 经 Star KV API |

文件区：`persona/profile.yaml`（静态基线）、`persona/dynamic.yaml`（动态层）、`gallery/files/`、`voice/cache/`、`vec/`（FAISS 两库）、`exports/`。

## 六、配置（_conf_schema.json 最小集合）

接线：主/上帝 bot 平台实例 id、主人 user id、频道 id、讨论组 id、时区；
模型：主对话 provider id（空=会话当前）、轻量决策 provider id、VLM provider id（空=主模型）、Embedding provider id、TTS/STT provider id；
生图：后端优先级列表 + nanobanana{key,base_url,model} + comfyui{base_url,workflow} + novelai{key,model}；
防打扰（A3 仅此三个行为参数）：最小触发间隔、最大沉默时限、未回停发条数；
系统：心跳间隔、调试日志开关。
**不设任何"表现强度"旋钮**——粘人程度/脾气/口吻全部由生命档案推导（原则 3）。

## 七、里程碑

M0 骨架与本文档 → M1 存储+档案+对话管线 → M2 四层记忆 → M3 虚拟生活+心跳+主动 → M4 事件流+头像签名+频道 → M5 图库+生图 → M6 语音 → M7 上帝 bot → M8 面板 → M9 安全/导出/文档/推送 GitHub(AstrLover)。

每个里程碑：模块代码 + 纯逻辑单元测试（不依赖 AstrBot 运行时的部分）；集成联调在 Debian 服务器实例上按 README 步骤进行。

## 八、已识别风险与对策

1. **Telegram 发送端相册**：适配器未实现 → 频道发帖不走适配器，直接 `client.send_media_group`；私聊多图逐条发。
2. **语音格式**：TTS 输出需 ogg/opus → ffmpeg 转码（官方镜像自带 ffmpeg；缺失时降级发文件并告警）。
3. **Reaction 感知**：PTB 需订阅 `message_reaction` 更新且 AstrBot 适配器未注册 → 尽力而为：向适配器 application 注册补充 handler；不可行则降级为"仅感知评论互动"，不影响主体验。
4. **换头像频率限制**：Telegram 对 bot 资料修改有速率限制 → 天级冷却本来就是需求（R2），自然规避。
5. **AstrBot 内部 API 变动**（ExtBot 直用、FaissVecDB 直用为非公开承诺 API）→ 集中封装在 tg/、store/vectors.py，破坏性变更只改一层；metadata 声明 `astrbot_version: ">=4.16,<5"`。
6. **主模型输出标记不规范** → composer 容错解析+纯文本降级（D2）。
