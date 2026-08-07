# AstrLover 架构设计

> 面向维护者。用户视角的说明在 README。

## 一、形态

单个 AstrBot 插件，**骑在默认对话管线上，不接管对话**。AstrBot 承载对话历史、
会话人格、其他插件、函数工具与 WebUI；本插件在管线上做三件事：

```
用户消息 → AstrBot 管线
   ├─ on_llm_request（App.on_llm_request 一次做完，顺序自控）
   │    ① 图片登记落盘 + 异步派发细节层解析
   │    ② 请求目录层描述（她在回复末尾附 <img_note>）
   │    ③ recall 的原图放回本轮
   │    ④ 历史动态按时间戳插进对话时间线（_no_save）
   │    ⑤ 上下文图片折叠（编号已分配，最后做）
   │    ⑥ 生命层：她的此刻（时间/日程/心情）+ 记忆 + 未提及的事
   ├─ LLM（会话当前模型；12 个工具由她自己决定何时调用）
   └─ on_llm_response：摘 <img_note> 存档、摘生命层内部标记，剥干净再发出

心跳（纯代码，默认 5min）：排期执行 → 日程推进 → 记忆沉淀 → 日记周记
                          → 主动意愿 → 生活冲动
导演 bot（插件自持 PTB 长轮询，只认管理员）：与 AstrBot 平台系统无关
```

注入优先走 `extra_user_content_parts` + `mark_as_temp()`（贴着当前 user turn，
且不落进 conversation history），拿不到 TextPart 时回退 `system_prompt`。

## 二、模块

```
main.py                  仅 @filter 注册与薄委托（钩子/12 个工具/静默拦截）
astrlover/
├── app.py               装配中心 + 管线钩子实现 + 控制台委托（gallery/vision/status）
├── config.py            生命层配置视图（读扁平 conf；presence 侧直接读 star_conf）
├── tools.py             LLM 工具实现体
├── actions.py           排期执行 = 控制台指令重放
├── vision/              client（三格式/失败四分类/熔断）· validate（输出校验）
│                        tags（分级/季节/标签行/分词词典）· tag_schema（标签候选值）
├── album/               store（DAO）· scan（.archive 分类 + snowflake 时间）
│                        index（后台索引 + 批次报告）· embed（四段向量 + 区分度探测）
│                        search（IDF 词面 + 语义 + 分层确定性排序）· service（门面/维护）
├── photos/              archive（sha 去重 + 编号 + 两层描述）· memory（三段钩子逻辑）
│                        sender（g123/#3 解析与发送记账）
├── presence/            moments（发布/时间线注入）· profile（头像/签名/表情）· limits（频控）
├── director/            bot（PTB 传输层）· console（命令）· bridge（说话/写历史/按人格生成）
├── heart/               heartbeat（心跳）· proactive（意愿式主动）· impulses（生活冲动）
├── life/                clock（时间感知）· engine（日程）· mood（情绪半衰期）
├── memory/              pipeline（事实/小抄/日记/周记/召回）· working（对话素材）
├── persona/             profile（生命参数）· dynamic（演化状态）· prompt（生命块组装）
├── imagegen/            三后端 + 提示词构建（锚点图保一致性）
├── voice/               TTS → ogg 语音条
├── panel/ + pages/panel Web 面板（Plugin Pages + register_web_api）
├── store/               db（单库 schema）· dao · vectors（memory + album 两库）· export
└── security.py          外部输入包裹防注入
```

## 三、关键决策

**D1 管线骑乘**：不接管对话，只注入与捕获。代价是回复形态交给 AstrBot
（语音/照片是工具而非自定义标记协议），收益是整个生态与数据都在原位。

**D2 全 async 单库**：所有存储走 aiosqlite 的同一个 `astrlover.db`（WAL）。
没有同步 sqlite3 阻塞事件循环，没有 state.json 全量重写，备份就是拷一个文件。

**D3 失败四分类是视觉层的核心**：配置错中止整批、上游故障常规重试、
生成中被拦独立预算（采样随机，重试常有效）、输入侧判死独立预算（基本无效）。
后两种都是 HTTP 200 空正文且照常计费，必须自己记账并在批次报告里摊开。

**D4 检索确定性**：词面 IDF 不取交集 + 语义四段取最大，合并后按
「明说条件 > 默认偏好 > 匹配度 > 时间 > id」分层排序，同分按 id 兜底——
同一段词每次必须搜出同一批，否则"上次那张"会在多次检索间漂移。

**D5 图片编号 = photo_archive 主键**：sha256 去重保证同图同号；
静默期从消息文件读回 base64 走同一个登记函数，两条路径算出同一个编号。

**D6 导演桥必须写回历史**：她说过的话要进 conversation history，
否则下一轮她不知道自己说过。写回时给她自己的消息打时间戳（对方的消息
AstrBot 本来就带时间，不重复打）；她模仿上下文自写的时间戳在生成后剥掉。

**D7 排期 = 指令重放**：`/plan` 存一行控制台指令，心跳到点交给
`console.handle()` 原样执行，回执发回原控制台会话。零重复实现。

**D8 频控只约束自主行为**：手动指令随时可用、不消耗配额、不重置计时；
触发限制时返回给她一句能读懂的话，她就不会反复重试。

**D9 人设只有一个来源**：她是谁、什么性格、怎么说话、有哪些朋友全部由
AstrBot 人格设定负责；插件的生命块只注入人格写不了的——此刻（时间/日程/心情）、
记忆（小抄/日记/召回）、她做过但他不知道的事、以及 P1 铁律（产品承诺，
不让人格覆盖）。life.yaml 只存代码消费的结构化字段：name/call_me、
生日与纪念日、外观基准（生图锚）、身世条目（播种进事实层）、作息（心跳地基）。

**D10 生命层可整个关掉**：`life_enabled=false` 时 `App.ready=False`，
presence 能力照常工作；生图在无生命参数时退化为纯情境描述。

## 四、数据模型（astrlover.db）

`album_images`（相册：路径/分类/拍摄时间/描述/分级/季节/状态/失败数/向量标记/发送记录）
`photo_archive`（聊天图片：sha/文件/首见时间/目录层/细节层）
`facts`（可失效原子事实）· `diary`（daily/weekly）· `events`（内容/动机/提及状态）
`schedule`（当日日程）· `mood`（强度+半衰期）· `cheatsheet`（版本化小抄）
`pending_actions`（排期）· `chat_log`（恋人对话素材）· `kvmisc`（游标/计数/动态列表）

向量：FAISS 双库——`memory`（事实/日记，带 ts 做时间衰减）与
`album`（每图四段，meta={img, seg}）。

## 五、测试

`python -m pytest tests` —— 54 项，不需要安装 AstrBot：
`conftest.py` 提供 astrbot 桩模块，`test_smoke_app.py` 用假 Context 真正启动
App 跑通装配、钩子注入、相册扫描检索、图片折叠、控制台命令与全部降级路径。
