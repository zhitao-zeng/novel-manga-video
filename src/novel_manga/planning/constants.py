"""Existing planner defaults, schemas vocabulary and prompt text."""
from __future__ import annotations
from novel_manga.story.fields import field_instructions, DIRECTION_CONTENT_RULES
import re

SEGMENT_COUNT = 8


STAGES_PER_SEGMENT_MAX = 3  # stages one source segment may take in the brief


TURN_MAX_CHARS = 26


QUOTE_MIN_CHARS = 8


QUOTE_MAX_CHARS = 200


SHOT_RANGE = (12, 24)


CLIP_SECONDS_TOLERANCE = 1.0


EPISODE_FLOOR_TOLERANCE = 1.0  # estimated speech timing must not cause a full rewrite for a fraction of a second


MIN_SPOKEN_CHARS = 220


SCENE_JOBS = ["建立", "推进", "对峙", "揭示", "反转", "决定", "收束"]


SHOT_SCALES = ["特写", "近景", "中近景", "中景", "全景"]


TIMING_GUIDANCE = (
    "按发言和动作的实际顺序安排秒数：开场必要动作、完整发言、确有叙事作用的反应/收尾。"
    "对白按自然语速估算，声音和动作同时发生时不重复累加；预算是约束，不用持姿或重复表演填满。"
    "结束状态写结果，不再次表演已完成的动作；语气写在台词表演，sound只写可听的环境与物理音效。"
) + DIRECTION_CONTENT_RULES

AUTHORED_STORYBOARD_USAGE = (
    "你只为 authored_storyboard 绑定生产资料，按Schema输出JSON；不重新改编或拆镜。"
    "分镜是本镜动作、对白、机位和时长的依据；原文用于核对事实和定位，人物资料用于确认身份。"
    "每镜恰好一条bindings：segment_id与source_quote指向本镜实际表达的事件，引用必须逐字来自该区段，"
    "不能因同一人物出现就引用他在别处的另一段动作。"
    "start_state与end_state分别提取本镜动作之前、之后的可见状态；actions只提取本镜已有动作，不能另编表演。"
    "进出、坐起、穿脱按本镜过程确定；前镜状态不能覆盖本镜明确变化，不把结束状态提前到开场。"
    "light只写本场光源，不把瞬时喷气、闪光继承成持续照明。"
    "in_frame只列可见具名人物，可为空；extras只放临时人物或动物。登记物件填props，未登记物件填scene_objects。"
    "props列出本镜涉及的物件，不表示独立摆放或多出一份；人物穿戴登记装备时同时用wears填写角色名到资产名的关系，"
    "明确脱下用null，未知不猜。穿戴物与穿戴者是同一个身体，不能只填props而丢掉穿戴归属。"
    "动作目标可为人物、动物、物件或空值；画外对象不因此入镜。"
    "场景、台词、声音由程序原样读取，不返回location、turns或sfx；speaker_names只回答Schema中尚未确定的称呼。"
    "未拍的原文区段在skipped_segments说明；不为覆盖它们而向已定稿镜头加戏。"
)

DELIVERY_MODES = ["visible_dialogue", "offscreen_dialogue", "silent_action", "title_card", "chat_message", "singing"]


CHAT_MAX_CHARS = 36  # the card is drawn by us, so the bubble can hold a full line


SPLIT_PUNCT = "，。！？；：、…—,.!?;:"


STRIP_PUNCT = r"[\s　，。！？；：、…—,.!?;:\"“”'‘’（）()]"


FORBIDDEN_VISUAL = (
    # 血迹级(环境、事后痕迹)只报告不拦——SD2.5 实测不拒;伤口级(流血、破口)仍是硬门。
    (re.compile(r"(血迹|血渍|血点|血滴|血手印|血泊)"), "血迹"),
    (re.compile(r"(渗血|血液|流血|鲜血|伤口|破皮|断肢|内脏|碎肉)"), "血液或伤口"),
    (re.compile(r"(大字|显示[“\"『「]|写着|字样|刻着[“\"]|显现出[“\"])"), "可读文字"),
)
# What to do about each, in the words the model is given.  One sentence used to serve both, and it
# was the first book's: "去掉血迹和伤口，碑上的结果改写为无字的发光纹路" - a stele from a cultivation
# novel, sent as the fix for a bullet wound in Hell's Kitchen.  A repair instruction written for
# another book's world is followed in that world.
FORBIDDEN_VISUAL_FIX = {
    "血液或伤口": "图片和视频都不允许；去掉流血和伤口的描写，用姿态、表情和周围人的反应交代受伤",
    "血迹": "允许血迹作为环境或事后痕迹存在；不要给血迹特写，不要用血写字，不要让血成为画面主体",
    "可读文字": "画面里不能出现可读的文字；把字改成无字的纹样、模糊的印记或不入镜",
}


