"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import re
from novel_manga.story.character_evidence import excerpts
from pathlib import Path
import novel_manga.llm.client as model_client
from novel_manga.models.bible import StoryBible as review_models_StoryBible
from novel_manga.models.bible import Character
import novel_manga.story.identity as story_names
import novel_manga.story.name_rules as name_rules
import novel_manga.review.contracts as review_contracts
from novel_manga.util import atomic_write_json
from novel_manga.story.reading_facts import apply_known_facts, facts_for_name, facts_prompt


import novel_manga.application.review.cast_endpoint as service_cast_endpoint
import novel_manga.application.identity.reading_roster as service_reading_roster
import novel_manga.application.identity.reading_roster as service_reading_roster

VAGUE = re.compile(r"未详|不详|未知|未提及|未描述|建议|待定|无描述")

EXISTING_BUDGET = 15000


def fill_characters(bible: review_models_StoryBible, bible_path: Path, missing: dict, text: str,
                    cast: list[str] | None = None, *, character_facts: dict | None = None,
                    aliases: dict | None = None, chapter: int | None = None
                    ) -> tuple[review_models_StoryBible, list[str], dict, dict]:
    """Ask for casting entries for the missing names; add the confident proper
    names, keep appellations and vague entries as suggestions for a human."""
    known = [c.name for c in bible.characters]
    if character_facts is None:
        character_facts = service_reading_roster.reading_character_facts(bible_path.parent)
    if aliases is None:
        aliases = service_reading_roster.load_aliases(bible_path.parent)
    filled: list[str] = []
    needs_human: dict[str, str] = {}
    suggestions: dict[str, dict] = {}
    if True:
        # The whole cast used to go in here.  诸天 grew to 1310 characters = 81,575 characters of
        # prompt against a 65,536 context, so every fill request 400ed and bible growth stopped
        # for good.  Only plausible same_as candidates are worth showing: the ones this chapter
        # names, then the earliest entries (ids are positions, appended only, so the opening cast
        # is the recurring one), inside EXISTING_BUDGET.  role cannot rank them - in 诸天 only 8 of
        # 1310 entries say 配角 and many hold a whole appearance paragraph instead.
        # These are identity candidates, not appearance examples for new designs.
        lines = [(c.name, f"- {c.name}：{c.role}，{c.gender}") for c in bible.characters]
        plausible = {name for name, _ in lines if len(name) >= 2 and (
            name in text or any(len(wanted) >= 2 and (wanted in name or name in wanted) for wanted in missing))}
        ordered = [line for name, line in lines if name in plausible]
        ordered += [line for name, line in lines if name not in plausible]
        kept, used = [], 0
        for line in ordered:
            if used + len(line) + 1 > EXISTING_BUDGET:
                break
            kept.append(line)
            used += len(line) + 1
        existing = "\n".join(kept)
        prompt = (
            "圣经里已有这些角色：\n" + existing + "\n\n下面是原文里反复出现但圣经缺失的人物及其原文摘录。为每个人物判断：same_as 填写它其实是哪个已有角色（或本列表中另一个人物）的另一种叫法，不是则填空字符串；"
            "confidence 是你对“这是一个需要单独定妆的独立人物”的把握（0到1）。然后写一条可跨集复用的选角条目（性别、年龄段、外貌、服装、发型、配色、基础服装、识别物），"
            "已确认读书事实是人物设计的依据，外貌、物种、外观年龄和服装须完整保留；"
            "hair、palette、base_costume与这些事实保持一致。原文未写的部分可以做克制设计，并注明设计推断。"
            "外貌记录稳定特征，包括原文确认的持久毁容、残肢、非人身体；"
            "临时伤势、表情和当下动作留在章节状态。寿龄、职务与外观年龄分别表达，"
            "阶段性的变身或换身体按已有阶段证据处理。"
            "只为本批请求的名字各返回一条，不输出上方已有角色，不扩展名单。只输出JSON。\n\n"
        )
        vouched_names = [name for name in missing if cast and story_names.name_matches(name, cast)]
        if vouched_names:
            prompt += ("全书通读已经确认以下名字是需要定妆的独立人物："
                       + "、".join(vouched_names)
                       + "。这不是根据本章猜测是否会再出现；请据原文完成这些人物的具体设计，"
                         "不要因敬称后缀或本章没有复述外貌就拒绝设计。\n\n")
        rows = []
        requested = list(missing)
        for offset in range(0, len(requested), 4):
            batch = requested[offset:offset + 4]
            question = prompt + facts_prompt(character_facts or {}, batch, aliases=aliases, chapter=chapter) + "\n\n".join(
                f"【{name}】（出现 {missing[name]['mentions']} 次，章 {sorted(set(missing[name]['chapters']))}）\n{excerpts(text, name)}"
                for name in batch)
            rows.extend(model_client.ask_json([{"type": "text", "text": question}],
                review_contracts.fill_schema(batch), name="fill", max_tokens=3000,
                settings=service_cast_endpoint.bible_settings()).get("characters", []))
        added: list[Character] = []
        for row in rows:
            name = re.sub(r"\s+", "", str(row.get("name", "")))
            if name not in missing:
                continue
            row = apply_known_facts(row, facts_for_name(character_facts or {}, name, aliases=aliases, chapter=chapter))
            reason = ""
            # The reading read the whole book; this question is asked from inside one chapter.  When
            # the two disagree about whether somebody exists, the reading wins - otherwise 伊文斯 is
            # filed as another word for 布鲁斯·韦恩 and can never be bound again.
            vouched = bool(cast) and story_names.name_matches(name, cast)
            if row.get("same_as") and not vouched:
                reason = f"疑为 {row['same_as']} 的别称"
            elif float(row.get("confidence", 0)) < 0.6:
                reason = f"把握不足（{row.get('confidence')}）"
            elif VAGUE.fullmatch(row.get("appearance", "").strip()) or VAGUE.fullmatch(row.get("wardrobe", "").strip()) or not row.get("appearance") or not row.get("wardrobe"):
                reason = "外貌或服装描述不具体"
            elif story_names.name_matches(name, known):
                reason = "与已有角色重名"
            if not reason and not vouched and name_rules.APPELLATION.search(name):
                reason = "称呼类人物，可能是已有角色的另一叫法；条目已生成，需人工确认后加入"
            if reason:
                needs_human[name] = reason
                suggestions[name] = {**{k: v for k, v in row.items() if k in Character.model_fields}, "same_as": row.get("same_as", "")}
                continue
            added.append(Character(**{k: v for k, v in row.items() if k in Character.model_fields}))
            known.append(name)
        for name in missing:
            if name not in needs_human and name not in {c.name for c in added}:
                needs_human[name] = "模型未给出条目"
        if added:
            bible = bible.model_copy(update={"characters": [*bible.characters, *added]})
            atomic_write_json(bible_path, bible.model_dump(mode="json"))
        filled = [c.name for c in added]
    return bible, filled, needs_human, suggestions
