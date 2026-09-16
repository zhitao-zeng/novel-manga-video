"""entities.contracts responsibilities; existing evidence and identity policy."""
from __future__ import annotations


LEDGER_POLICY = "entity-ledger-v2"


RECENT_WINDOW = 15  # chapters: someone on stage this recently is offered even if the chapter never names them


CLAIM_TYPES = ["same_as", "impersonates", "lookalike", "avatar_of", "occupies_body", "reveal", "transformation", "death", "return", "rename"]


MERGING = {"same_as"}  # the only claim that joins two records


ONE_SIDED = {"death", "return", "transformation"}  # claims whose object may be free text


BODY_TYPES = {"occupies_body", "avatar_of", "transformation"}  # claims that change whose card the picture is drawn from


SCOPES = ["reality", "dream", "flashback", "hearsay", "hypothetical"]


PRESENCE = ["on_stage", "voice", "mentioned"]


PRESENCE_RANK = {"on_stage": 2, "voice": 1, "mentioned": 0}


RELATION_BASES = {
    "none": "无", "unknown": "未知", "kinship_parent_child": "亲子", "kinship_sibling": "兄弟姐妹", "kinship_spouse": "夫妻",
    "kinship_fiance": "婚约", "kinship_clan": "亲族", "kinship_adoptive": "收养", "same_faction": "同阵营", "same_unit": "同一小队",
    "friend": "朋友", "childhood_friend": "发小", "rival_pair": "对手", "household_family": "同一家", "opposing_faction": "敌对阵营",
    "superior_subordinate": "上下级", "lord_vassal": "君臣", "master_disciple": "师徒", "teacher_student": "师生",
    "commander_soldier": "将与兵", "employer_employee": "雇佣", "colleague": "同事", "classmate": "同学", "roommate": "室友",
    "contract_bound": "契约", "oath_bound": "誓约", "guardian_ward": "监护", "romantic_partner": "恋人", "temporary_alliance": "临时同盟",
}


RELATION_STANCES = {
    "unknown": "未知", "neutral": "中立", "friendly": "友好", "trusting": "信任", "protective": "保护", "dependent": "依赖", "admiring": "敬佩",
    "romantic_interest": "爱慕", "wary": "警惕", "distrustful": "怀疑", "hostile": "敌对", "fearful": "恐惧", "resentful": "怨恨",
    "jealous": "嫉妒", "obsessive": "执着", "submissive": "顺从", "dominant": "支配", "conflicted": "矛盾",
}


EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["mentions", "new_entities", "claims", "relations"],
    "properties": {
        "relations": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["from", "to", "base", "stance", "address", "hidden_from_reader", "evidence"],
                                                 "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                                                                "base": {"type": "string", "enum": sorted(RELATION_BASES)},
                                                                "stance": {"type": "string", "enum": sorted(RELATION_STANCES)},
                                                                "address": {"type": "string"}, "hidden_from_reader": {"type": "boolean"},
                                                                "evidence": {"type": "string"}}}},
        "mentions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["form", "entity", "entity_name", "kind", "presence", "evidence"],
                                                "properties": {"form": {"type": "string"}, "entity": {"type": "string"}, "entity_name": {"type": "string"},
                                                               "kind": {"type": "string", "enum": ["proper", "contextual"]},
                                                               "presence": {"type": "string", "enum": PRESENCE},
                                                               "evidence": {"type": "string"}}}},
        "new_entities": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                    "required": ["name", "kind", "named", "description", "evidence"],
                                                    "properties": {"name": {"type": "string"},
                                                                   "kind": {"type": "string", "enum": ["person", "animal", "spirit", "other"]},
                                                                   "named": {"type": "boolean"}, "description": {"type": "string"},
                                                                   "evidence": {"type": "string"}}}},
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["type", "subject", "object", "scope", "hidden_from_reader", "evidence"],
                                              "properties": {"type": {"type": "string", "enum": CLAIM_TYPES}, "subject": {"type": "string"},
                                                             "object": {"type": "string"}, "scope": {"type": "string", "enum": SCOPES},
                                                             "hidden_from_reader": {"type": "boolean"}, "evidence": {"type": "string"}}}},
    },
}


VERDICT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "why"],
                  "properties": {"verdict": {"type": "string", "enum": ["supports", "contradicts", "insufficient"]}, "why": {"type": "string"}}}