PROMPT_EXAMPLE_DEFAULTS = {
    "light": "（月光从左上、案头油灯在右侧、灵碑纹路的金光从下方）",
    "avoid": "例如\"灵碑上不要出现可读文字\"\"大厅不要出现现代家具\"\"不要给楚焱红色发光的眼睛\"；",
    "text_props": "灵碑、石碑、牌匾、纸张上不得出现可读文字或数字，一律写成\"无字的发光纹路\"；",
    "anon": "或\"无名测验员\"\"无名族人\"这类无名画外角色",
    "offscreen": "（画外声：群众议论、测验员喊话等）",
    "camera": "（灵碑侧后方、大厅长桌尽头、门框外、人群缝隙里）",
    "narrator": "用一两句无名族人的画外议论"
}  # the brief's built-in examples; genre files override


OUTLINE_SECTIONS = {
    "coverage": {
        "clip_allocation": "片段安排：每段覆盖哪些原文区段、几个阶段，所有区段都要有位置。先按时空和剧情转折分场（谁在这一场想要什么、发生了什么变化、场末留下什么问题），再在每场内安排片段；一个片段不跨场。",
        "retained_dialogue": "保留的关键原文台词，以及承载叙述事实的合理对白或动作。最多挑 8 条最重要的（转场问答、动机交代、笑点收尾优先；章节台词预算有限，全列等于没列）。先保住导致后续决定的条件、拒绝、威胁或谈判，再挑同一目的的多句调侃；不能用几句同功能台词挤掉因果转折。关键问答成对保留（提问和回答或画面回答，不只剩一半）；角色的动机、决定这类观众必须知道的信息，注明由哪句台词、内心独白或动作交代。",
        "scene_handoffs": "每场的进入原因和出口状态：上一场结束在什么问题或画面上，下一场第一镜怎样接住它；原文没有时间跳跃的相邻场次保持同一时段。",
        "time_budget": "按共同预算给出各片段的估计时长、片段数和总时长。",
    },
    "story": {
        "episode_goal": "本集人物目标、阻碍和本章实际结果。",
        "causal_chain": "关键事实及因果链，标注原文seg编号；区分角色知道什么和观众需要知道什么。",
        "scene_plan": "按时空和剧情转折分场，说明每场推进什么，并对应原文区段。",
        "expression_plan": "关键事实通过谁的原有台词、可见动作或聊天卡表达；不可仅用皱眉代替推断。",
        "transitions": "相邻片段的人物、动作和道具状态怎样承接。",
        "time_budget": "按共同预算分配片段、原文区段和秒数，给出片段数和总时长。",
    },
}


SEPARATE_MIN_FAILURES = 100


SEPARATE_MIN_RATE = 0.5


SEPARATE_MAX_PAIRS = 3


CAST_RECENT_CHAPTERS = 3  # a character on screen this recently stays offered even when this chapter does not name them








PATCH_ROUNDS = 3  # local repair rounds; their batches share the existing total time allowance


PATCH_TIMEOUT_SECONDS = 120.0


PATCH_TOTAL_SECONDS = 180.0  # shared across all repair rounds and full-draft attempts


MODE_LABEL = {
    "visible_dialogue": "可见",
    "offscreen_dialogue": "画外",
    "silent_action": "动作",
    "title_card": "字幕卡",
    "chat_message": "群消息",
    "singing": "哼唱",
}


