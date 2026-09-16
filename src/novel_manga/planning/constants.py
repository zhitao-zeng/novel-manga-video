"""Existing planner defaults, schemas vocabulary and prompt text."""
from __future__ import annotations
from novel_manga.story.fields import field_instructions
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


DELIVERY_MODES = ["visible_dialogue", "offscreen_dialogue", "silent_action", "title_card", "chat_message", "singing"]


CHAT_MAX_CHARS = 36  # the card is drawn by us, so the bubble can hold a full line


SPLIT_PUNCT = "，。！？；：、…—,.!?;:"


STRIP_PUNCT = r"[\s　，。！？；：、…—,.!?;:\"“”'‘’（）()]"


FORBIDDEN_VISUAL = (
    (re.compile(r"(血迹|渗血|血液|流血|鲜血|伤口|破皮)"), "血液或伤口"),
    (re.compile(r"(大字|显示[“\"『「]|写着|字样|刻着[“\"]|显现出[“\"])"), "可读文字"),
)


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
        "clip_allocation": "片段安排：每段覆盖哪些原文区段、几个阶段，所有区段都要有位置。",
        "retained_dialogue": "保留的关键原文台词，以及承载叙述事实的合理对白或动作。",
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


UNCITED_ERROR = re.compile(r"^(seg_\d+) is neither cited by any shot")


STAGE_ERROR = re.compile(r"^([A-Za-z0-9_\-]{1,24} stage \d+): ")


CLIP_LEVEL_ERROR = re.compile(r"characters not in StoryBible|unknown location")


