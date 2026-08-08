"""AstrLover —— 拟真 AI 恋人插件入口。

架构：骑在 AstrBot 默认对话管线上，不接管对话。
- 注入（on_llm_request）：她的此刻（人格/记忆/生活/情绪/时间/未提及事件）、
  历史动态时间线、图片记忆（登记/请求描述/取回/折叠）；
- 捕获（on_llm_response）：图片目录层描述、生命层内部标记；
- 她的能力全部是 LLM 工具，由她自己决定什么时候用；
- 导演 bot 是插件自持的独立 PTB bot（不占 AstrBot 平台实例），只认管理员。

本文件只做注册与薄委托：@filter 装饰器必须留在插件主模块。
业务实现在 astrlover/ 各子系统，由 App 装配。
"""

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .astrlover.app import App


def _flatten(node, out: dict) -> dict:
    """配置分组只是配置页的排版，代码一律按扁平 key 读。"""
    for k, v in (node or {}).items():
        if isinstance(v, dict):
            _flatten(v, out)
        else:
            out.setdefault(k, v)
    return out


class AstrLover(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.conf = _flatten(dict(config), {})
        self.app: App | None = None

    async def initialize(self):
        try:
            self.app = App(star=self, context=self.context, flat_conf=self.conf)
            await self.app.initialize()
        except Exception:
            logger.error("[AstrLover] 初始化失败：", exc_info=True)
            self.app = None

    async def terminate(self):
        if self.app is not None:
            await self.app.terminate()
            self.app = None
        logger.info("[AstrLover] 已停止。")

    # ==================================================================
    # 管线钩子
    # ==================================================================
    @filter.on_llm_request()
    async def on_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入：她的此刻、历史动态、图片记忆。"""
        if self.app is not None:
            await self.app.on_llm_request(event, req)

    @filter.on_llm_response()
    async def on_response(self, event: AstrMessageEvent, resp):
        """捕获：图片描述与内部标记，并从要发出去的内容里剥掉。"""
        if self.app is not None:
            await self.app.on_llm_response(event, resp)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=90)
    async def hold_when_silent(self, event: AstrMessageEvent):
        """/noreply 静默期：她先听着不回话，但你说的仍进她的记忆。"""
        app = self.app
        if app is None or not app.booted:
            return
        text = (event.message_str or "").strip()
        if text.startswith("/"):
            return  # 指令一律放行，否则静默就再也解除不了
        if not await app.silent_now():
            return
        event.should_call_llm(False)
        # AstrBot 在调 LLM 那步顺手写历史，禁掉 LLM 就没人写了，自己补一笔
        try:
            if app.is_partner(event) and app.ready and text:
                await app.working.log_user(text)
        except Exception:
            logger.debug("[AstrLover] 静默期补记失败", exc_info=True)

    # ==================================================================
    # LLM 工具：她自己决定什么时候用
    # ==================================================================
    @filter.llm_tool(name="browse_gallery")
    async def browse_gallery(
        self, event: AstrMessageEvent, keywords: str = "", want: str = "",
        folder: str = "", prefer_sent: str = "", around: str = "",
        rating: str = "", season: str = "", **_extra,
    ):
        """在你自己的相册里翻，找一张想发给他的照片。如果只是想发张照片、不确定该搜什么，用 want_photo 更好——它会帮你想。三种时候用：你想给他看点什么；他描述了某个画面让你找；他让你拍一张（「去厕所拍一张」「拍给我看」都是要照片，你不用真的去拍，在相册里找一张符合那个场景的发过去）。返回一批候选，你自己挑一张，再用 send_photo 发出去。

        Args:
            keywords(string): 检索词，空格分隔，六到十个，例如「酒店 落地窗 黑丝 细高跟 M腿 淫水」。是加分制不是筛选：多给不会搜不到，只影响排序，个别词没对上也不影响。三类各给几个才打得全——环境（酒店/车里/浴室/床上/镜子前）、身体与衣着（黑丝/细高跟/开裆内裤/蝴蝶逼/翘臀）、动作与体液（M腿大开/掰开/手指插入/淫水拉丝）。用库里真有的词，也就是描述照片时会用的那些字眼；写「下面」「私处」这种委婉说法一张都搜不到
            want(string): 可选，把想找的画面用一句话陈述出来，像在描述照片本身：「酒店房间落地窗前，黑色丝袜配红底细高跟，倚在窗边回头看镜头」。别写成「我想找一张…」，那几个字会当成画面内容去比对
            folder(string): 可选，限定某个相册分类
            prefer_sent(string): 他要最近发过的那张就填 recent，要没发过的新图填 fresh，听不出来留空（默认 fresh）
            around(string): 他提到某个月份就填，格式 YYYY-MM 或 MM，例如「三月那会儿的」填 03
            season(string): 季节。默认就会挑合当下时令的，通常留空。他明说要别的时候的才填——「去年冬天那张」填 冬，「换季那阵子」填 春秋，强调「现在这个季节」填 now
            rating(string): 尺度，六档由轻到重：生活、OOTD、性感、诱惑、露点、淫荡。也可以按平常说话的词填——日常（=生活）、穿搭（=OOTD）、勾人/撩（=性感+诱惑）、骚/骚货（=露点+淫荡）。留空则六档都会出现。这是你的选择而不是限制——他在挑逗你、话越说越色，尺度就跟着往上走；平常闲聊别自己往露的挑
        """
        app = self.app
        if app is None:
            return "相册还没准备好。"
        return await app.tools.browse_gallery(
            keywords, want, folder, prefer_sent, around, rating, season
        )

    @filter.llm_tool(name="want_photo")
    async def want_photo(self, event: AstrMessageEvent, reason: str = "", **_extra):
        """你想发一张自己的照片给他。不用想关键词，只说你为什么想发——你在撩他、他让你拍一张、你想秀今天的穿搭、或者就是想让他看看你。说了之后我替你挑，你只负责最后看一眼选哪张。

        Args:
            reason(string): 可选，为什么想发，比如「他让我去厕所拍一张」「想给他看今天的穿搭」。不写也行，我会从你们刚才的对话里判断
        """
        app = self.app
        if app is None:
            return "相册还没准备好。"
        return await app.tools.want_photo(event, reason)

    @filter.llm_tool(name="send_photo")
    async def send_photo(self, event: AstrMessageEvent, photo_id: str, caption: str = "", **_extra):
        """把一张照片发给对方。编号两种：browse_gallery 给的 g123 是你相册里的；对话里 [图片 #3] 那种 #3 是之前聊天里出现过的图，想重发某张旧图就用它。发完照常说你的话，别把发照片这件事当成一次汇报。

        Args:
            photo_id(string): 照片编号，g123 或 #3
            caption(string): 可选，跟照片一起发的一句话。留空则只发图
        """
        app = self.app
        if app is None:
            return "相册还没准备好。"
        return await app.tools.send_photo(event, photo_id, caption)

    @filter.llm_tool(name="inspect_photo")
    async def inspect_photo(self, event: AstrMessageEvent, photo_id: str = "", **_extra):
        """查一张图的画面细节。想知道某张图里的具体东西（有什么、什么颜色、写了什么字），而它不在你眼前时用这个——只给文字记录，不会把图重新塞进来，比 recall_photo 省得多。相册里的图也认：拿不准哪张合适、想确认某个细节在不在画面里，就用这个。

        Args:
            photo_id(string): 图片编号。聊天里出现过的填 12 或 #12；相册里的填 g123
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        return await app.tools.inspect_photo(photo_id)

    @filter.llm_tool(name="find_photo")
    async def find_photo(self, event: AstrMessageEvent, keywords: str = "", day: str = "", **_extra):
        """在存档的旧图片里找。对话里能直接看到的 [图片 #N] 占位不用查这个——只有当他提起一张你在当前上下文里找不到的旧图时才用。会同时搜你自己写的那句描述和系统存的画面细节。返回候选列表，如果不止一张，问他是哪张，别自己瞎猜。

        Args:
            keywords(string): 关键词，空格分隔。画面里的东西也能搜，比如「咖啡杯」
            day(string): 限定日期，格式 MM-DD 或 YYYY-MM-DD。留空则不限
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        return await app.tools.find_photo(keywords, day)

    @filter.llm_tool(name="recall_photo")
    async def recall_photo(self, event: AstrMessageEvent, photo_id: str, **_extra):
        """重新看一张之前被折叠掉的图片。对话里出现 [图片 #12 ...] 占位时，如果你需要真的再看一眼那张图的内容，用这个把它取回来，下一次回复时你就能看到它。注意这是让你自己再看一眼，不是发给他——要发给他用 send_photo。

        Args:
            photo_id(string): 占位里的编号，比如 12
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        return await app.tools.recall_photo(photo_id)

    @filter.llm_tool(name="generate_photo")
    async def generate_photo(self, event: AstrMessageEvent, situation: str, caption: str = "", **_extra):
        """相册里翻不到合适的照片时，现场"拍"一张发给他。先用 browse_gallery 找，实在没有贴切的再用这个——他点名要的场景相册里没有，或者要拍你此刻正在做的事。画面必须符合你此刻的生活状态和你的长相打扮。

        Args:
            situation(string): 画面描述：场景、你在做什么、穿着、情绪、构图（自拍视角/半身等）
            caption(string): 可选，跟照片一起说的一句话
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        return await app.tools.generate_photo(event, situation, caption)

    @filter.llm_tool(name="send_voice")
    async def send_voice(self, event: AstrMessageEvent, text: str, **_extra):
        """把一段话用你的声音以语音条发出去。撒娇、道晚安、长长的心里话、哄他睡觉这种时刻才用，平时打字就好，别滥用。

        Args:
            text(string): 要说出口的话，口语、自然，像对着手机说话（一般 80 字以内）
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        return await app.tools.send_voice(event, text)

    @filter.llm_tool(name="post_moment")
    async def post_moment(self, event: AstrMessageEvent, text: str, mention_now: bool = True, **_extra):
        """发一条动态到你自己的频道。当此刻发生了值得记录的事、你有情绪想表达、或者想让他看到你的近况时用。像发朋友圈那样，不需要每次聊天都发。

        Args:
            text(string): 动态的正文，用你自己的口吻写
            mention_now(boolean): 发完要不要在这次聊天里主动提这件事。真人不是每条动态都会特意说一嘴——想让他立刻知道就传 true；想让他自己刷到、或者这条你暂时不想解释，就传 false 然后正常聊别的
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        client = getattr(event, "client", None)
        return await app.moments.post(client, text, quiet=not mention_now)

    @filter.llm_tool(name="change_avatar")
    async def change_avatar(self, event: AstrMessageEvent, category: str = "", **_extra):
        """换一张新头像。换季了、心情变了、或者就是想换个样子时用。头像是你资料页上的脸，他点开你的头像就能看到。

        Args:
            category(string): 可选，头像分类（候选目录下的子文件夹名）。留空则随机挑
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        client = getattr(event, "client", None)
        return await app.face.change_avatar(client, category)

    @filter.llm_tool(name="update_signature")
    async def update_signature(self, event: AstrMessageEvent, text: str, **_extra):
        """改你资料页上那句签名（120 字以内）。适合放此刻的状态或心情——它会覆盖上一句、没有历史记录。想记录某件事、想让他收到通知，那是动态的活儿。

        Args:
            text(string): 新的签名
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        client = getattr(event, "client", None)
        return await app.face.update_signature(client, text)

    @filter.llm_tool(name="react_message")
    async def react_message(self, event: AstrMessageEvent, emoji: str, **_extra):
        """给他刚发的这条消息打个表情。不说话也让他知道你看到了、有反应——适合他发了张图、说了句好笑的、或者你懒得回但想让他知道你在。

        Args:
            emoji(string): 一个表情符号，比如 ❤️ 👍 😂 🔥 🥰
        """
        app = self.app
        if app is None:
            return "还没准备好。"
        return await app.face.react(event, emoji)
