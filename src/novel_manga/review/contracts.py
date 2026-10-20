"""Existing review schemas, questions and policy version. No file access or scheduling."""
from __future__ import annotations

import novel_manga.llm.client as model_client
REVIEW_POLICY = "thin-review-v1.25-watchable-core-events"


POLICY = REVIEW_POLICY  # shared with scheduling and delivery: the judge reads the source passage


def current_review(report: dict, mode: str | None) -> bool:
    """A verdict from another mode cannot stand in for the requested checks."""
    return (report.get('policy') == POLICY
            and report.get('review_mode') == ('verify' if mode == 'verify' else 'classic'))


PHOTOREAL_LIMIT = 0.6


MIN_MENTIONS = 3


FRAMES_PER_CLIP = 4


FRAME_WIDTH = 960


CARD_SIDE = 1024


MAX_IMAGES = 8  # vLLM --limit-mm-per-prompt


STORY_KINDS = ("无问题", "原文中有动作的人物缺席", "动作落在错误的人物身上", "画面事件与原文不符", "无法判断")


STORY_FATAL = {"原文中有动作的人物缺席", "动作落在错误的人物身上"}


NAME_SCHEMA = model_client.obj({
    "characters": {"type": "array", "items": model_client.obj({
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["具名角色", "称呼或身份", "群体"]},
        "mentions": {"type": "integer"},
        "speaks_or_close_up": {"type": "boolean"},
    })},
})


FILL_SCHEMA = model_client.obj({"characters": {"type": "array", "items": model_client.obj({
    "name": {"type": "string"}, "same_as": {"type": "string"}, "confidence": {"type": "number"},
    "role": {"type": "string"}, "gender": {"type": "string"}, "age": {"type": "string"},
    "appearance": {"type": "string"}, "wardrobe": {"type": "string"}, "hair": {"type": "string"},
    "palette": {"type": "string"}, "base_costume": {"type": "string"}, "signature_prop": {"type": "string"},
})}})


def fill_schema(names: list[str]) -> dict:
    """Return one bounded design per requested name, rather than the existing cast."""
    import copy
    entries = []
    for name in names:
        entry = copy.deepcopy(FILL_SCHEMA['properties']['characters']['items'])
        entry['properties']['name'] = {'type': 'string', 'enum': [name]}
        entry['properties']['confidence'].update(minimum=0, maximum=1)
        for field, definition in entry['properties'].items():
            if definition.get('type') == 'string' and field != 'name':
                definition['maxLength'] = 200 if field in {'appearance', 'wardrobe'} else 100
        entries.append(entry)
    return model_client.obj({'characters': {'type': 'array', 'minItems': len(entries),
        'maxItems': len(entries), 'prefixItems': entries, 'items': False}})


LOCATION_SCHEMA = model_client.obj({"locations": {"type": "array", "items": model_client.obj({
    "name": {"type": "string"}, "description": {"type": "string"}, "scene_count": {"type": "integer"},
    "time_of_day": {"type": "string", "enum": ["白天", "夜晚", "黄昏", "清晨", "不定"]},
    "main_light": {"type": "string"},
})}})


PROP_SCHEMA = model_client.obj({"props": {"type": "array", "items": model_client.obj({
    "name": {"type": "string"},
    "category": {"type": "string", "enum": ["武器", "信物", "法器", "工具", "其他"]},
    "appearance": {"type": "string"}, "material": {"type": "string"}, "owner": {"type": "string"},
    "quote": {"type": "string"},
    "closeup": {"type": "boolean"}, "wearable": {"type": "boolean"},
})}})


CHARACTER_CARD_SCHEMA = model_client.obj({
    "photoreal": {"type": "number"},
    "matches_description": {"type": "boolean"},
    "mismatch": {"type": "string"},
    "same_person": {"type": "boolean"},
    "text_or_extra_people": {"type": "boolean"},
    "note": {"type": "string"},
})


LOCATION_CARD_SCHEMA = model_client.obj({
    "has_people": {"type": "boolean"},
    "time_of_day": {"type": "string", "enum": ["白天", "夜晚", "黄昏或清晨", "室内不确定"]},
    "text": {"type": "boolean"},
    "matches_description": {"type": "boolean"},
    "note": {"type": "string"},
})


CLIP_SCHEMA = model_client.obj({
    "visible_people": {"type": "integer"},
    "identity_ok": {"type": "boolean"},
    "identity_issue": {"type": "string"},
    "location_ok": {"type": "boolean"},
    "time_of_day_ok": {"type": "boolean"},
    "location_issue": {"type": "string"},
    "text_or_watermark": {"type": "boolean"},
    "chat_text_ok": {"type": "boolean"},
    "chat_text_issue": {"type": "string"},
    "visual_defects": {"type": "boolean"},
    "defect_issue": {"type": "string"},
    "story_ok": {"type": "boolean"},
    "story_kind": {"type": "string", "enum": list(STORY_KINDS)},
    "story_issue": {"type": "string"},
    "severity": {"type": "string", "enum": ["pass", "minor", "fail"]},
    "feedback": {"type": "string"},
})


