"""Shared scene field definitions and authoring instructions."""
from copy import deepcopy

ACTION_KEYS = ("actor", "action", "target")
TURN_REQUIRED_TEXT = ("speaker_name", "delivery_mode", "text")
TURN_KEYS = (*TURN_REQUIRED_TEXT, "emotion", "chat_target", "inner_monologue")


def cast_field(names):
    return {"type": "array", "maxItems": 6 if names else 0,
            "items": {"type": "string", **({"enum": names} if names else {})}}


def speaker_field(names, anonymous=(), *, allow_empty=True):
    return {"type": "string", "enum": [*names, *anonymous, *([""] if allow_empty else [])]}


ACTION_FIELD = {'type': 'array',
 'maxItems': 3,
 'items': {'type': 'object',
           'additionalProperties': False,
           'required': list(ACTION_KEYS),
           'properties': {'actor': {'type': 'string', 'maxLength': 80},
                          'action': {'type': 'string'},
                          'target': {'type': 'string', 'maxLength': 80}}}}

EXTRAS_FIELD = {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}}

def actions_field():
    return deepcopy(ACTION_FIELD)


def extras_field():
    return deepcopy(EXTRAS_FIELD)


def scene_objects_field(*, max_items=3, max_length=80):
    return {'type': 'array', 'maxItems': max_items,
            'items': {'type': 'string', **({'maxLength': max_length} if max_length is not None else {})}}


def props_field(names=None, *, max_items=2):
    return {'type': 'array', 'maxItems': max_items,
            'items': {'type': 'string', **({'enum': list(names)} if names is not None else {})}}


def wears_field(names=None):
    return {'type': 'object', 'additionalProperties': {
        'type': ['string', 'null'], **({'enum': [*names, None]} if names is not None else {})}}


def turn_field(character_names, anonymous_speakers, delivery_modes):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["speaker_name", "delivery_mode", "text", "emotion", "chat_target"],
        "properties": {
            "speaker_name": speaker_field(character_names, anonymous_speakers),
            "delivery_mode": {"type": "string", "enum": delivery_modes},
            "text": {"type": "string"},
            "emotion": {"type": "string"},
            "inner_monologue": {"type": "boolean", "description": "仅角色心声为true；delivery_mode仍用offscreen_dialogue，普通画外对白为false"},
            "chat_target": speaker_field(character_names),  # chat_message only: empty = group chat, a name = a one-to-one chat
        },
    }


FIELD_INSTRUCTIONS = {'planning': '8b. '
             '每个阶段的in_frame只填这一阶段画面里真正出现的具名角色，是clip.characters的子集；原文里只被提起、在别处、或只有声音的人不进in_frame，他们的话用offscreen_dialogue。actions写这一阶段谁对谁做了什么：actor和target可以是具名角色、extras里的描述，或原文中明确的动物、道具、环境对象；它们不局限于in_frame名单。action是谓语短语（如"环住脖子吻住"、"向后仰头避开"），不含主体名字。没有动作就留空数组；无受事或无法确定目标时target=""，不得挑一个已有角色补位，也不得默认填动作发起者自己。例如主角砍山羊，target写"灰色野山羊"，不是主角的名字。extras只放无角色卡的无名人物或动物，不能放机甲、车辆、武器等物件；有圣经登记的资产物件且 Schema 提供 props 时填 props，未登记的本场普通物件填 scene_objects。有圣经穿戴物且 Schema 提供 wears 时标明穿戴者，不能把穿戴物写成独立演员。每阶段只写本场实际在场的物件，不能把下一场的车辆提前放进室内。具名角色不能靠写进extras替代其身份绑定。in_frame表示可见性，不表示谁说话或谁正脸：画面里看得见的听者也要保留。正脸、背影、站位和机位由start_state与camera明确写出；画外动作主体或目标不因此进入in_frame。event必须独立、完整地写清事件与先后顺序，actions用于归属核对，不会由程序追加到事件前面。',
 'repair': '你在修一段动画短剧的分镜。判官对照原文发现这段画面把动作或台词安错了人，或漏了人。下面给你：这段原文、现有分镜的各阶段、原著账本记的这段谁在场、判官的意见。只输出 '
           'JSON。对每个阶段（按 origin_index）重写：\n'
           'in_frame：这一阶段画面里真正出现的具名人物（只能从候选名单选；原文里只被提起、在别处、或只有声音的人不进）。\n'
           '涉及个体与头数时，按原文写清独立身体的数量及各自动作；一个身体多颗头或融合形态另写头数，不能由物种名称或量词猜测。\n'
           'actions：这一阶段谁对谁做了什么。actor/target 可以是具名角色、extras描述，或原文明示的动物、道具、环境对象，不受 in_frame 名单限制。action '
           '是谓语短语（如“环住脖子吻住”“递过信封”）。无动作就空数组，无受事或目标不明确则 target 留空；不能随便挑已有角色补位或默认填自己。\n'
           'extras：原文里在场、有动作或台词、但没有专属角色卡的无名人物、动物等，用简短描述（如“戴眼镜的灰发老妇人”“灰色野山羊”），没有就空数组。机甲、车辆、武器等物件不进 extras，沿用现有 props 或 scene_objects；穿戴物仍归穿戴它的人，不是另一位演员。具名角色仍须核对其身份。\n'
           'event：改写后的事件句，一句话写清谁做什么，先写动作再写其余。\n'
           'in_frame只表示本镜实际可见的人，包含可见听者；不因不说话而移出画面，也不因是动作主体或目标就强迫入镜。按原文和当前机位修正可见范围；同处一个场景不等于本镜可见。event保留完整因果顺序，不能依赖程序拼接actions补写。'}