PATCH_ROUNDS = 3  # small repair calls per chapter before a full re-plan is the only option left


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
时长估算：每个发声汉字0.25秒，每句台词加1秒，每个阶段加1秒，无声动作阶段按4秒；按单段最多{clip_secs_hi}秒安排，全集目标约{episode_target}秒，规划上限{episode_max}秒。全集发声字数控制在{spoken_lo}到{spoken_hi}字之间。优先满足剧情完整性与片段预算，超长内容交给后续打包拆分，不得为凑时长新增或重复剧情。
硬规则：
1. 只用当前章的事实、人物和顺序。不得引入后文信息、新事件、新地点，或StoryBible之外的具名角色。
2. 原文已切成{segment_count}个连续区段 seg_1 到 seg_{segment_count}。每个阶段必须写 segment_id，并把该区段里一段连续原文逐字复制到 source_quote（8到120字；不得改字、不得拼接）。每个区段都必须至少被一个阶段引用，一个都不许跳过；skipped_segments 必须是空数组 []。每个区段用1到{stages_per_segment}个阶段带过：内容多的区段把对话压成一两句、把过程并成一个阶段，也不能整段不拍。
3. 成片没有旁白、没有内心独白。可听的只有四种：visible_dialogue（画内可见说话者，一个阶段只允许一个可见说话者）、offscreen_dialogue（画外声：群众议论、测验员喊话等）、silent_action（无声的可见动作或反应，text写动作）、title_card（时间或地点跳转的字幕卡，只在必要时用）。另有一种不发声的 chat_message：手机或电脑屏幕上显示的聊天消息，speaker_name 写发消息的人，text 写消息原文，逐字取自原文、不超过36字（更长的只取到一个标点为止）；一个阶段最多八条（消息由插卡呈现，一个阶段可以带一整轮对话，不必为了拆消息而多写阶段）；群聊消息的 chat_target 留空；一对一私聊的消息把 chat_target 写成和主角私聊的那个人的名字——绝不能写主角自己，同一段私聊里每条消息（无论谁发的）都写同一个名字；私聊是两个人来回说话：对方发的消息 speaker_name 要写对方的名字，只有主角自己发的才写主角，不要把整段私聊都记成主角发的；同一阶段不要混用群聊和私聊。屏幕上的聊天界面由后期插卡渲染，画面里不需要拍清屏幕文字，含 chat_message 的阶段 start_state 和 event 只写看手机的人的动作与反应。原文里凡是聊天软件上的消息（形如「昵称：内容」的对话、群里的喊话、私聊），必须用 chat_message 呈现，一条都不许改成画外音、旁白或角色自己念出来。唱歌场景用 singing：speaker_name 写唱歌的人，text 只写演唱方式（如"轻声哼唱一段温柔的无词旋律"），绝不写任何歌词、歌名或已有歌曲，观众的反应用其他阶段的画面和画外音表现。silent_action只能写此刻能拍到的动作，不能用来表达回忆、心理活动、气质评价或规则说明。
4. 台词取舍：推动剧情和人物关系的原文台词必须保留，可以只删子句、不改词序；重复表达同一意思的群众议论要合并成一两句或删掉。叙述里承载来历、规则和身份的信息（谁曾经是什么、某条规则意味着什么、某个称号指谁）用一两句无名族人的画外议论或角色问答说出来，改成口语但不新增原文没有的事实。内心独白不要改成出声自语，改成可见反应。
5. 每条turn的text不超过26个汉字，长句拆成多条turn。
6. 阶段字段：start_state写开始时画面（谁在哪、站位、朝向、表情、道具）；event写这几秒内的一个主要动作或事件；end_state写结束时能直接看到的状态（人物位置、朝向、表情、道具归属）；sfx写环境声或动作音效（如"人群低语""脚步声"），没有就空字符串，不要写"寂静声""注视声"这类不是声音的词；shot_scale写景别。情绪一律写成可见表现（眼神、眉头、嘴角、呼吸、手部动作），不写"气质如清莲""闪过一丝痛苦"这类拍不出来的词。不描述镜头运动、文字、字幕、Logo。相邻阶段不要重复同一个开始画面。
   原文数量有歧义时，画面明确写几个独立身体、各自在做什么；若是一个身体多颗头或融合形态，写清身体与头的数量。不能把个体数量当成头数，也不能将真正多头的身体拆成多个个体；依据当前原文形态，不按物种名称猜测。
   camera写摄影机的物理位置，像一个在场的目击者：站在这个空间的哪里（灵碑侧后方、大厅长桌尽头、门框外、人群缝隙里）、离主体多远、高度是平视还是略低略高、前景有没有自然遮挡；摄影机静止，不写推拉摇移。
   light先写真实光源再写效果：主光源是什么、从哪个方向来（月光从左上、案头油灯在右侧、灵碑纹路的金光从下方），次光源是什么，阴影落在哪里，冷暖关系如何；同一段内光源不能凭空改变，不用"电影感""氛围感"这类词。
   camera和light各不超过40个汉字。同一段clip里光源不变时，后续阶段的light直接写"同上"；机位不变时camera也可写"同上"。
   每段clip写avoid：本段具体不要出现的东西，用名词，例如"灵碑上不要出现可读文字""大厅不要出现现代家具""不要给楚焱红色发光的眼睛"；不写"低质量"这类空泛负面词。clip_id只写clip_1这样的短编号。
7. 画面描述不得出现血液、伤口、破皮、流血。灵碑、石碑、牌匾、纸张上不得出现可读文字或数字，一律写成"无字的发光纹路"；唯一允许的可读文字是手机或电脑屏幕上的聊天消息（用 chat_message 给出内容）。
8. clip.characters只填该段画面中出现的StoryBible具名角色；location只填给定地点名。speaker_name是具名角色，或"无名测验员""无名族人"这类无名画外角色；无名角色只能用offscreen_dialogue。silent_action和title_card的speaker_name留空字符串。
{scene_fields}
8c. 如果给了ledger_snapshot：它是原著逐段的出场记录，chapter_cast是本章在场/只有声音/只被提及的人，segments里是每个区段原文点到名的人。只让原文这一段在场的人进in_frame；segments里没点到、chapter_cast里又不在场的人不要出现；must_not_reveal里的关系此时读者还不知道，台词和画面都不得点破。
9. 只输出JSON。不要Markdown、不要解释、不要代码围栏。""".replace("{scene_fields}", field_instructions("planning"))


DEFAULT_ANONYMOUS_SPEAKERS = ["无名测验员", "无名族人", "无名少年", "无名少女", "无名群声"]
