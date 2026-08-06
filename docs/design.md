# AstrLover 架构设计

> v2.0（2026-08-06）· 合并架构：presence 层（源自 astrbot_plugin_tg_presence v0.33，同作者）+ 生命模拟层。
> 需求编号（R/A/P）指向需求定义 ailover.md。

## 一、总体形态

单个 AstrBot 插件，**骑在默认对话管线上，不接管对话**。AstrBot 承载：对话历史、
会话人格、其他插件、函数工具、WebUI——本插件是管线上的增强层。

```
用户消息 → AstrBot 管线
   ├─ on_llm_request（本插件钩子）
   │    ├─ presence：图片登记/瘦身折叠、历史动态时间线注入、请求目录层描述
   │    └─ 生命层：注入她的"此刻"（人格/记忆/生活/情绪/时间/未提及事件）
   ├─ LLM（会话当前模型；她可调用 12 个工具：相册/照片/动态/头像/签名/
   │        表情回应/图片记忆×3/want_photo/generate_photo/send_voice）
   ├─ on_llm_response：presence 摘 <img_note>；生命层摘 <improv>/<told>/<found>
   └─ 正常发送（AstrBot 分段等设置照常生效）

心跳（纯代码，5min）：日程推进/情绪衰减/记忆沉淀/日记周记/主动意愿/生活冲动/排期执行
控制台 bot（插件自持 PTB 长轮询，只认管理员）：/umo /link /say /act /photo /moment
  /avatar /signature /gallery /vision /noreply /proactive + 生命层 /status /diary /events /plan /plans
```

## 二、代码组织

```
main.py                     全部 @filter 钩子/工具/指令的注册与薄委托
                            （AstrBot 按 handler 模块路径解析插件，装饰器必须在此）
astrlover/
├── presence/core.py        tg_presence v0.33 整体移植（类 PresenceCore，行为保真）
│                           相册/视觉解析/图片记忆/动态/头像签名/表情回应/控制台/静默/主动
├── presence/tag_schema.py  标签行校验与分词词典
├── app.py                  生命层装配中心 + 两个钩子实现（注入/摘标记）
├── console_ext.py          LifeConsoleMixin：生命层控制台指令 + run_console_line
├── config.py               生命层配置（读 presence 压平后的扁平 conf，单一配置体系）
├── persona/                档案（静态 profile.yaml + 动态 dynamic.yaml）与 prompt 组装
├── memory/                 沉淀管线（事实/小抄/日记/周记/召回+遗忘）+ chat_log 素材
├── life/                   时间感知/日程引擎/情绪半衰期
├── heart/                  心跳/意愿引擎/生活冲动
├── imagegen/               生图三后端（nanobanana/comfyui/novelai）+ 提示词构建
├── voice/                  TTS→ogg 语音条 / STT 兜底
├── tg/channel.py           频道评论区互动闭环
├── panel/ + pages/panel/   Web 面板（Plugin Pages + register_web_api）
├── store/                  SQLite(dao) + FAISS 记忆向量 + 导出
└── security.py             外部输入包裹防注入
最终类：AstrLover(LifeConsoleMixin, PresenceCore, Star)
```

## 三、关键设计决策

**D1 管线骑乘（放弃接管）**：对话由 AstrBot 默认管线处理；生命层只做 system prompt
注入与响应侧标记捕获。收益：对话历史/其他插件/工具生态/WebUI 全部保留；
代价：回复形态交给 AstrBot 的分段设置与工具（语音/照片是工具而非标记协议）。

**D2 双数据目录**：presence 层沿用 `astrbot_plugin_tg_presence/`（老用户上万张图的
索引是真金白银，直接复用）；生命层用 `astrlover/`。

**D3 控制台可扩展**：presence 控制台按模块级 `CONSOLE_ROUTES` 查名、`getattr(self, h)`
取方法——生命层指令注册进路由表、方法混进最终类即可，core 零改动。

**D4 定时编排 = 指令重放**：`/plan` 把一行控制台指令存入 pending_actions，
心跳到点经 `_console_run` 原样重放，回执回控制台。与手敲同一条路，零重复实现。

**D5 主动消息双模**：生命层意愿引擎（作息窗/纪念日/想炫耀/想念，纯代码打分）为默认；
presence 随机倒计时开启时意愿引擎让位。两者共用 `_proactive_fire`
（导演生成+投递+写回历史）与全部防打扰门槛（静默时段/连发未回/绑定目标）。
`_proactive_fire(extra_brief=…)` 是对 core 唯一的功能性修改：把"这次的缘由"带给她。

**D6 生活冲动经同一通道**：心跳掷签（冷却+特别日子加成）→ `run_console_line`
执行 `/moment` `/avatar` `/signature`（留空=她自己想），落地记入事件流
（内容/动机/提及状态，A2 双路径防穿帮）。

**D7 生图补相册**：`generate_photo` 工具仅在相册翻不到时用；锚点图保同一人
（persona/anchors/）；产物落相册目录 `aiimages/`，经 `/gallery scan+index` 回流。

**D8 恋人身份**：`life_partner_id`（缺省退化为控制台管理员）。只有恋人的对话进入
记忆素材与日记；其他会话仍能与她（bot 人格）对话，但注入提示要求按边界应对。

## 四、生命层数据模型（astrlover.db）

facts（可失效原子事实，含编造固化/身世播种）· diary（daily/weekly，UNIQUE(date,type)）·
events（内容/动机/提及状态三要素）· schedule（当日日程，日期播种随机保连续）·
mood（强度+半衰期）· cheatsheet（版本化小抄）· pending_actions（排期=控制台指令重放）·
chat_log（恋人对话素材，供日记/沉淀取材）· kvmisc。
向量：FAISS 单库（记忆），meta 带 type/ts，召回带时间衰减与"模糊"标注（A15）。

## 五、成本与安全

心跳纯代码；模型出场点：聊天回复、意愿过阈的主动消息（天级个位数）、日记 1/天、
周记 1/周、沉淀（空闲批处理，轻模型）、相册索引（一次性，独立视觉 API）。
安全：控制台/面板仅管理员（Dashboard JWT）；评论区外部文本 wrap_external 包裹
防注入；公开回复用不含私密记忆的精简人格提示；仓库与配置模板零密钥。

## 六、已知限制

见 README「已知限制」。要点：仅 Telegram；与 astrbot_plugin_tg_presence 互斥；
她收不到你的表情回应（适配器未订阅 message_reaction）；`astrbot_version >=4.26,<5`。
