"""Read a book chapter by chapter, then decide who is who once, over all chapters at the same time.

The pipeline's own reading pass is sequential: each chapter is committed against the bible as it
stands after the one before, so a name has to be judged before the book has finished introducing it.
That is why 贝纳妮丝 was recognised 860 chapters after her first mention.  Here a chapter records the
name forms exactly as it writes them and judges nothing, and one later pass sees every chapter at
once.  On 伏天氏 1-100 that read the book in 22 minutes with no wrong merge, against 56 minutes for
the pipeline and 127 for a full agent run that missed the minor characters.

The merge is one call against a schema, not an agent loop.  An agent re-reads the candidate table on
every turn and its context only grows: the 超品相师 merge ran 58 minutes, spent 391k input tokens
against a 262k window, and exited with an API error having written nothing.
"""
from __future__ import annotations

from collections import Counter, defaultdict

STR = {"type": "string"}

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "people", "looks", "changes", "locations"],
    "properties": {
        "summary": STR,
        "people": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["form", "kind", "refers_to", "presence", "speaks", "gender", "evidence"],
            "properties": {
                "form": STR,
                "kind": {"type": "string", "enum": ["专名", "绰号", "姓氏加头衔", "描述性称谓",
                                                    "头衔", "亲属称谓", "自称或代号"]},
                "refers_to": STR,
                "presence": {"type": "string", "enum": ["在场", "只被提到"]},
                "speaks": {"type": "boolean"},
                "gender": {"type": "string", "enum": ["男", "女", "未写明"]},
                "evidence": STR}}},
        "looks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["form", "text", "lasting"],
            "properties": {"form": STR, "text": STR,
                           "lasting": {"type": "string", "enum": ["持久", "临时", "不确定"]}}}},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["form", "what", "evidence"],
            "properties": {"form": STR, "what": STR, "evidence": STR}}},
        "locations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "sketch", "time_of_day", "main_light", "evidence"],
            "properties": {"name": STR, "sketch": STR,
                           "time_of_day": {"type": "string",
                                           "enum": ["白天", "夜晚", "黄昏", "清晨", "不定"]},
                           "main_light": STR, "evidence": STR}}},
    },
}

EXTRACT_PROMPT = """你在为一部小说做逐章的只读分析。下面是《{title}》第{n}章全文。只根据这一章的原文回答，不要用你对这本书的任何印象，也不要推测后面的剧情。

要写的内容：
1. summary：一到两句，写读完这一章，谁的处境、认知、关系或筹码和上一章不一样了。不要复述剧情。
2. people：本章出现的每一个人物称谓各记一条，照抄原文里的写法。同一个人在本章有几种叫法就记几条。
   - kind：专名（全名或常用名）；绰号（外号）；姓氏加头衔（带姓或名的称呼）；描述性称谓（按外形或身份描述，如"红衣女子"）；头衔（不带姓名的职位，如"院长"）；亲属称谓（如"老爹"）；自称或代号。
   - refers_to：只有本章原文能确定这个称谓指的是哪一个有名字的人时，写那个名字；否则写"未定"。不要跨章猜。
   - presence：人物在本章的场景里出现、行动或说话，写"在场"；只是被别人提起，写"只被提到"。
   - speaks：本章里这个人有没有说话。
   - gender：原文能看出的性别，看不出写"未写明"。
   - evidence：能证明以上判断的原文，照抄，不超过 30 字。
   - 群体（众人、弟子们、围观的人）不记。
3. looks：本章原文对某个人物的外貌、服装、形态的描写，照抄，不超过 40 字。lasting 判断这是这个人长期的样子（持久）、一时的状态（临时）还是不确定。原文写明不满十八岁的人物，以及在校学生，一律不摘录身材描写（身材、曲线、胸、腰、腿之类），只摘面容、发型和服装。
4. changes：本章发生的持久变化：身份揭晓、改名、变身、长期可见的伤、长期换装。没有就给空数组。evidence 照抄，不超过 30 字。
5. locations：本章里**能单独画成一张空场景图**的具体地方。名字要具体到能和别的地方区分开（写"南昌大学食堂"不要写"食堂"，写"诸葛庐碑碣深处凉亭"不要写"诸葛庐"）；泛指的"路上""远处""门口"不记。
   - sketch：这个地方长期不变的样子，不超过 50 字：建筑结构、空间布局、固定陈设与材质。**不写任何人、人群、人影或剪影**，也不写只属于本章这一场戏的临时状态。写到碑文、匾额、招牌这类本来有字的东西，写成看不出字形的样子（风化的刻痕、磨平的笔画），不要写可辨读的文字。原文不够写就写空字符串，不要编。
   - time_of_day：本章在这里发生时通常是什么时候，说不清写"不定"。
   - main_light：主光源，如 窗外日光、路灯、烛火、洞顶天光；原文没写就按这个地方的常识写。
   - evidence 照抄原文，不超过 30 字。

所有 evidence 和 text 都必须能在本章原文里一字不差地找到。

第{n}章原文：
{text}"""


