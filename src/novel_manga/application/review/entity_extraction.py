"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import re
import novel_manga.llm.client as model_client
import novel_manga.review.contracts as review_contracts


import novel_manga.application.review.cast_endpoint as service_cast_endpoint


def extract_names(chapter_text: str) -> list[dict]:
    prompt = (
        "列出这段小说里出现的所有人物：具名角色（如 主角的名字）、以称呼或身份指代但反复出现的个体（如 张管家、穿白衬衫的青年），以及群体（如 同学们、围观路人）。"
        "每个给出在本段出现的大致次数，以及是否有台词或近景出场（speaks_or_close_up）。同一人物的不同叫法合并为最常用的一个；"
        "如果一个称呼在段中后来被点出姓名，只保留姓名。只输出JSON。\n\n" + chapter_text
    )
    return model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.NAME_SCHEMA, name="names", max_tokens=1500, settings=service_cast_endpoint.bible_settings()).get("characters", [])


def extract_locations(chapter_text: str, known_locations: list[str]) -> list[dict]:
    recent = "、".join(known_locations[-30:])
    prompt = (
        "列出这段小说里发生场景的地点：能画成一张空场景图的具体地方（如 楚家广场、山崖之巅、赤岩城药材店），每个给一句可画的视觉描述"
        "（空间、主要物件、时间与光线）和本段在此发生的场景数。不要列泛指的地点（路上、远处、门口）。"
        # The description is pasted straight into the empty-scene card prompt, which forbids people; and the
        # plate is reused by every shot in that place, so one scene's state would be baked into all of them.
        "描述写这个地方长期不变的样子，当成一张没有人的背景板来写：不要写任何人、人群、人影或剪影，"
        "也不要写只属于本段这一场戏的临时状态（摊开的行李、摆出来的东西、正在发生的事）。"
        "写到碑文、匾额、招牌、书页这类本来有字的东西时，写成看不出字形的样子（风化的刻痕、磨平的笔画、模糊的符号），不要写可辨读的文字——地点卡的判定会把可读文字判成缺陷。"
        "另给每个地点判断 time_of_day（本段在此发生时通常是白天、夜晚、黄昏、清晨，说不清写不定）和 main_light"
        "（主光源，如 煤气灯、壁炉、窗外日光、篝火、洞顶天光、手机屏幕；按原文写，没写就按地点常识）。"
        + (f"已有的地点名：{recent}。如果本段的地点就是其中之一，name 必须原样使用已有名字。" if recent else "")
        + "只输出JSON。\n\n" + chapter_text
    )
    return model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.LOCATION_SCHEMA, name="locations", max_tokens=1200, settings=service_cast_endpoint.bible_settings()).get("locations", [])


def extract_props(chapter_text: str, known_props: list[str], known_names: set[str]) -> list[dict]:
    """Plot objects worth a card, judged on the judge's channel like every cast question.

    Named always; unnamed only when recurring across chapters or plot-driving (易手/认主/被毁/
    开启).  Every row carries a verbatim quote; a row whose quote is not a contiguous slice of
    the chapter, or whose name a character or an earlier prop already owns, is dropped - the
    merge pilot's v3 evidence rule, verbatim.
    """
    recent = "、".join(known_props[-30:])
    prompt = (
        "列出这段小说里有剧情分量的物件（武器、信物、法器、关键工具）："
        "有独立名字的必收；没名字的只在跨章反复出现、或牵动剧情（易手/认主/被毁/开启某物）时收。"
        "日常生活物件（茶杯、桌椅、饭菜）不收。每个给出：name、category（武器/信物/法器/工具/其他）、"
        "appearance（长期稳定外观，不写当下状态）、material（材质，没写留空）、owner（本段持有者，没写留空）、"
        "quote（从本段原文连续复制的原句，不得改写）、closeup（是否需要特写镜头）、wearable（是否可穿戴/覆盖身体的装备）。"
        + (f"已有道具：{recent}。同一物件必须原样使用已有名字。" if recent else "")
        + "只输出JSON。\n\n" + chapter_text
    )
    rows = model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.PROP_SCHEMA,
                                 name="props", max_tokens=1500, settings=service_cast_endpoint.bible_settings()).get("props", [])
    out = []
    for row in rows:
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        quote = str(row.get("quote") or "")
        if not name or name in known_names or name in known_props:
            model_client.log(f"bible: 道具 {name or '(无名)'} 未收（与人物或已有道具同名）")
            continue
        if not quote or quote not in chapter_text:
            model_client.log(f"bible: 道具 {name} 未收（quote 不是原文连续子串）")
            continue
        out.append({**row, "name": name})
    return out


def scan_chapter(chapter_text: str, known_locations: list[str], names: bool = True) -> dict:
    """The model calls of bible growth that do not depend on the bible's state:
    the chapter's proper names and its locations.  Safe to run for several
    chapters at once; grow_bible() then commits them in order under the lock.
    With names=False the caller supplies the names (the entity ledger does).
    Props ride the in-lock pass instead: growth is rare, and the scan window's
    parallelism buys nothing for one more call."""
    return {"names": extract_names(chapter_text) if names else [],
            "locations": extract_locations(chapter_text, known_locations),
            "props": []}