SCRIPT_CHECK_SCHEMA = {
    "type": "object", "required": ["scripted", "evidence", "note"],
    "properties": {"scripted": {"type": "boolean"}, "evidence": {"type": "string"}, "note": {"type": "string"}},
}


SCRIPT_CHECK_RULES = (
    "下面是一段 AI 短剧画面对应的剧本事件和原文段落，以及审查员看了生成画面后指出的问题。\n"
    "判断：审查员指出的异常，是不是剧本事件或原文明确写到的画面？例如原文写“掌心裂开一张嘴”，"
    "审查员说“手掌中心出现嘴巴属于崩坏”，那就是剧本要求的（scripted=true），evidence 抄原文里对应的那句。\n"
    "只有原文或剧本事件确实写到了同一件事才算剧本要求。以下都不算：发型、服装、年龄、性别与设定不符；"
    "多出了原文没有写的人；同一个人在画面里出现两次而原文没有写两个人；地点或昼夜不对；画面上有文字。"
    "原文段落可能跨多个镜头，只有当前镜头阶段明确要求的同一事件才可判 scripted=true；"
    "后续或其他镜头的异象不能为当前镜头豁免。结合本镜实际请求、事件及阶段绑定定位，"
    "剧本与原文明示内容冲突或缺少当前阶段依据时填 false。"
    "拿不准就填 false。note 用一句话说明。只输出 JSON。"
)


WATCHABLE_CRITERIA = (
    '验收以观众能顺畅看懂剧情为准，允许正常剪辑、表演简化和不改变关键因果的合理改编。'
    '不影响人物识别或关键事件的服饰、配饰、发色、外貌细节、口型、站坐、手脚摆放和动作幅度差异，'
    '非关键动作省略、背景布置、无关背景人物增减和道具局部形状差异，只记提示，不要求重拍。'
    '旧分镜或提示词写得精确，不等于这些细节本身决定剧情；背景多一名旁观者不等于关键人物错误。'
    '服饰或物件承担关键身份、防护、交接或功能，人数决定事件，或动作差异改变生死、行动能力与攻击防御时，仍须修复。'
    '关键对白错漏或意思反转、明显错人或性别物种错误、人物分身、关键角色缺席、动作归属错误、关键事件缺失、'
    '剧情必需群体完全未出现、额外肢体、严重头身或肢体比例失真、持续严重抖动，仍必须修复。'
    '不能只凭与原文或旧请求有差别判失败，必须指出观众会误认的关键人物、误解的事件或可见的严重画面缺陷。'
    '无法判断或审核未完成不能记通过；一项轻微差异不能清除其他独立失败。'
)


VERIFY_FLAGS = ('same_person_twice', 'species_or_gender_wrong', 'action_by_wrong_person',
                'actor_missing', 'lead_face_swapped', 'body_artifact', 'scene_missing')

BODY_QUESTION = (
    "body_artifact：对照参考图与实际视频，沿肩—肘—腕—手的连接检查是否多出独立手臂、腿或机械附肢，"
    "或存在明显断裂/重复连接。不能只数人头而忽略同一身体的额外分支；遮挡、裁切不算缺失，"
    "还须对照参考人物检查头、躯干与四肢的比例是否明显失真；俯身、蜷腿、近大远小及正常动画风格不单独算错，"
    "但头部异常膨大、躯干或四肢明显缩短且无法由构图透视解释时填true，写明可见部位和连续帧依据。"
    "原文明确的非人形态按其实际结构判断。evidence写具体视频帧与连接位置。\n"
)

VERIFY_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["people", *VERIFY_FLAGS, "ghost_text", "count_checks", "verdict", "evidence", "instruction"],
                 "properties": {
                     "people": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["who", "entity_kind", "gender", "is_animal", "doing", "frames"],
                                                           "properties": {"who": {"type": "string"}, "entity_kind": {"type": "string", "enum": ["character", "object"]}, "gender": {"type": "string", "enum": ["男", "女", "不明"]},
                                                                          "is_animal": {"type": "boolean"}, "doing": {"type": "string"}, "frames": {"type": "string"}}}},
                     "body_artifact": {"type": "boolean"}, "scene_missing": {"type": "boolean"},
                     "same_person_twice": {"type": "boolean"}, "species_or_gender_wrong": {"type": "boolean"}, "action_by_wrong_person": {"type": "boolean"},
                     "actor_missing": {"type": "boolean"}, "lead_face_swapped": {"type": "boolean"}, "ghost_text": {"type": "boolean"},
                     "count_checks": {"type": "array", "minItems": 1, "maxItems": 24, "items": {
                         "type": "object", "additionalProperties": False,
                         "required": ["count_kind", "entity", "frames", "expected_min", "expected_max", "observed", "instances", "reason"],
                         "properties": {"count_kind": {"type": "string", "enum": ["characters", "worn_equipment", "independent_objects", "background_people"]}, "entity": {"type": "string"}, "frames": {"type": "string"},
                                        "expected_min": {"type": "integer", "minimum": 0},
                                        "expected_max": {"type": "integer", "minimum": 0},
                                        "observed": {"type": "integer", "minimum": 0},
                                        "instances": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 120}},
                                        "reason": {"type": "string", "maxLength": 180}}}},
                     "verdict": {"type": "string", "enum": ["obvious", "subtle", "fine"]}, "evidence": {"type": "string"},
                     "instruction": {"type": "string"}}}