def aggregate(extracts: dict[int, dict], chapter_texts: dict[int, str]) -> dict:
    """Fold the per-chapter reads into one table of name forms.

    For every form: how the chapters classified it, which named person a chapter itself said it was,
    the chapters that list it and the chapters whose text contains it, where it was on stage or
    spoke, the gender votes and one piece of evidence.
    """
    forms: dict[str, dict] = defaultdict(
        lambda: {"kind": Counter(), "refers_to": Counter(), "listed": set(), "on_stage": set(),
                 "speaks": set(), "gender": Counter(), "evidence": []})
    looks: dict[str, list] = defaultdict(list)
    changes: list[dict] = []
    locations: dict[str, set] = defaultdict(set)
    sketches: dict[str, list] = defaultdict(list)
    summaries: dict[int, str] = {}
    missing: list[int] = []

    for n in sorted(chapter_texts):
        answer = extracts.get(n)
        if answer is None:
            missing.append(n)
            continue
        summaries[n] = answer.get("summary", "")
        for person in answer.get("people", []):
            form = forms[person["form"].strip()]
            form["kind"][person["kind"]] += 1
            target = str(person.get("refers_to") or "").strip()
            if target and target != "未定" and target != person["form"].strip():
                form["refers_to"][target] += 1
            form["listed"].add(n)
            if person["presence"] == "在场":
                form["on_stage"].add(n)
            if person["speaks"]:
                form["speaks"].add(n)
            form["gender"][person["gender"]] += 1
            if len(form["evidence"]) < 2:
                form["evidence"].append({"chapter": n, "quote": person["evidence"]})
        for look in answer.get("looks", []):
            looks[look["form"].strip()].append(
                {"chapter": n, "text": look["text"], "lasting": look["lasting"]})
        for change in answer.get("changes", []):
            changes.append({"chapter": n, **change})
        for place in answer.get("locations", []):
            name = place["name"].strip()
            locations[name].add(n)
            if place.get("sketch", "").strip():
                sketches[name].append({"chapter": n, "sketch": place["sketch"].strip(),
                                       "time_of_day": place.get("time_of_day", "不定"),
                                       "main_light": place.get("main_light", "")})

    rows = []
    for form, data in forms.items():
        rows.append({"form": form, "kind": data["kind"].most_common(1)[0][0],
                     "kinds": dict(data["kind"]), "refers_to": dict(data["refers_to"]),
                     "listed": sorted(data["listed"]),
                     # A string count, not a mention count: the same word may name someone else, or
                     # nothing at all.  The merge is told to read it that way.
                     "in_text": [n for n, text in chapter_texts.items() if form and form in text],
                     "on_stage": sorted(data["on_stage"]), "speaks": sorted(data["speaks"]),
                     "gender": dict(data["gender"]), "evidence": data["evidence"]})
    rows.sort(key=lambda row: (-len(row["listed"]), row["listed"][0]))
    return {"forms": rows, "looks": dict(looks), "changes": changes,
            "locations": {name: sorted(chapters) for name, chapters in locations.items()},
            "sketches": dict(sketches), "summaries": summaries, "missing": missing}


