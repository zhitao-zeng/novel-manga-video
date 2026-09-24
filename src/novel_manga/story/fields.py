"""One definition of scene fields; preserve the existing prompt renderings."""
from copy import deepcopy


def cast_field(names):
    return {"type": "array", "maxItems": 6 if names else 0,
            "items": {"type": "string", **({"enum": names} if names else {})}}


def speaker_field(names, anonymous=(), *, allow_empty=True):
    return {"type": "string", "enum": [*names, *anonymous, *([""] if allow_empty else [])]}


ACTION_FIELD = {'type': 'array',
 'maxItems': 3,
 'items': {'type': 'object',
           'additionalProperties': False,
           'required': ['actor', 'action', 'target'],
           'properties': {'actor': {'type': 'string', 'maxLength': 80},
                          'action': {'type': 'string'},
                          'target': {'type': 'string', 'maxLength': 80}}}}

EXTRAS_FIELD = {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}}

def actions_field():
    return deepcopy(ACTION_FIELD)


def extras_field():
    return deepcopy(EXTRAS_FIELD)


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
             '每个阶段的in_frame只填这一阶段画面里真正出现的具名角色，是clip.characters的子集；原文里只被提起、在别处、或只有声音的人不进in_frame，他们的话用offscreen_dialogue。actions写这一阶段谁对谁做了什么：actor和target可以是具名角色、extras里的描述，或原文中明确的动物、道具、环境对象；它们不局限于in_frame名单。action是谓语短语（如"环住脖子吻住"、"向后仰头避开"），不含主体名字。没有动作就留空数组；无受事或无法确定目标时target=""，不得挑一个已有角色补位，也不得默认填动作发起者自己。例如主角砍山羊，target写"灰色野山羊"，不是主角的名字。extras只放无角色卡的无名人物或动物，不能放机甲、车辆、武器等物件；有圣经登记的资产物件且 Schema 提供 props 时填 props，未登记的本场普通物件填 scene_objects。有圣经穿戴物且 Schema 提供 wears 时标明穿戴者，不能把穿戴物写成独立演员。每阶段只写本场实际在场的物件，不能把下一场的车辆提前放进室内。具名角色不能靠写进extras替代其身份绑定。有可见说话者的阶段，in_frame只放说话的人和这一阶段与他有动作往来的人，听的人不进in_frame（相邻阶段轮流给两人正脸，像正反打）；两张脸同框时视频模型常把口型安错人。',
 'repair': '你在修一段动画短剧的分镜。判官对照原文发现这段画面把动作或台词安错了人，或漏了人。下面给你：这段原文、现有分镜的各阶段、原著账本记的这段谁在场、判官的意见。只输出 '
           'JSON。对每个阶段（按 origin_index）重写：\n'
           'in_frame：这一阶段画面里真正出现的具名人物（只能从候选名单选；原文里只被提起、在别处、或只有声音的人不进）。\n'
           '涉及个体与头数时，按原文写清独立身体的数量及各自动作；一个身体多颗头或融合形态另写头数，不能由物种名称或量词猜测。\n'
           'actions：这一阶段谁对谁做了什么。actor/target 可以是具名角色、extras描述，或原文明示的动物、道具、环境对象，不受 in_frame 名单限制。action '
           '是谓语短语（如“环住脖子吻住”“递过信封”）。无动作就空数组，无受事或目标不明确则 target 留空；不能随便挑已有角色补位或默认填自己。\n'
           'extras：原文里在场、有动作或台词、但没有专属角色卡的无名人物、动物等，用简短描述（如“戴眼镜的灰发老妇人”“灰色野山羊”），没有就空数组。机甲、车辆、武器等物件不进 extras，沿用现有 props 或 scene_objects；穿戴物仍归穿戴它的人，不是另一位演员。具名角色仍须核对其身份。\n'
           'event：改写后的事件句，一句话写清谁做什么，先写动作再写其余。\n'
           '有可见说话者的阶段，in_frame 只放说话的人和这一阶段与他有动作往来的人（听的人不进）。判官说缺席的人若原文这段确实在场，必须进 in_frame。'}


def field_instructions(entry):
    return FIELD_INSTRUCTIONS[entry]


def dialogue_instructions():
    """Delivery semantics also used before a screenplay is split into shots."""
    return ("delivery_mode必须按实际发声填写：人物真实开口说话用visible_dialogue；"
            "确实来自画外且画内不对口型的台词用offscreen_dialogue；"
            "chat_message仅用于原文真实的屏幕聊天消息；singing仅用于明确的无歌词哼唱，"
            "它的text只能写哼唱方式，不能放普通台词。喃喃自语仍是说话，不是singing。"
            "没有对白时turns=[]，不能把动作或导演说明塞进台词。")
