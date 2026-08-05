# AstrLover 💞

拟真 AI 恋人——有人格、有记忆、有作息、过着自己生活的数字恋人。基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 Telegram 插件。

> **像真人一样生动，像 AI 一样无害。**
> 她不是"问答机器人加了人设"：昨天聊过的事她今天记得，她自己做过的事她知道，
> 她的生活在你不在的时候也在继续。

## 她能做什么

| 能力 | 说明 |
|------|------|
| 🧬 生命档案 | YAML 人格档案（身份/外观/性格/身世/社交圈/作息/关系），静态基线与动态演化分离，换档案不丢"你们的过去" |
| 🧠 四层记忆 | 工作记忆 → 核心小抄（她自己修订）→ 结构化事实（可更新可失效）→ 日记/周记（第一人称情景记忆，语义召回+适度遗忘） |
| 🌊 虚拟生活 | 按作息生成每日日程、"此刻在干什么"状态机、洗澡/睡觉时晚回消息、叙事连续 |
| 💌 主动消息 | 早安晚安/饭点关心/话题延续/想炫耀/想念——由"意愿"驱动而非定时器；防打扰三参数兜底 |
| 😊 情绪引擎 | 情绪带半衰期自动消散，绝不累积；哄一句立刻雨过天晴；只有"可爱系"表达，永不指责 |
| 🖼 图库+生图 | 图片 VLM 结构化打标 + FAISS 语义检索；"下一幕"情境推理选图；不够贴切自动降级生图（NanoBanana/云ComfyUI/NovelAI），生成图回流图库 |
| 😜 表情包 | 按语境语义甩表情包、接梗斗图 |
| 🔄 拟真存在 | 自主换头像、改签名、发频道动态（她的朋友圈）；评论区互动闭环；所有行为记录"内容/动机/提及状态"，绝不穿帮 |
| 🎙 语音 | TTS 原生语音条（ogg/opus 波形条）+ STT 听懂你的语音；引擎走 AstrBot Provider 体系可换可克隆 |
| 🎬 导演 bot | 插件自持的独立控制台 bot，只认管理员一个人：绑定/切换她生活的对话（/umo /link）、说/做/定时行为编排、状态查看、偷看日记 |
| 🖥 Web 面板 | AstrBot WebUI 插件页：档案编辑、日记/记忆/事件浏览、图库管理上传打标、行为编排、数据导出 |
| 🔐 数据主权 | 全部数据在你自己的 `data/plugin_data/astrlover/`；一键导出=档案+记忆包+图库，解包即迁移 |

架构设计详见 [docs/design.md](docs/design.md)。

## 两个 bot 的分工

- **主 bot**：她本人。挂在 AstrBot 的 Telegram 平台实例上，聊天、语音、照片、换头像、发动态都发生在这个 bot 上。
- **导演 bot**：你的控制台。**由插件直接注册和管理，不占用 AstrBot 平台实例**——只需在插件配置里填 token。它只接受、只回复管理员一个人的消息，其他任何人发消息一律静默无视。

她"生活"在哪个对话里由导演 bot 决定：

```
/umo                                          ← 列出 AstrBot 里的全部对话 UMO
/link astrbotbot:FriendMessage:9876543210     ← 绑定到该对话
```

绑定后，她的聊天、主动消息、提醒都发生在这个对话里；随时可以 `/link` 切换到别的对话、`/unlink` 解除。尚未绑定任何对话时，管理员私聊主 bot 会自动绑定当前对话，开箱即用。

## 环境要求

- AstrBot **v4.16+**（推荐官方 Docker 镜像，自带 ffmpeg）
- 两个 Telegram bot（主 bot + 导演 bot，@BotFather 创建）
- 模型服务：对话 LLM（必需）、Embedding（记忆/图库检索必需）、
  轻量决策模型 / 独立 VLM / TTS / STT / 生图后端（可选，缺什么降级什么）

## 安装

```bash
cd <astrbot数据目录>/plugins
git clone https://github.com/RGBadmin/AstrLover.git
# 重启 AstrBot 或在 WebUI 插件页重载，依赖自动安装
```

## 配置步骤

### 1. 两个 bot

- @BotFather 创建主 bot（她）与导演 bot（控制台）；
- **主 bot 必须关闭 Group Privacy**（BotFather → Bot Settings → Group Privacy → Disable），
  否则她收不到讨论组里的评论。导演 bot 无需任何特殊设置。

### 2. AstrBot 平台实例（只需主 bot）

WebUI「消息平台」创建**一个** Telegram 适配器实例，填主 bot 的 token，
**记下你为它填写的实例 `id`**（例如 `tg_main`）。导演 bot 不在这里配置。

### 3. 插件配置（WebUI → 插件 → AstrLover → 配置）

- `wiring.main_platform_id`：主 bot 的平台实例 id；
- `wiring.owner_id`：管理员（你）的数字 user id（向 @userinfobot 发条消息即得）；
- `director.bot_token`：导演 bot 的 token（留空则禁用导演控制台）；
  `director.proxy`：网络需要时填代理地址（如 `http://127.0.0.1:7890`）；