def candidate_table(title: str, candidates: dict, last_chapter: int) -> str:
    def span(chapters: list[int]) -> str:
        return f"{len(chapters)}章 ({chapters[0]}–{chapters[-1]})" if chapters else "—"

    missing = candidates["missing"]
    out = [f"# 《{title}》第 1–{last_chapter} 章称谓候选表", "",
           f"由 {last_chapter} 章逐章提取汇总而来（缺章：{missing or '无'}）。"
           "每一行是原文里的一种写法，不是一个人；谁和谁是同一个人，由你按规则决定。", "",
           "## 称谓（按被列出的章数排序）", "",
           "| 称谓 | 类型 | 本章原文确认的指向（次数） | 被列出 | 原文出现 | 在场 | 说话 | 性别票 | 一条证据 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for row in candidates["forms"]:
        refers = "、".join(f"{k}×{v}" for k, v in row["refers_to"].items()) or "—"
        gender = "、".join(f"{k}{v}" for k, v in row["gender"].items())
        first = row["evidence"][0] if row["evidence"] else {"chapter": "?", "quote": ""}
        out.append(f"| {row['form']} | {row['kind']} | {refers} | {span(row['listed'])} | "
                   f"{span(row['in_text'])} | {len(row['on_stage'])} | {len(row['speaks'])} | "
                   f"{gender} | 第{first['chapter']}章「{first['quote']}」 |")
    out += ["", "## 外貌、服装、形态的原文摘录", ""]
    for form, items in sorted(candidates["looks"].items(), key=lambda kv: -len(kv[1])):
        out.append(f"- **{form}**：" + "；".join(
            f"第{i['chapter']}章（{i['lasting']}）「{i['text']}」" for i in items[:8]))
    out += ["", "## 持久变化", ""]
    out += [f"- 第{c['chapter']}章 {c['form']}：{c['what']}（「{c['evidence']}」）"
            for c in candidates["changes"]]
    out += ["", "## 地点（附逐章的空场描写）", ""]
    for name, chapters in sorted(candidates["locations"].items(), key=lambda kv: -len(kv[1])):
        drawn = candidates.get("sketches", {}).get(name, [])
        out.append(f"- **{name}**：{span(chapters)}")
        for item in drawn[:4]:
            out.append(f"    - 第{item['chapter']}章（{item['time_of_day']}，{item['main_light']}）"
                       f"「{item['sketch']}」")
    out += ["", "## 每章一句话", ""]
    out += [f"- 第{n}章：{s}" for n, s in sorted(candidates["summaries"].items())]
    return "\n".join(out) + "\n"


MERGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["characters"],
    "properties": {
        "characters": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "name_source", "aliases", "tier", "gender", "age", "identity",
                         "appearance", "wardrobe", "first_chapter", "chapters", "evidence",
                         "unresolved"],
            "properties": {
                "name": STR,
                "name_source": {"type": "string", "enum": ["原文", "描述"]},
                "aliases": {"type": "array", "items": STR},
                "tier": {"type": "string", "enum": ["主要", "重要", "次要", "龙套"]},
                "gender": {"type": "string", "enum": ["男", "女", "未写明"]},
                "age": STR, "identity": STR, "appearance": STR, "wardrobe": STR,
                "first_chapter": {"type": "integer"},
                "chapters": {"type": "array", "items": {"type": "integer"}},
                "evidence": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["chapter", "quote"],
                    "properties": {"chapter": {"type": "integer"}, "quote": STR}}},
                "unresolved": {"type": "array", "items": STR}}}},
    },
}

MERGE_LOCATIONS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["locations"],
    "properties": {
        "locations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "first_chapter", "chapters"],
            "properties": {"name": STR, "description": STR,
                           "first_chapter": {"type": "integer"},
                           "chapters": {"type": "array", "items": {"type": "integer"}}}}},
    },
}