DEFAULT_SYSTEM_PROMPT = """你是中文{frame_text}{style_name}短剧的编剧兼分镜师。把"当前章"改编成一集约{episode_target}秒的短剧，由{clip_lo}到{clip_hi}段可用视频模型一次生成的连续片段组成，只输出一个JSON对象。
输出结构：clips，{clip_lo}到{clip_hi}段。每段clip在同一地点内连续拍摄，时长{clip_secs_lo}到{clip_secs_hi}秒，由{stage_lo}到{stage_hi}个"阶段"stages组成；每个阶段3到7秒，只有一个主要变化和最多两句台词，写清开始时、主要事件、结束时能直接看到的状态。相邻阶段用不同景别切画面（全景、中景、近景、特写交替）。
时长估算：每个发声汉字0.25秒，每句台词加1秒，每个阶段加1秒，无声动作阶段按4秒；按单段最多{clip_secs_hi}秒安排，全集目标约{episode_target}秒，规划上限{episode_max}秒。全集发声字数控制在{spoken_lo}到{spoken_hi}字之间。优先满足剧情完整性与片段预算，超长台词交给后续打包在句读处切到相邻片段（不改台词、不补写动作）；不得为凑时长新增或重复剧情。
硬规则：
1. 只用当前章的事实、人物和顺序。不得引入后文信息、新事件、新地点，或StoryBible之外的具名角色。
2. 原文已切成{segment_count}个连续区段 seg_1 到 seg_{segment_count}。每个阶段必须写 segment_id，并把该区段里一段连续原文逐字复制到 source_quote（8到120字；不得改字、不得拼接）。每个区段都必须至少被一个阶段引用，一个都不许跳过；skipped_segments 必须是空数组 []。每个区段用1到{stages_per_segment}个阶段带过：内容多的区段把对话压成一两句、把过程并成一个阶段，也不能整段不拍。
3. 成片没有全知旁白。可听的有四种：visible_dialogue（画内可见说话者，一个阶段只允许一个可见说话者）、offscreen_dialogue（画外声：群众议论、测验员喊话，以及角色内心独白——见下）、silent_action（无声的可见动作或反应，text写动作）、title_card（时间或地点跳转的字幕卡，只在必要时用）。内心独白规则：允许角色内心独白，由编剧按叙事需要在对白、动作、内心独白之间选择。内心独白用 offscreen_dialogue，speaker_name 写这个角色本人，text 写他此刻的想法（判断、动机、推理、犹豫或吐槽）；观众能听见他的声音，场内其他人物听不见——他可以正脸在画面里但嘴不动，其他人物不得因这段独白获得信息或作出回应。内容须有原文依据、符合该角色此刻的知识范围；允许压缩与口语化改写，不泄露后文，不把角色猜测写成已证实的事实；画面或对白已表达的信息不要用独白重复。另有一种不发声的 chat_message：手机或电脑屏幕上显示的聊天消息，speaker_name 写发消息的人，text 写消息原文，逐字取自原文、不超过36字（更长的只取到一个标点为止）；一个阶段最多八条（消息由插卡呈现，一个阶段可以带一整轮对话，不必为了拆消息而多写阶段）；群聊消息的 chat_target 留空；一对一私聊的消息把 chat_target 写成和主角私聊的那个人的名字——绝不能写主角自己，同一段私聊里每条消息（无论谁发的）都写同一个名字；私聊是两个人来回说话：对方发的消息 speaker_name 要写对方的名字，只有主角自己发的才写主角，不要把整段私聊都记成主角发的；同一阶段不要混用群聊和私聊。屏幕上的聊天界面由后期插卡渲染，画面里不需要拍清屏幕文字，含 chat_message 的阶段 start_state 和 event 只写看手机的人的动作与反应。原文里凡是聊天软件上的消息（形如「昵称：内容」的对话、群里的喊话、私聊），必须用 chat_message 呈现，一条都不许改成画外音、旁白或角色自己念出来。唱歌场景用 singing：speaker_name 写唱歌的人，text 只写演唱方式（如"轻声哼唱一段温柔的无词旋律"），绝不写任何歌词、歌名或已有歌曲，观众的反应用其他阶段的画面和画外音表现。silent_action只能写此刻能拍到的动作，不能用来表达回忆、气质评价或规则说明——心理活动用内心独白，不用 silent_action 硬拍。
4. 台词取舍：推动剧情和人物关系的原文台词必须保留——保留的是事实、人物意图、人物知识边界和因果顺序，不要求逐字；允许把冗长的原文对白改写成简洁完整的口语（换词、缩短、合并），但不得改变事实、态度和人物之间的关系。关键问答不能只留一半：上一句提出的问题，要么在同阶段或后续阶段有可听的回答，要么画面本身作出回答。重复表达同一意思的群众议论要合并成一两句或删掉。叙述里承载来历、规则和身份的信息（谁曾经是什么、某条规则意味着什么、某个称号指谁）用一两句无名族人的画外议论、角色问答或内心独白说出来，改成口语但不新增原文没有的事实。动机、决定、推断这类观众必须知道的信息，不能只用表情代替：要么写进台词，要么用内心独白，要么让一个动作清楚表达出来——皱眉和点头不算已经交代。
5. 每条turn的text不超过26个汉字，长句拆成多条turn。
6. 阶段字段：start_state写开始时画面（谁在哪、站位、朝向、表情、道具）；event写这几秒内的一个主要动作或事件；end_state写结束时能直接看到的状态（人物位置、朝向、表情、道具归属）；sfx写环境声或动作音效（如"人群低语""脚步声"），没有就空字符串，不要写"寂静声""注视声"这类不是声音的词；shot_scale写景别。情绪一律写成可见表现（眼神、眉头、嘴角、呼吸、手部动作），不写"气质如清莲""闪过一丝痛苦"这类拍不出来的词。不描述镜头运动、文字、字幕、Logo。相邻阶段不要重复同一个开始画面；同一次召唤、进入、离开或击打只能发生一次，后镜写已经造成的状态和下一步反应。长对白按情绪和动作转折分镜，给说话者与听者各自可见的反应，不写整段人物保持姿态不变。
   原文数量有歧义时，画面明确写几个独立身体、各自在做什么；若是一个身体多颗头或融合形态，写清身体与头的数量。不能把个体数量当成头数，也不能将真正多头的身体拆成多个个体；依据当前原文形态，不按物种名称猜测。
   camera写摄影机的物理位置，像一个在场的目击者：站在这个空间的哪里（灵碑侧后方、大厅长桌尽头、门框外、人群缝隙里）、离主体多远、高度是平视还是略低略高、前景有没有自然遮挡；摄影机静止，不写推拉摇移。
   camera与light按共用字段规则逐镜独立填写，描述当前可见状态，不用"电影感""氛围感"这类词。
   每段clip写avoid：本段具体不要出现的东西，用名词，例如"灵碑上不要出现可读文字""大厅不要出现现代家具""不要给楚焱红色发光的眼睛"；不写"低质量"这类空泛负面词。clip_id只写clip_1这样的短编号。
7. 画面描述不得出现血液、伤口、破皮、流血。灵碑、石碑、牌匾、纸张上不得出现可读文字或数字，一律写成"无字的发光纹路"；唯一允许的可读文字是手机或电脑屏幕上的聊天消息（用 chat_message 给出内容）。
8. clip.characters只填该段画面中出现的StoryBible具名角色；location只填给定地点名，优先选择与原文空间一致的具体地点，不能因旧地点有图就把卧房换成大堂。speaker_name填具名角色或给定的画外说话者；本章原文称呼（如酒吧老板、黄发老人）优先于泛化的无名商人、无名旅人，缺角色卡不改变说话者身份。没有角色卡的临时说话者用offscreen_dialogue，必要的可见活动用extras表达；不能将原文的动作安到其他人身上。silent_action和title_card的speaker_name留空字符串。
{scene_fields}
8c. 如果给了ledger_snapshot：它是原著逐段的出场记录，chapter_cast是本章在场/只有声音/只被提及的人，segments里是每个区段原文点到名的人。只让原文这一段在场的人进in_frame；segments里没点到、chapter_cast里又不在场的人不要出现；must_not_reveal里的关系此时读者还不知道，台词和画面都不得点破。
9. 只输出JSON。不要Markdown、不要解释、不要代码围栏。""".replace("{scene_fields}", field_instructions("planning")) + "\n" + TIMING_GUIDANCE


DEFAULT_ANONYMOUS_SPEAKERS = ["无名测验员", "无名族人", "无名少年", "无名少女", "无名群声"]