- `models.*`：各 Provider 的 id（在 AstrBot「服务提供商」里配置好后填 id）；
  `embedding_provider_id` 强烈建议配置，否则记忆召回与图库检索降级；
- `proactive.*`：防打扰三参数（**仅有的行为参数**——粘人程度、语气这些一律改档案，不设旋钮）。

### 4. 频道（她的朋友圈，可选）

1. 创建一个频道 + 一个关联讨论组（频道设置 → Discussion）；
2. 把**主 bot** 拉为频道管理员（需发消息权限），并加入讨论组；
3. `wiring.channel_id` 填 `@频道用户名` 或 `-100` 开头的数字 id；
   `wiring.discussion_group_id` 填讨论组数字 id（@getidsbot 可查）。

### 5. 生命档案

首次启动自动生成 `data/plugin_data/astrlover/persona/profile.yaml`（模板见
`examples/persona.example.yaml`），在 Web 面板「档案」页直接编辑保存即热加载。
**想要她更粘人/更毒舌/更文静——改档案的性格描述，一切表现由人格推导。**

### 6. 图库

- 面板「图库」页上传，或把图片放进 `data/plugin_data/astrlover/gallery/files/` 后点「扫描目录」；
- 点「全量打标」（VLM 逐张打标，几百张需要一些时间；不点也会由心跳慢慢消化）；
- 挑几张最能代表她长相的自拍**设为锚点**——生图保持"同一个人"全靠它们；
- 表情包放进图库即可，打标会自动归类到 sticker。

### 7. 生图后端（可选，三选一或全配）

- **NanoBanana**（推荐首选）：填 `api_key`（官方或兼容中转的 `base_url`），支持参考图，一致性最好；
- **云 ComfyUI**：填 `base_url`，把 workflow 以 **API 格式**导出为 JSON 放到
  `data/plugin_data/astrlover/comfyui_workflow.json`，正/负提示词与种子用
  `{POSITIVE}` / `{NEGATIVE}` / `{SEED}` 占位；人物一致性建议在 workflow 内用 LoRA/IPAdapter/InstantID；
- **NovelAI**：填 `api_key`；一致性较弱，建议放降级链末位。

### 8. 语音（可选）

在 AstrBot 配置 TTS Provider（GPT-SoVITS 自部署 / CosyVoice / Fish Audio / Edge TTS 等），
把其 id 填入 `models.tts_provider_id`；STT 同理（Whisper / SenseVoice 等）。
声线克隆好之后换引擎不影响其他功能。

## 导演 bot 用法

对导演 bot 直接说人话即可编排（`今晚8点提醒他吃药，要像她自己惦记着一样`），
到点她会像自己想起来一样去做。常用命令：

| 命令 | 作用 |
|------|------|
| `/umo` | 列出 AstrBot 全部对话 UMO |
| `/link <UMO>` | 绑定她生活的对话（可随时切换） |
| `/unlink` | 解除绑定 |
| `/status` | 运行状态（此刻/日程/心情/模块健康/图库/待办） |
| `/diary [日期]` | 偷看日记 |
| `/events` | 最近的生活事件流 |
| `/say <内容>` | 让她以自己的口吻对他说这件事 |
| `/voice <内容>` | 让她发条语音 |
| `/post <主题>` | 让她发条频道动态 |
| `/avatar [提示]` `/sign [提示]` | 让她换头像 / 改签名 |
| `/pending` `/cancel <id>` | 查看 / 取消定时任务 |
| `/scan` `/tagall` | 扫描图库 / 全量打标 |
| `/config` | 查看与修改防打扰等参数 |

完整列表发 `/help` 查看。

## 成本说明

心跳（默认 5 分钟）为纯代码：推进日程、衰减情绪、计算意愿，**不耗 token**。
模型只在真正需要时出场：聊天回复、过阈值的主动消息（每天个位数）、
日记 1 次/天、周记 1 次/周、记忆沉淀（对话空闲后批处理）、打标（一次性）。
挂机一天的调用次数是可数的。

## 已知限制

- **表情回应感知不可用**：AstrBot 的 Telegram 适配器轮询未订阅 `message_reaction`
  更新，她无法感知你给动态点的 ❤️（评论她都能看到并回应）；
- 私聊多图逐条发送（Telegram 适配器无发送端相册；频道动态走原生 API 支持相册）；
- 头像/签名/频道/相册等能力直接使用适配器内部的 PTB 客户端，
  故 `astrbot_version` 锁定 `>=4.16,<5`，大版本升级需回归验证；
- 语音条依赖 ffmpeg（官方 Docker 镜像自带；裸机部署请自行安装）。

## 安全

- 导演 bot 与 Web 面板等同最高权限：导演 bot 只认 `wiring.owner_id`，
  面板经 AstrBot Dashboard 登录鉴权，**切勿把 Dashboard 暴露公网**；
- 频道评论等外部文本一律以"她读到的内容"包裹并附防注入护栏，绝不进入指令层；
- 仓库与配置模板不含任何密钥；一切凭据只存在于你的 AstrBot 配置中。

## 开发

```bash
python -m pytest tests   # 纯逻辑单元测试，无需安装 AstrBot
```

## License

MIT
