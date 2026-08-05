# AstrLover 💞

拟真 AI 恋人——有人格、有记忆、有作息、过着自己生活的数字恋人。基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 Telegram 插件。

> **像真人一样生动，像 AI 一样无害。**
> 她不是"问答机器人加了人设"：昨天聊过的事她今天记得，她自己做过的事她知道，
> 她的生活在你不在的时候也在继续。

## 功能

| 能力 | 说明 |
|------|------|
| 🧬 生命档案 | YAML 人格档案（身份/外观/性格/身世/社交圈/作息/关系），静态基线与动态演化分离，换档案不丢"你们的过去" |
| 🧠 四层记忆 | 工作记忆 → 核心小抄（她自己修订）→ 结构化事实（可更新可失效）→ 日记/周记（第一人称情景记忆，语义召回+适度遗忘） |
| 🌊 虚拟生活 | 按作息生成每日日程、"此刻在干什么"状态机、洗澡/睡觉时晚回消息、叙事连续 |
| 💌 主动消息 | 早安晚安/饭点关心/话题延续/想炫耀/想念——由"意愿"驱动而非定时器；防打扰三参数兜底 |
| 😊 情绪引擎 | 情绪带半衰期自动消散，绝不累积；哄一句立刻雨过天晴；只有"可爱系"表达，永不指责 |
| 🖼 图库+生图 | 图片 VLM 结构化打标 + FAISS 语义检索；"下一幕"情境推理选图；不够贴切自动降级生图（NanoBanana/云ComfyUI/NovelAI），生成图回流图库 |
| 😜 表情包 | 按语境语义甩表情包、接梗斗图 |
| 🔄 拟真存在 | 自主换头像、改签名、发频道动态（她的朋友圈）；评论区互动闭环；所有行为进入事件流（内容/动机/提及状态），绝不穿帮 |
| 🎙 语音 | TTS 原生语音条（ogg/opus 波形条）+ STT 听懂你的语音；引擎走 AstrBot Provider 体系可换可克隆 |
| 🎛 上帝模式 | 第二个 bot 只认你：说/做/定时行为编排、状态查看、偷看日记——与她的自主行为共用执行通道，毫无违和 |
| 🖥 Web 面板 | AstrBot WebUI 插件页：档案编辑、日记/记忆/事件浏览、图库管理上传打标、行为编排、数据导出 |
| 🔐 数据主权 | 全部数据在你自己的 `data/plugin_data/astrlover/`；一键导出=档案+记忆包+图库，解包即迁移 |

架构设计详见 [docs/design.md](docs/design.md)。

## 环境要求

- AstrBot **v4.16+**（推荐官方 Docker 镜像，自带 ffmpeg）
- 两个 Telegram bot（主 bot + 上帝 bot，@BotFather 创建）
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

- @BotFather 创建主 bot（她）与上帝 bot（控制台）；
- **主 bot 必须关闭 Group Privacy**（BotFather → Bot Settings → Group Privacy → Disable），
  否则她收不到讨论组里的评论。

### 2. AstrBot 平台实例

WebUI「消息平台」创建两个 Telegram 适配器实例，分别填两个 bot 的 token，
**记下你为它们填写的实例 `id`**（例如 `tg_main`、`tg_god`）。

### 3. 插件配置（WebUI → 插件 → AstrLover → 配置）

- `wiring.main_platform_id` / `wiring.god_platform_id`：上面两个实例 id；
- `wiring.owner_id`：你的数字 user id（向 @userinfobot 发条消息即得）；
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

## 上帝 bot 用法

对上帝 bot 直接说人话即可编排（`今晚8点提醒他吃药，要像她自己惦记着一样`），
或用命令：`/status` `/diary` `/events` `/pending` `/cancel` `/scan` `/tagall`
`/post` `/avatar` `/sign` `/say` `/voice` `/config`，`/help` 查看全部。
其他任何人对上帝 bot 说话都会被静默无视。

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

- Web 面板经 AstrBot Dashboard 登录鉴权，**等同上帝权限，切勿把 Dashboard 暴露公网**；
- 频道评论等外部文本一律以"她读到的内容"包裹并附防注入护栏，绝不进入指令层；
- 仓库与配置模板不含任何密钥；一切凭据只存在于你的 AstrBot 配置中。

## 开发

```bash
python -m pytest tests   # 纯逻辑单元测试，无需安装 AstrBot
```

## License

MIT
