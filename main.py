"""AstrLover —— 拟真 AI 恋人插件入口。

架构（v0.3 起）：骑在 AstrBot 默认对话管线上，不接管对话。
- presence 层（源自 astrbot_plugin_tg_presence v0.33，作者本人代码并入）：
  相册/发照片/动态/头像签名/表情回应/图片记忆/控制台/静默/主动消息，
  以钩子 + LLM 工具 + 指令注册，主体实现在 astrlover/presence/core.py；
- 生命模拟层（人格/记忆/生活/心跳/情绪/事件/日记）经 on_llm_request 注入，
  在 astrlover/ 各子系统中，由 App 装配。

本文件只做注册与薄委托：@filter 装饰器必须留在 main.py
（AstrBot 按 handler 模块路径解析插件，见 builtin_commands 同款组织）。
"""

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .astrlover.presence.core import PresenceCore


class AstrLover(PresenceCore, Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)  # PresenceCore.__init__ → Star.__init__
        self.config = config

    async def initialize(self):
        await PresenceCore.initialize(self)
        logger.info("[AstrLover] presence 层就绪。")

    async def terminate(self):
        await PresenceCore.terminate(self)
        logger.info("[AstrLover] 已停止。")

    # ==================================================================
    # presence 层委托（实现体在 astrlover/presence/core.py，行为与
    # tg_presence v0.33 一致；docstring 原样保留——llm_tool 靠它向模型
    # 描述工具，command 靠它出帮助）
    # ==================================================================
    @filter.on_llm_request(priority=-50)
    async def prune_context_images(self, event: AstrMessageEvent, req: ProviderRequest):
        """只保留最近 N 张真图，更早的换成文字占位。

        AstrBot 把图片以 base64 data URL 的形式写进 conversation history
        (entities.py:207-222 → internal.py:531)，之后每一轮都原样重发。
        累积几十张之后既吃 token 又稀释注意力。

        换下来的图先存盘再替换，正文里留 [图片 #N] 的编号，
        需要重新看时用 recall_photo 工具按编号取回。

        priority=-50 让它在其它注入之后跑，只处理真正的历史图片。
        """
        return await PresenceCore.prune_context_images(self, event, req)

    @filter.on_llm_request(priority=-35)
    async def register_context_photos(self, event: AstrMessageEvent, req: ProviderRequest):
        """给上下文里每张图登记编号、存盘，并派发视觉解析。

        存盘不能等到折叠时才做：llm_compress 会把整轮对话连同图片一起压掉，
        /new 也会清空上下文，那之后原图就再也拿不回来了。图片一旦进入视野
        就先落盘，后面无论上下文怎么变，档案都还在。

        priority=-35 让它排在请求描述(-40)和折叠(-50)前面，
        这样那两步拿到的编号都是这里分配好的。
        """
        return await PresenceCore.register_context_photos(self, event, req)

    @filter.on_llm_request(priority=-40)
    async def ask_for_descriptions(self, event: AstrMessageEvent, req: ProviderRequest):
        """把上下文里还没有描述的图片列出来，请角色在这次回复里顺带描述。

        比折叠时另起一次视觉调用便宜得多，而且此刻她正看着图、
        也知道当时聊的是什么，描述会带上她自己的视角。
        """
        return await PresenceCore.ask_for_descriptions(self, event, req)

    @filter.on_llm_response()
    async def capture_descriptions(self, event: AstrMessageEvent, resp):
        """抽出 <img_note> 存档，并从要发出去的内容里剥掉。

        必须走 completion_text 这个 property，不能直接摸 result_chain：
        LLMResponse.result_chain 默认是 None（provider 一般只填 _completion_text，
        setter 在 result_chain 为 None 时就写那个私有字段），所以
        result_chain.chain 多半取不到，判空一 return 就等于整个钩子没跑。
        property 的 getter/setter 两种存储形态都覆盖，而且 result_chain 存在时
        setter 只替换 Plain 组件、不动图片之类的其它组件。
        """
        return await PresenceCore.capture_descriptions(self, event, resp)

    @filter.on_decorating_result()
    async def strip_notes_before_send(self, event: AstrMessageEvent):
        """发送前最后一道闸：确保 <img_note> 不会漏进聊天窗口。

        上一步已经剥过一次，但文本抵达发送阶段的路径不止一条（其它插件改写、
        分段回复重组等），这里照最终要发的内容再兜一次底。
        正常情况下这里什么都匹配不到 —— 一旦日志里出现，说明上一步漏了。
        """
        return await PresenceCore.strip_notes_before_send(self, event)

    @filter.llm_tool(name="find_photo")
    async def find_photo(self, event: AstrMessageEvent, keywords: str='', day: str='', **_extra):
        """在存档的旧图片里找。对话里能直接看到的那些 [图片 #N] 占位不用查这个——只有当对方提起一张你在当前上下文里找不到的旧图时才用。会同时搜你自己写的那句描述和系统存的画面细节记录。返回候选列表，如果不止一张，问对方是哪张，别自己瞎猜。

        Args:
            keywords(string): 关键词，空格分隔，例如「黑丝 足底」。会取交集。画面里的东西也能搜，比如「咖啡杯」
            day(string): 限定日期，格式 MM-DD 或 YYYY-MM-DD。留空则不限
        """
        return await PresenceCore.find_photo(self, event, keywords, day, **_extra)

    @filter.llm_tool(name="recall_photo")
    async def recall_photo(self, event: AstrMessageEvent, photo_id: str, **_extra):
        """重新看一张之前被折叠掉的图片。对话里出现 [图片 #12 ...] 这样的占位时，如果你需要真的再看一眼那张图的内容，用这个把它取回来。取回后在下一次回复时你就能看到它。

        Args:
            photo_id(string): 占位里的编号，比如 12
        """
        return await PresenceCore.recall_photo(self, event, photo_id, **_extra)

    @filter.llm_tool(name="want_photo")
    async def want_photo(self, event: AstrMessageEvent, reason: str='', **_extra):
        """你想发一张自己的照片给他。不用想关键词，只说你为什么想发——你在撩他、他让你拍一张、你想秀今天的穿搭、或者就是想让他看看你。说了之后我替你挑，你只负责最后看一眼选哪张。

        Args:
            reason(string): 可选，为什么想发，比如「他在挑逗我想看我湿没湿」「他让我去厕所拍一张」「想给他看今天的穿搭」。不写也行，我会从你们刚才的对话里判断
        """
        return await PresenceCore.want_photo(self, event, reason, **_extra)

    @filter.llm_tool(name="browse_gallery")
    async def browse_gallery(self, event: AstrMessageEvent, keywords: str='', want: str='', folder: str='', prefer_sent: str='', around: str='', rating: str='', season: str='', **_extra):
        """在你自己的相册里翻，找一张想发给他的照片。如果你只是想发张照片、不确定该搜什么，用 want_photo 更好——它会帮你想。这个工具适合你已经知道要什么、想精确控制检索词的时候。三种时候用：你想给他看点什么；他描述了某个画面让你找；他让你拍一张。特别注意第三种——他说「去厕所拍一张」「现在拍给我看」「让我看看」「拍张照发我」，这些都是要照片，你不用真的去拍，就是在相册里找一张符合他说的那个场景的发过去，正常聊天里这本来就是同一件事。返回一批候选，你自己挑一张，再用 send_photo 发出去。

        Args:
            keywords(string): 检索词，空格分隔，例如「酒店 灰丝 细高跟 M腿」。词尽量多给几个，命中越多排得越前，个别词没对上也不影响。从他刚说的话里把地点、姿势、身体部位、衣着、动作都抠出来当词——他说「去厕所拍一张」就带上 厕所 卫生间 隔间，问「是不是湿了」就带上 淫水 内裤 湿。刚才聊到的东西也算数，不只是最后那一句
            want(string): 可选，把想找的画面用一句话原样描述出来
            folder(string): 可选，限定某个相册分类
            prefer_sent(string): 他要的是最近发过的那张就填 recent，要没发过的新图就填 fresh，听不出来就留空（默认 fresh，免得老发同一张）
            around(string): 他提到某个月份就填，格式 YYYY-MM 或 MM，例如「三月那会儿的」填 03。那个月的图会整体排到前面。没提就留空
            season(string): 季节。默认就会挑合当下时令的，所以通常留空即可。他明说要别的时候的就填——「去年冬天那张」填 冬，「换季那阵子」填 春秋；他强调「现在这个季节」填 now。填的是画面里那身打扮适合什么季节穿，不是拍摄日期
            rating(string): 尺度，六档由轻到重：生活（吃饭逛街风景自拍）、OOTD（拍的是这身穿搭）、性感（衣服还能出门但在展示身材）、诱惑（内衣泳装情趣内衣、身体特写、明显在勾人，还没露点）、露点（露出性器官或乳头）、淫荡（性行为、自慰、体液）。填档名只翻那一档；也可以按平常说话的词来填——日常/平时（=生活）、穿搭（=OOTD）、勾人/诱人/撩（=性感+诱惑）、骚/骚货/母狗（=露点+淫荡）。留空则六档都会出现。注意这是你的选择而不是限制——想用一张露的去逗他，那就主动填。他多半不会明说要多露，得你自己从刚才聊的内容里判断：他在挑逗你、问的是你身体上的事、话越说越色，那尺度就跟着往上走，这种时候翻出一张日常穿搭最煞风景；反过来平常闲聊时也别自己往露的挑
        """
        return await PresenceCore.browse_gallery(self, event, keywords, want, folder, prefer_sent, around, rating, season, **_extra)

    @filter.llm_tool(name="inspect_photo")
    async def inspect_photo(self, event: AstrMessageEvent, photo_id: str='', **_extra):
        """查一张图的画面细节。想知道某张图里的具体东西（有什么、什么颜色、写了什么字），而它不在你眼前时用这个——只给文字记录，不会把图重新塞进来，比 recall_photo 省得多。相册里的图用它细看：browse_gallery 给的是摘要，拿不准哪张合适、或者想确认某个细节在不在画面里，就用这个。

        Args:
            photo_id(string): 图片编号。聊天里出现过的图填 12 或 #12；相册里的填 browse_gallery 列出来的那个 g123
        """
        return await PresenceCore.inspect_photo(self, event, photo_id, **_extra)

    @filter.llm_tool(name="send_photo")
    async def send_photo(self, event: AstrMessageEvent, photo_id: str, caption: str='', **_extra):
        """把一张照片发给对方。编号有两种：browse_gallery 给的 g123 是你相册里的；对话里 [图片 #3] 那种 #3 是之前聊天里出现过的图，想重发某张旧图就用它。发完照常说你的话，别把发照片这件事当成一次汇报。

        Args:
            photo_id(string): 照片编号，g123 或 #3
            caption(string): 可选，跟照片一起发的一句话。留空则只发图
        """
        return await PresenceCore.send_photo(self, event, photo_id, caption, **_extra)

    @filter.command("photo")
    async def cmd_photo(self, event: AstrMessageEvent, photo_id: str='', *, caption: str=''):
        """手动发一张照片。用法：/photo g123 [附言]"""
        async for _r in PresenceCore.cmd_photo(self, event, photo_id, caption=caption):
            yield _r

    @filter.on_llm_request(priority=-60)
    async def serve_recalled(self, event: AstrMessageEvent, req: ProviderRequest):
        """把被 recall_photo 点名的图片重新塞进本轮请求。"""
        return await PresenceCore.serve_recalled(self, event, req)

    @filter.after_message_sent()
    async def record_sent_time(self, event: AstrMessageEvent):
        """记下角色刚说话的时刻，下次组请求时给那条 assistant 消息补戳。"""
        return await PresenceCore.record_sent_time(self, event)

    @filter.on_llm_request()
    async def stamp_assistant(self, event: AstrMessageEvent, req: ProviderRequest):
        """给角色自己的消息加时间戳。

        AstrBot 只给 user 消息带时间（datetime_system_prompt 那段 system_reminder
        会随 content 落库），assistant 消息是模型输出、没有任何时间锚点。
        主动消息尤其严重——前面没有 user 消息，等于完全没有时间参照。

        这里改的是 req.contexts，而 _save_to_history 保存的正是它派生出的
        run_context.messages，所以戳会落库、只需要打一次。
        """
        return await PresenceCore.stamp_assistant(self, event, req)

    @filter.on_llm_request()
    async def inject_moments(self, event: AstrMessageEvent, req: ProviderRequest):
        return await PresenceCore.inject_moments(self, event, req)

    @filter.llm_tool(name="post_moment")
    async def post_moment(self, event: AstrMessageEvent, text: str, category: str='', mention_now: bool=True, **_extra):
        """发一条动态到你自己的频道。当此刻发生了值得记录的事、你有情绪想表达、或者你想让对方看到你的近况时使用。像发朋友圈那样，不需要每次聊天都发。如果对方这条消息里带了图片，那些图会自动作为这条动态的配图。

        Args:
            text(string): 动态的正文，用你自己的口吻写
            category(string): 可选，配图分类名。仅在对方没带图时才用它去图库里挑；留空则发纯文字
            mention_now(boolean): 发完要不要在这次聊天里主动提这件事。真人不是每条动态都会特意说一嘴——想让他立刻知道就传 true；想让他自己刷到、或者这条你暂时不想解释，就传 false，然后正常聊别的
        """
        return await PresenceCore.post_moment(self, event, text, category, mention_now, **_extra)

    @filter.command("moment")
    async def cmd_moment(self, event: AstrMessageEvent, text: str=''):
        """手动发一条动态。用法：/moment 正文"""
        async for _r in PresenceCore.cmd_moment(self, event, text):
            yield _r

    @filter.llm_tool(name="change_avatar")
    async def change_avatar(self, event: AstrMessageEvent, category: str='', **_extra):
        """换一张自己的头像。当你心情变了、换了造型、或者只是想换换感觉的时候使用。

        Args:
            category(string): 可选，头像分类名，对应头像目录下的子文件夹。留空则从全部头像里随机挑
        """
        return await PresenceCore.change_avatar(self, event, category, **_extra)

    @filter.command("avatar")
    async def cmd_avatar(self, event: AstrMessageEvent, category: str=''):
        """手动换头像。用法：/avatar [分类]"""
        async for _r in PresenceCore.cmd_avatar(self, event, category):
            yield _r

    @filter.llm_tool(name="update_signature")
    async def update_signature(self, event: AstrMessageEvent, text: str, mention_now: bool=False, **_extra):
        """改自己资料页上的个性签名。那是一句短话，对方点开你的头像就能看到，会一直挂在那儿直到你再改。它会覆盖上一句、没有历史记录，所以适合放此刻的状态或心情；想记录某件事、想让对方收到通知，用发动态。

        Args:
            text(string): 新的签名，120 字符以内，一句话就够
            mention_now(boolean): 要不要在聊天里主动说自己换了签名。默认不说——改签名没有通知，让他自己发现更自然
        """
        return await PresenceCore.update_signature(self, event, text, mention_now, **_extra)

    @filter.command("signature", alias={"bio"})
    async def cmd_signature(self, event: AstrMessageEvent, text: str=''):
        """手动改签名。用法：/signature 新签名"""
        async for _r in PresenceCore.cmd_signature(self, event, text):
            yield _r

    @filter.llm_tool(name="react_message")
    async def react_message(self, event: AstrMessageEvent, emoji: str, **_extra):
        """给对方刚发的那条消息打一个表情，作为轻量回应。适合不需要说话、但想让对方知道你看到了的时候——比如他说了句好笑的、或者你只是想戳他一下。

        Args:
            emoji(string): 一个表情符号，例如 ❤️ 👍 😂 🔥 🥰 😭 🤔
        """
        return await PresenceCore.react_message(self, event, emoji, **_extra)

    @filter.command("presence")
    async def cmd_presence(self, event: AstrMessageEvent):
        """查看插件状态：已发动态数、各项冷却剩余。"""
        async for _r in PresenceCore.cmd_presence(self, event):
            yield _r

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def hold_when_silent(self, event: AstrMessageEvent):
        """静默期里，他说的话只进历史，不惊动她。

        should_call_llm 的语义是反的，传 True 才是禁止。禁掉之后 AstrBot
        也不再往 conversation 里写这条了（它是在调 LLM 那一步顺手写的），
        所以得自己补一笔——否则解除静默之后，她对这中间说过的话一无所知，
        而"说给她听、只是别回"正是这条指令的全部意义。
        """
        return await PresenceCore.hold_when_silent(self, event)

    @filter.command("tghelp", alias={"插件帮助"})
    async def cmd_help(self, event: AstrMessageEvent, name: str=''):
        """列出插件的全部指令。用法：/tghelp，或 /tghelp gallery 看单条。
        控制台里直接用 /help。"""
        async for _r in PresenceCore.cmd_help(self, event, name):
            yield _r

    @filter.command("whoami")
    async def cmd_whoami(self, event: AstrMessageEvent):
        """诊断：这个会话里你是谁、插件读到的管理员名单认不认你。不需要管理员权限。"""
        async for _r in PresenceCore.cmd_whoami(self, event):
            yield _r

    @filter.command("umo")
    async def cmd_umo(self, event: AstrMessageEvent, arg: str=''):
        """列出所有会话的 UMO，用来挑一个 /link 绑上。用法：/umo [关键词]"""
        async for _r in PresenceCore.cmd_umo(self, event, arg):
            yield _r

    @filter.command("link")
    async def cmd_link(self, event: AstrMessageEvent, target: str=''):
        """在控制台里绑定投递目标。用法：/link 目标UMO，或 /link show 查看当前绑定。"""
        async for _r in PresenceCore.cmd_link(self, event, target):
            yield _r

    @filter.on_llm_request(priority=-95)
    async def note_user_activity(self, event: AstrMessageEvent, req: ProviderRequest):
        """他一开口就重排倒计时。控制台那边的指令不算互动。"""
        return await PresenceCore.note_user_activity(self, event, req)

    @filter.command("proactive")
    async def cmd_proactive(self, event: AstrMessageEvent, arg: str=''):
        """看主动消息的倒计时状态。用法：/proactive [now]"""
        async for _r in PresenceCore.cmd_proactive(self, event, arg):
            yield _r

    @filter.command("say")
    async def cmd_say(self, event: AstrMessageEvent, *, text: str=''):
        """在控制台里用：让角色原样说一句话。用法：/say 内容"""
        async for _r in PresenceCore.cmd_say(self, event, text=text):
            yield _r

    @filter.command("noreply")
    async def cmd_noreply(self, event: AstrMessageEvent, arg: str=''):
        """让她先别回话，你说的仍然记进她的记忆。用法：/noreply [分钟]"""
        async for _r in PresenceCore.cmd_noreply(self, event, arg):
            yield _r

    @filter.command("reply")
    async def cmd_reply(self, event: AstrMessageEvent, arg: str=''):
        """解除静默，她重新开口。用法：/reply"""
        async for _r in PresenceCore.cmd_reply(self, event, arg):
            yield _r

    @filter.command("act")
    async def cmd_act(self, event: AstrMessageEvent, *, brief: str=''):
        """在控制台里用：给个方向，让角色自己组织语言发出去。用法：/act 跟他说你今天加班到很晚"""
        async for _r in PresenceCore.cmd_act(self, event, brief=brief):
            yield _r

    @filter.command("gallery")
    async def cmd_gallery(self, event: AstrMessageEvent, action: str='', *, rest: str=''):
        """管理相册索引。用法：/gallery [scan|index N|search 词|embed N|audit|redo|retry]"""
        async for _r in PresenceCore.cmd_gallery(self, event, action, rest=rest):
            yield _r

    @filter.command("vision")
    async def cmd_vision(self, event: AstrMessageEvent, arg: str=''):
        """给还没有细节记录的存量图片补做视觉解析。用法：/vision [张数|retry|test]"""
        async for _r in PresenceCore.cmd_vision(self, event, arg):
            yield _r