DIRECTION_CONTENT_RULES = (
    "导演可以创作机位、节奏和当前机位能看见的必要反应，但不能增加改变剧情事实的装备功能、"
    "特效、人物或关键物件。动作音效应有本镜动作或环境中的声源，不能为了气氛凭空增加声源或能力。"
    "穿戴装备属于穿戴者，只有剧情明确要求的无人副本才是独立物件；面罩状态及变化要与本镜事件一致。"
)


def field_instructions(entry):
    instructions = SCENE_STATE_INSTRUCTIONS
    if entry == 'repair':
        instructions = '修复时只填写当前Schema提供的字段。' + instructions.replace('start_state', 'visual_prompt')
    return FIELD_INSTRUCTIONS[entry] + '\n' + instructions + ('\n' + DIRECTION_CONTENT_RULES if entry == 'repair' else '')


CAMERA_INSTRUCTIONS = (
    '机位与运镜按剧情需要设计，固定或移动由本镜的信息揭示、人物行动与情绪变化决定。'
    '写清摄影机所在位置、高度、朝向与取景范围；移动时写清为什么动、由哪个动作或发现触发，'
    '从什么位置和景别沿什么路径移到什么位置和景别，以及在哪个动作完成、信息看清或时间点停住。'
    '若移动持续到切镜，明确写出切镜时的终点；固定镜头写清观察对象及保持到哪个动作或切点。'
    '运镜、人物动作与时长应能同时实现，前后镜的空间、视线及人物与道具状态接得上。'
    '按当镜取景写前景人物实际可见的肩、腰、腿等局部与远处主体；场内站位不等于每人都要完整入画。'
    '参考图提供身份和造型，镜头正文先交代画内范围，不用全场人物或全身造型介绍代替当前构图。'
    '全书视觉语法中的固定机位偏好不作为逐镜禁动要求。'
    '选型时区分机位构图、相机运动与焦点：对话可用双人景、过肩或带前景单人，'
    '道具关联可用过腰或前中后景分层，空间关系可用高低机位、顶拍、框中框、留白或主观视角；'
    '移动可选推近、拉远、摇摄、横移、升降、跟随、弧移或环绕，按剧情决定速度与先后。'
    '涉及遮挡揭示或拉焦时，写清前景、主体、背景的关系，以及起始清晰对象、触发和最终清晰对象；'
    '相机不动也可转移焦点。组合手法写成实际路径与信息出现顺序，不只写导演风格名。'
    '先明确本场观众要发现、判断或感受的变化，以及谁先知道、哪些信息暂不露出，再逐镜确定观看位置与切点。'
    '认知冲击可考虑希区柯克式眩晕变焦（主体大小近似不变、背景比例变化）；探索未知空间可考虑库布里克式低位稳定跟拍；'
    '工整关系中的注意转移可考虑韦斯·安德森式对称起幅配甩镜或快变焦；抽离感可考虑斯派克·李式人物与相机共同平移；'
    '近处动作与远处反应需同时可读时，可考虑德·帕尔玛式分割焦点。根据本场需要选择，仍写实际可见过程与停住条件。'
    '不为用齐手法添加剧情；跨镜声音衔接属于剪辑安排，不当作单镜相机运动。'
)


SCENE_STATE_INSTRUCTIONS = (
    '填写camera和light时，每个阶段独立、完整填写，不能只写同上或接上一镜。'
    + CAMERA_INSTRUCTIONS +
    'light不超过40个汉字，写当前持续存在的环境光源、方向与明暗，没有次光源就不补。'
    '喷气、闪光等随动作产生的短暂光效写在event里，并在event或end_state说明出现与停止，'
    '不把已结束的事件光效写成后续对白镜头的常亮光源；相应动作声仅在发生时写入sfx。'
    'wears的值仍用登记的资产名；面罩开合等当前外观状态写进start_state和end_state，'
    '只有event明确发生变化时才改变后续状态，不能在资产名后追加状态导致引用失配。'
)


def dialogue_instructions():
    """Delivery semantics also used before a screenplay is split into shots."""
    return ("delivery_mode必须按实际发声填写：人物真实开口说话用visible_dialogue；"
            "确实来自画外且画内不对口型的台词用offscreen_dialogue；"
            "chat_message仅用于原文真实的屏幕聊天消息；singing仅用于明确的无歌词哼唱，"
            "它的text只能写哼唱方式，不能放普通台词。喃喃自语仍是说话，不是singing。"
            "没有对白时turns=[]，不能把动作或导演说明塞进台词。")


def sound_effects_field() -> dict:
    """Physical sound belonging to this stage; an empty string removes an obsolete effect."""
    return {"type": "string"}