MERGE_PROMPT = """前一步已经把《{title}》第 1–{last} 章逐章读过。每一章只做了四件事：照抄原文里出现的人物称谓（不做跨章判断）、摘录外貌描写、记下持久变化、写一句话摘要。汇总结果就是下面这张候选表。

你的工作是像责任编辑一样决定"谁是谁"：把指同一个人的称谓并成一个人物，给出正名、别名、性别、年龄、身份、外貌和出场章。

## 归并规则

- 一人一实体。拿不准两个称谓是不是同一个人，就分开建两条，把疑问写进 `unresolved`。
- 只有专名、有同指证据的绰号、带姓名的称呼可以合并成别名。
- **共现不是同指。** 一句里同时出现两个称谓，只说明这句提到了两个人。"甲和乙有关系""甲和乙联姻""甲是乙的人"这类**关系陈述是两个人的证据，不是同指证据**；同姓、同出身、同阵营、同一场合在场也都不是。同指要的是两个称谓互换之后整句仍然成立——"甲即乙"的直陈句，或者同一段里交替用两个称谓指同一个动作的主语。找不到这样的句子就分开建两条，写进 `unresolved`。
- 描述性称谓（"中年""红衣女子"）和不带姓名的头衔（"院长"）不单独建人物，也不当别名；候选表"本章原文确认的指向"一列写明了它指谁，就作为证据记在那个人物下。
- 亲属称谓（"老爹"）：原文确认指向某个具名人物时，并进去当别名；没确认就不收。
- **原文始终没给名字、但反复上镜的人要单独建条目**：同一个描述性称谓或头衔在 3 章以上指的确实是同一个人，就用原文的叫法当正名建一条，`name_source` 写 `描述`。他们要画卡，漏掉就没法出镜。拿不准就分开建并写进 `unresolved`。
- 候选表里"原文出现"只是字符串计数：同一个词可能指不同的人，也可能根本不是人名。
- 硬事实（年龄、性别、身份、在哪几章出场）必须能追溯到原文；原文没写的写"未写明"，不按常理补。
- 明显不是人物的（功法、称号、概念、群体）不收。
- 只收有名有姓、或有台词且不止一章出现的人物，以及上面那条"没有名字但 3 章以上反复上镜"的人物。
- `chapters` 是这个人物在原文里出场（被叙述或说话）的全部章，不是被提到名字的章。

## 证据

`quote` 必须是从标注的那一章原文里连续复制的一段，不得改写、概括、拼接，也不要用省略号把两处接起来。情节复述不是引文。复制不出连续原句时，换一章能直接复制的，或者把这条证据删掉——宁可少一条证据，不要一条对不上原文的证据。

## 未成年人

原文写明年龄不满 18、或身份是在校学生一类的人物，`appearance` 和 `wardrobe` **一律不写身材**。禁止出现胸、腰、臀、腿、曲线、身材、凹凸、丰满、纤细、玲珑、婀娜一类词，也不要用"身姿""体态"绕开；只写脸、发、神态、衣着款式与颜色。即使原文写了，也不照抄。

只输出 JSON。

{table}"""