VERIFY_QUESTIONS = (WATCHABLE_CRITERIA + '\n' + BODY_QUESTION +
    'background_people仅用于无名且人数不决定剧情的背景群体；具名人物和剧情要求定量的群体仍填characters。'
    '背景群体已经出现但人数有差异时verdict填subtle并保留实际计数；observed=0的整群缺席仍是obvious。'
    "\n先逐个描述视频帧里看到的角色及人形物件（people：who 是谁或长相，entity_kind 为 character 或 object，gender，is_animal，doing 在做什么，frames 出现在哪几帧）。有身份和行为的角色填character；无人空甲、雕像、人体模型等物件填object，不能因为它们长得像人就填character，再回答：\n"
    "count_checks：必须数实际视频帧，不数参考卡。按镜头阶段分三类：count_kind=characters只数entity_kind=character的角色；worn_equipment数剧情指定的穿戴装备，entity填写具体装备名，不混入普通衬衫、马甲；independent_objects数独立无人甲、车辆等关键物件，entity填写具体物件名。"
    "每项写entity、选定的一张视频帧号frames、按原文和本镜构图允许的expected_min/expected_max、实际observed数量，以及依据reason。"
    "characters且expected_max=1时，instances逐一列出同一帧各个独立身体的位置与可见部位，observed必须等于条数，不按姓名去重；其他计数行instances可留空，背景群众无需逐人描述。"
    "例如左前肩背与右侧正面属于同一个名字，若同时是两具身体也必须列两项；不同帧转身不累加，有明确镜面边界和反射关系的倒影不另计。参考卡多视图不计视频人数。"
    "人物穿甲是一个人和一件穿戴物，不是两个演员；无人空甲不是人。穿甲者露出正常人脸是允许的，不要求透过装甲看见人体。"
    "一人穿甲加一套独立空甲，在召来空甲之后可以成立；召来之前不得提前出现。不要仅因两套甲设计相同就判克隆。"
    "切近景、画外人物和遮挡可能减少可见人数，应根据本镜构图给合理范围，不强迫全剧演员每帧到齐。"
    "同一画面里多出身体、空甲或车辆，要分别计数，不能只核对具名角色。至少给出一项count_kind=characters的数量检查。例如同框两个人，其中一人穿甲，旁边另有一套无人空甲：角色2、穿戴装甲1、独立空甲1，人物总数不能填3。同一件装备的穿戴状态和独立状态分行，不能混为一类。\n"
    "same_person_twice：同一视频帧不同位置是否同时出现同一角色的两个独立身体，包括正面与背影、遮挡或裁切的身体，不要求两张脸都可见；\n"
    "scene_missing：剧情所需的具体场所是否整体消失或明显错位（如街道变纯色定妆背景）；装饰色差、裁切或虚化不算，只有影响理解的场景丢失填true。\n"
    "species_or_gender_wrong：人被画成动物、动物被画成人或别的动物、动物直立拟人化，或原文里的女人由男人演（反之）、成人画成小孩；\n"
    "action_by_wrong_person：原文里甲做的动作或说的话，画面里由乙做或对错的对象做（例如原文甲向乙递东西，画面却由丙递出）；\n"
    "actor_missing：原文对应本镜事件中应入镜、有动作或对白的人不在画面里，而且没有别人替他做（只露背影的听者、画外说话的人不算缺席；其他镜头事件中的人不必在本镜出现）；\n"
    "lead_face_swapped：主角或其他给了角色卡的主要人物，脸型发型明显不是角色卡上那个人（追剧的观众认得主角，这也算一眼看出）；\n"
    "ghost_text：画面出现字幕、文字、水印、Logo（手机屏幕上的消息除外）；这一项单独记录，不影响 verdict；\n"
    "verdict：obvious＝观众会觉得荒谬或剧情含义改变；仅姿态与请求不同但事件含义不变应填subtle，并在evidence保留实际差异（same_person_twice、species_or_gender_wrong、action_by_wrong_person、actor_missing、"
    "lead_face_swapped、body_artifact、scene_missing 任一成立就是 obvious）；subtle＝只有对照角色卡才看得出的配角差别（发色、服装、帽子、徽章）；fine＝没问题；\n"
    "evidence：一句话，写清哪几帧、谁、看到了什么；\n"
    "instruction：给视频生成模型的一句修正指令，只说画面里该有谁、谁对谁做什么、谁只能出现一次或不该出现，点名到人，不复述错误、不提上一次；verdict 为 fine 时留空。只输出JSON。"
)


INSTRUCTION_SCHEMA = model_client.obj({"instruction": {"type": "string"}})
