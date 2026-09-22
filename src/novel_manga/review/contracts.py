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
                              "lead_face_swapped", "ghost_text", "verdict", "evidence", "instruction"],
                 "properties": {
                     "people": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["who", "gender", "is_animal", "doing", "frames"],
                                                           "properties": {"who": {"type": "string"}, "gender": {"type": "string", "enum": ["男", "女", "不明"]},
                                                                          "is_animal": {"type": "boolean"}, "doing": {"type": "string"}, "frames": {"type": "string"}}}},
                     "same_person_twice": {"type": "boolean"}, "species_or_gender_wrong": {"type": "boolean"}, "action_by_wrong_person": {"type": "boolean"},
                     "actor_missing": {"type": "boolean"}, "lead_face_swapped": {"type": "boolean"}, "ghost_text": {"type": "boolean"},
                     "verdict": {"type": "string", "enum": ["obvious", "subtle", "fine"]}, "evidence": {"type": "string"},
                     "instruction": {"type": "string"}}}


VERIFY_QUESTIONS = (
    "\n先逐个描述视频帧里看到的每个人（people：who 是谁或长相，gender，is_animal，doing 在做什么，frames 出现在哪几帧），再回答：\n"
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