MERGE_LOCATIONS_PROMPT = """前一步已经把《{title}》第 1–{last} 章逐章读过，下面是汇总出来的候选表。

请从表里的地点一节，整理出这本书的空场景清单。

候选表的地点一节给了每个地方逐章的空场描写。把同一个地方的几条合成一条 `description`：这个地方长期不变的样子——建筑结构、空间布局、固定陈设与材质，最后交代常态下的时段与主光源。不写任何人、人群、人影或剪影，也不写只属于某一场戏的临时状态。写到碑文、匾额、招牌这类本来有字的东西时，写成看不出字形的样子（风化的刻痕、磨平的笔画），不要写可辨读的文字。

`name` 要具体到能和别的地方区分开，**不要合并成上位地名**：「南昌大学食堂」和「南昌大学宿舍楼下空地」是两个地方，不要并成「南昌大学」；「诸葛庐碑碣深处凉亭」和「诸葛庐石碑园地中心」也是两个。一张空场卡只画一个空间。

**写不出描写也要列出来**：原文不足以描述的地方照样建一条，`name` 照写，`description` 留空字符串——后面会有人按原文补，漏列就没人知道这个地方存在。不要编描写，也不要因为编不出来就不列。

**只列能单独画成一张空场图的物理空间。** 阵法、招式、功法、组织、势力、时间段、抽象概念都不是地点。「大厅」「房间」「屋里」这种任何地方都有的泛称也不算，除非前面能加上专属限定（「韦恩庄园大厅」可以，光一个「大厅」不行）。

只输出 JSON。

{table}"""


# The same rules, addressed to an agent that writes files instead of returning one answer.  The rules
# themselves are MERGE_PROMPT's; only the delivery changes.
MERGE_BRIEF_TAIL = """
## 导出（写进 `output/export/`）

**1. `story_bible.json`**

```json
{"characters": [
  {"name": "正名", "name_source": "原文|描述", "aliases": ["有同指证据的别名"],
   "tier": "主要|重要|次要|龙套", "gender": "男|女|未写明",
   "age": "原文写明的年龄或年龄段，没有就写 未写明",
   "identity": "身份、所属势力（原文有据）",
   "appearance": "原文有据的外貌，没有就写空字符串",
   "wardrobe": "原文有据的服装，没有就写空字符串",
   "first_chapter": 1, "chapters": [1, 2, 5],
   "evidence": [{"chapter": 1, "quote": "不超过 30 字的原文"}],
   "unresolved": ["未决的指代或矛盾"]}],
 "locations": [
  {"name": "地点名", "description": "原文有据的空场描写", "first_chapter": 1, "chapters": [1, 3]}]}
```

**2. `bible_aliases.json`**：`{"别名": "正名"}`，只放有同指证据的。

**3. `phases.json`**：外观、形态或身份发生**持久**变化的人物。一场戏里的临时状态不算。

```json
{"policy": "phase-cards-v1",
 "characters": {"正名": [
   {"from": 起始章, "to": 结束章或 null, "label": "阶段名",
    "appearance": "这一阶段原文有据的外貌", "wardrobe": "这一阶段的服装",
    "evidence": [{"chapter": 1, "quote": "不超过 30 字的原文"}]}]}}
```

没有就写 `{"policy": "phase-cards-v1", "characters": {}}`。

**4. `report.md`**：每个合并和拆分的决定，写依据（章号加原文短引）；最后写你没把握的地方。

原文在 `input/source.md`，需要核对上下文时用 `grep -n` 查。不要用子代理，不要联网，只读原文和候选表。
一边判一边写，不要等到最后一次性输出——文件是逐个写的，一份写完再写下一份。
"""


def merge_brief(title: str, last: int, table_file: str = "input/candidates.md") -> str:
    """The merge rules as a brief for an agent, pointing at the table instead of carrying it."""
    body = MERGE_PROMPT.format(title=title, last=last, table="").rstrip()
    body = body.replace("汇总结果就是下面这张候选表。", "汇总结果就是候选表。")
    body = body.replace("只输出 JSON。", f"候选表在 `{table_file}`，完整机读版在 `input/candidates.json`。")
    # The agent writes characters and locations, so it needs both sets of rules.  Splitting the
    # single call in two moved 「## 地点」 into its own prompt and quietly took it out of this brief.
    places = MERGE_LOCATIONS_PROMPT.split("请从表里的地点一节，整理出这本书的空场景清单。", 1)[-1]
    places = places.split("只输出 JSON。", 1)[0].strip()
    return ("# 任务说明：人物与地点归并\n\n" + body + "\n\n## 地点\n\n" + places + MERGE_BRIEF_TAIL)
