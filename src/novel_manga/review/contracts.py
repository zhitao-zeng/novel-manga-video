"""Existing review schemas, questions and policy version. No file access or scheduling."""
from __future__ import annotations

import novel_manga.llm.client as model_client
REVIEW_POLICY = "thin-review-v1.17-story"


POLICY = REVIEW_POLICY  # shared with scheduling and delivery: the judge reads the source passage


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
    "拿不准就填 false。note 用一句话说明。只输出 JSON。"
)


VERIFY_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["people", "same_person_twice", "species_or_gender_wrong", "action_by_wrong_person", "actor_missing",
                              "lead_face_swapped", "ghost_text", "count_checks", "verdict", "evidence", "instruction"],
                 "properties": {
                     "people": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["who", "entity_kind", "gender", "is_animal", "doing", "frames"],
                                                           "properties": {"who": {"type": "string"}, "entity_kind": {"type": "string", "enum": ["character", "object"]}, "gender": {"type": "string", "enum": ["男", "女", "不明"]},
                                                                          "is_animal": {"type": "boolean"}, "doing": {"type": "string"}, "frames": {"type": "string"}}}},
                     "same_person_twice": {"type": "boolean"}, "species_or_gender_wrong": {"type": "boolean"}, "action_by_wrong_person": {"type": "boolean"},
                     "actor_missing": {"type": "boolean"}, "lead_face_swapped": {"type": "boolean"}, "ghost_text": {"type": "boolean"},
                     "count_checks": {"type": "array", "minItems": 1, "maxItems": 24, "items": {
                         "type": "object", "additionalProperties": False,
                         "required": ["count_kind", "entity", "frames", "expected_min", "expected_max", "observed", "reason"],
                         "properties": {"count_kind": {"type": "string", "enum": ["characters", "worn_equipment", "independent_objects"]}, "entity": {"type": "string"}, "frames": {"type": "string"},
                                        "expected_min": {"type": "integer", "minimum": 0},
                                        "expected_max": {"type": "integer", "minimum": 0},
                                        "observed": {"type": "integer", "minimum": 0},
                                        "reason": {"type": "string", "maxLength": 180}}}},
                     "verdict": {"type": "string", "enum": ["obvious", "subtle", "fine"]}, "evidence": {"type": "string"},
                     "instruction": {"type": "string"}}}


VERIFY_QUESTIONS = (
    "\n先逐个描述视频帧里看到的角色及人形物件（people：who 是谁或长相，entity_kind 为 character 或 object，gender，is_animal，doing 在做什么，frames 出现在哪几帧）。有身份和行为的角色填character；无人空甲、雕像、人体模型等物件填object，不能因为它们长得像人就填character，再回答：\n"
    "count_checks：必须数实际视频帧，不数参考卡。按镜头阶段分三类：count_kind=characters只数entity_kind=character的角色；worn_equipment数剧情指定的穿戴装备，entity填写具体装备名，不混入普通衬衫、马甲；independent_objects数独立无人甲、车辆等关键物件，entity填写具体物件名。"
    "每项写entity、视频帧号frames、按原文和本镜构图允许的expected_min/expected_max、实际observed数量，以及依据reason。"
    "人物穿甲是一个人和一件穿戴物，不是两个演员；无人空甲不是人。穿甲者露出正常人脸是允许的，不要求透过装甲看见人体。"
    "一人穿甲加一套独立空甲，在召来空甲之后可以成立；召来之前不得提前出现。不要仅因两套甲设计相同就判克隆。"
    "切近景、画外人物和遮挡可能减少可见人数，应根据本镜构图给合理范围，不强迫全剧演员每帧到齐。"
    "同一画面里多出身体、空甲或车辆，要分别计数，不能只核对具名角色。至少给出一项count_kind=characters的数量检查。例如同框两个人，其中一人穿甲，旁边另有一套无人空甲：角色2、穿戴装甲1、独立空甲1，人物总数不能填3。同一件装备的穿戴状态和独立状态分行，不能混为一类。\n"
    "same_person_twice：同一帧里是否有两个或更多长得一样（同脸同装）的人；\n"
    "species_or_gender_wrong：人被画成动物、动物被画成人或别的动物、动物直立拟人化，或原文里的女人由男人演（反之）、成人画成小孩；\n"
    "action_by_wrong_person：原文里甲做的动作或说的话，画面里由乙做或对错的对象做（例如原文甲向乙递东西，画面却由丙递出）；\n"
    "actor_missing：原文这一段里有动作或对白的人不在画面里，而且没有别人替他做（只露背影的听者、画外说话的人不算缺席）；\n"
    "lead_face_swapped：主角或其他给了角色卡的主要人物，脸型发型明显不是角色卡上那个人（追剧的观众认得主角，这也算一眼看出）；\n"
    "ghost_text：画面出现字幕、文字、水印、Logo（手机屏幕上的消息除外）；这一项单独记录，不影响 verdict；\n"
    "verdict：obvious＝观众会觉得荒谬或前后不一致（same_person_twice、species_or_gender_wrong、action_by_wrong_person、actor_missing、"
    "lead_face_swapped 任一成立就是 obvious）；subtle＝只有对照角色卡才看得出的配角差别（发色、服装、帽子、徽章）；fine＝没问题；\n"
    "evidence：一句话，写清哪几帧、谁、看到了什么；\n"
    "instruction：给视频生成模型的一句修正指令，只说画面里该有谁、谁对谁做什么、谁只能出现一次或不该出现，点名到人，不复述错误、不提上一次；verdict 为 fine 时留空。只输出JSON。"
)


INSTRUCTION_SCHEMA = model_client.obj({"instruction": {"type": "string"}})