EXTRACT_RULES = (
    "你在通读一部小说，手里有本章可能涉及的人物账本（编号、正式名、已见过的专指写法）。读本章原文，只输出 JSON。\n"
    "mentions：本章里指称人物的每一种写法各记一条（名字、姓氏、称谓、绰号、身份代称；不记代词他/她）。entity 填账本编号；"
    "账本按正式全名列人，原文常只写名或姓或去掉头衔（莱恩 就是账本里的 莱恩·格雷，薇奥拉 就是 薇奥拉公主），这种情况填账本编号、不要写 NEW；"
    "账本里确实没有的写 NEW:名字；本章无法确定指谁的写 UNCERTAIN。原文里有名有姓的人在账本里对不上任何一条时，宁写 NEW 也不要塞给"
    "一条泛称记录（吟游诗人、那位女士、调查师这类不是名字的条目）。entity_name 抄账本里该编号后面的正式名（NEW 或 UNCERTAIN 时留空），"
    "编号和名字必须是同一行的。kind：proper = 离开本章也能唯一指认此人的写法；"
    "contextual = 只在本段语境里才知道指谁的代称（那女人、医生、年轻人、教授）。presence：on_stage = 此人在场景里出现；"
    "voice = 只有声音（脑内声音、电话、门外、旁白）；mentioned = 只被提起、不在场。evidence 抄本章原文里含该写法的一小句，不超过 25 字。\n"
    "new_entities：账本没有、本章新出现的人物（或动物、灵体）；named 表示有真正的名字（“拉格特·富兰克林”是，“浓妆女人”“业务员”不是）；"
    "只提一次的无名路人不要写。\n"
    "claims：两条记录之间的身份关系，类型只能是：same_as（确是同一个人）、impersonates（subject 冒充/化名为 object）、"
    "lookalike（长得一样但不是同一人，如双胞胎）、avatar_of（subject 是 object 的分身/化身/投影）、occupies_body（subject 的灵魂在 object 的身体里）、"
    "reveal（本章揭晓 subject 就是 object）、transformation（subject 变成 object 所写的形态）、death、return、rename（subject 从此改叫 object）。"
    "scope：这件事在故事里是现实，还是梦境/回忆/传闻/假设。hidden_from_reader：原文此时是否仍对读者隐瞒这层关系。"
    "每条附本章原文里逐字的一句证据（不超过 40 字），抄不出来就不要写。没有就空数组。\n"
    "relations：本章能看出的两个人物之间的关系，from/to 填账本编号或 NEW:名字。base 是持久的结构关系："
    + "、".join(f"{k}={v}" for k, v in RELATION_BASES.items()) + "。stance 是 from 此刻对 to 的态度："
    + "、".join(f"{k}={v}" for k, v in RELATION_STANCES.items()) + "。address 是 from 在本章怎么称呼 to（叔叔、殿下、格雷先生），没叫过留空。"
    "只写原文有依据的，每条附一小句证据（不超过 25 字）；日常寒暄不算关系。"
)


VERDICT_RULES = (
    "判断一条身份关系是否被证据支持。只输出 JSON {verdict, why}。verdict：supports = 这句原文明确说明该关系成立；"
    "contradicts = 原文说明该关系不成立（如“她绝不是艾琳娜”）；insufficient = 证据不足以确定（猜测、反问、第三人的怀疑）。\n"
)


SAME_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["same", "why"],
               "properties": {"same": {"type": "string", "enum": ["same", "different", "unsure"]}, "why": {"type": "string"}}}


SAME_RULES = (
    "人物账本里有两条记录，判断它们是不是同一个人。只输出 JSON {same, why}。same = 同一个人（同一人的全名与简称、带头衔与不带头衔、"
    "正名与称呼）；different = 不同的人（包括双胞胎、同姓的亲属、长得像的人、同一称呼指向的另一个人）；unsure = 依据不足。"
    "只依据给出的原文和描述判断，不要凭名字相似猜。\n"
)


TITLES = ("公主", "王子", "先生", "小姐", "夫人", "女士", "太太", "医生", "教授", "博士", "男爵", "伯爵", "侯爵", "公爵", "国王", "女王", "陛下",
          "殿下", "老师", "队长", "船长", "神父", "修女", "警官", "警长", "探长", "侦探", "老板", "掌柜", "长老", "宗主", "道君", "真人", "仙子")


LEAN_NOTE = ("\n\n本章人物很多：mentions 只记最重要的 30 种写法，relations 最多 8 条，evidence 都不超过 15 字。")


RELINK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entity", "why"],
                 "properties": {"entity": {"type": "string"}, "why": {"type": "string"}}}


WEAK_KEYS = set(TITLES) | {"女人", "男人", "女孩", "男孩", "老人", "青年", "少年", "少女", "孩子", "小孩", "姑娘", "大人", "那个", "这个", "那位", "这位"}


DEDUP_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["same_ids", "why"],
                "properties": {"same_ids": {"type": "array", "items": {"type": "string"}}, "why": {"type": "string"}}}


SETTLE_RULES = (
    "下面是一本小说的人物账本里两条记录，以及一条关于它们的关系判断。它在单章的证据不够，现在把全书里能找到的证据都给你：两条记录各自的描述和写法、"
    "各自在场的章、两人同时在场的章（同一场里同时出现的两个人不可能是同一个人，除非原文写了分身或幻象）、所有相关证据句、后文原句、人物关系记录。"
    "只依据这些证据判断这条关系是否成立，只输出 JSON {verdict, why}：supports = 成立；contradicts = 不成立；insufficient = 全书证据仍不够。"
)


SETTLE_RULES_B = (
    "你是审稿人，要推翻下面这条人物关系判断。先找能证明它不成立的证据（两人同场、称呼矛盾、后文明说是两个人），找不到再看支持它的证据。"
    "只输出 JSON {verdict, why}：contradicts = 有证据说明不成立；supports = 找不到反证而且有明确证据支持；insufficient = 两边都不够。"
)


SETTLE_RULES_TIEBREAK = (
    "两位审读对下面这条人物关系判断意见不一。你是终审，必须给出结论，不能说证据不够：把全书证据里最硬的几条摆出来（同场出现、称呼、后文明说），"
    "按证据多的一边定。只输出 JSON {verdict, why}：supports = 成立；contradicts = 不成立。拿不准时按不成立（两条记录分开处理）。"
)


SETTLE_RULES_BODY = (
    "这是一条换身体、化身或冒充类关系，它决定画面里该画成谁的样子。只回答一个问题：全书证据里有没有原文明说“谁在谁的身体里”或“谁扮成了谁”，"
    "并且那具身体（或被冒充的人）就是记录乙？只输出 JSON {verdict, why}：supports = 原文明说了且就是记录乙；contradicts = 没明说，或不是记录乙。"
)
