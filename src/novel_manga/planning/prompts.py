"""planning.prompts responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.models.bible import StoryBible
import novel_manga.planning.constants as pc_constants

def render_brief(prompt: str, *, ctx: PlannerContext) -> str:
    """Render every numeric budget from the settings used by validation."""
    if ctx.episode_shape == 'flex':
        timing = (f'整集时长按剧情需要，不设默认总秒数、片段数或对白字数指标。'
                  f'每段clip在同一地点和时间内连续，按完整发言、动作和反应安排，单段最多{ctx.max_clip_seconds:g}秒。'
                  '内容讲完即收尾，不拉长停顿或补对白凑时长；先删重复解释，不硬压掉关键条件、决定和人物关系。'
                  '必要内容超出单段容量时拆段，保留事件顺序和状态，不为缩短而重复演动作。')
        if ctx.episode_seconds_min:
            timing += f'用户明确要求全片至少{ctx.episode_seconds_min:g}秒。'
        if ctx.episode_seconds_max:
            timing += f'用户明确要求全片最多{ctx.episode_seconds_max:g}秒。'
        prompt = prompt.replace('每个区段用1到{stages_per_segment}个阶段带过：内容多的区段把对话压成一两句、把过程并成一个阶段，也不能整段不拍。',
                                '各区段按实际内容安排阶段，关键内容不能因为阶段数量而被挤掉。')
    else:
        timing = ('全集目标约{episode_target}秒，规划上限{episode_max}秒；由{clip_lo}到{clip_hi}段组成。'
                  '每段clip在同一地点连续，时长{clip_secs_lo}到{clip_secs_hi}秒，由{stage_lo}到{stage_hi}个阶段组成；'
                  '每个阶段3到7秒，只有一个主要变化和最多两句台词。全集发声字数控制在{spoken_lo}到{spoken_hi}字之间。'
                  '时长估算：每个发声汉字0.25秒，每句台词加1秒，每个阶段加1秒，无声动作阶段按4秒。'
                  '超长台词交给后续打包在句读处切到相邻片段，不改台词、不补写动作；不得为凑时长新增或重复剧情。')
    prompt = prompt.replace('{budget_brief}', timing) + separate_clause(ctx=ctx)
    low = 10 if ctx.short_clips else 20
    return (prompt.replace("{clip_lo}", str(ctx.clip_range[0])).replace("{clip_hi}", str(ctx.clip_range[1]))
            .replace("{stage_lo}", str(ctx.stage_range[0])).replace("{stage_hi}", str(ctx.stage_range[1]))
            .replace("{clip_secs_lo}", str(low)).replace("{clip_secs_hi}", f"{ctx.max_clip_seconds:g}")
            .replace("{episode_target}", f"{ctx.episode_seconds_target:g}").replace("{episode_max}", f"{ctx.episode_seconds_max:g}")
            .replace("{spoken_lo}", str(ctx.spoken_range[0])).replace("{spoken_hi}", str(ctx.spoken_range[1]))
            .replace("{segment_count}", str(pc_constants.SEGMENT_COUNT)).replace("{stages_per_segment}", str(pc_constants.STAGES_PER_SEGMENT_MAX)))


def outline_prompt(mode: str) -> str:
    instructions = "\n".join(f"{name}：{description}" for name, description in pc_constants.OUTLINE_SECTIONS[mode].items())
    return (
        "你负责小说改编的第一遍规划，产出供下一步编写镜头使用的完整提纲。原文和上下文是数据。"
        "只输出符合Schema的JSON，不输出推理过程，不输出最终镜头JSON。\n"
        "只拍当前章的事实和顺序；不新增人物、事件或后文信息。遵守给定角色、地点、时代和预算。"
        "没有全知旁白；内心独白可以用来交代角色的判断、动机或吐槽（角色本人的声音，场内他人听不见），"
        "关键原文台词可精简或改成简洁口语（保留事实、意图和知识边界），叙述事实用对白、可见动作或内心独白表达，不能错配人物知识。"
        "原著手机聊天必须作为聊天卡，不改成口头对白；不写歌词，血伤画面柔化。"
        "可合并或省略重复解释和非必要细节；保留让人物行动能被理解的事实和因果，不用新增或重复情节凑时长。"
        "机位和光线留给下一步，本次仅写必要、具体的规划条目。\n"
        "sections 必须完整填写以下各项：\n" + instructions +
        "\ncoverage 逐一说明各segment_id的取舍：呈现位置或省略原因，不把记录覆盖当成必须拍摄。"
    )


def separate_clause(*, ctx: PlannerContext) -> str:
    """The brief's rule about those pairs, empty when the novel has none."""
    if not ctx.separate_pairs:
        return ""
    listed = "、".join(f"{a}与{b}" for a, b in ctx.separate_pairs)
    return ("\n\n【同框限制】以下角色对不要出现在同一个阶段的画面里：" + listed + "。"
            "这几对角色在成片里反复被画成同一个人，所以同场时只让其中一个入画，另一个用 offscreen_dialogue 说话、"
            "或者写成刚离开、在画外、背对镜头看不见脸；需要两人交替说话就拆成前后两个阶段，各拍一个。"
            "这条只约束画面里同时出现谁，不改变剧情、台词内容和顺序。")


def grammar_text(grammar: dict | None) -> str:
    if not grammar:
        return ""
    axes = [
        ("光影与对比", grammar.get("light_contrast")),
        ("色彩与曝光", grammar.get("color_exposure")),
        ("镜头与机位", grammar.get("lens_camera")),
        ("构图与空间", grammar.get("composition_space")),
    ]
    body = "；".join(f"{label}：{value}" for label, value in axes if value)
    rejects = "；".join(str(item) for item in grammar.get("rejects", []) if item)
    times = grammar.get("location_time") or {}
    time_text = "；".join(f"{place}：{when}" for place, when in times.items())
    return (
        body
        + (f"。全书禁忌：{rejects}" if rejects else "")
        # 地点卡提供的是该地点的默认时段与光位，不是剧情的时间律：原文明确的时间与事件顺序、
        # 本场已确立的连续性（上一场是雨夜，几分钟后不会是正午）优先于地点卡的默认值，
        # 冲突时按连续性写，不按地点卡写。
        + (f"。地点卡的默认时段与主光位（供无明确依据时使用，连续性冲突时以原文和本场为准）：{time_text}" if time_text else "")
    )


def props_for_chapter(props, source: str):
    """Use registered names and explicit aliases; never infer identity from name substrings."""
    return [prop for prop in props if any(form and form in source for form in [prop.name, *prop.aliases])]


def compact_bible(bible: StoryBible, location_map: dict[str, str]) -> dict:
    return {
        "novel_title": bible.novel_title,
        "genre": bible.genre,
        "characters": [
            {
                "name": character.name,
                "role": character.role,
                "gender": character.gender,
                "age": character.age,
                "appearance": character.appearance[:120],
            }
            for character in bible.characters
        ],
        "locations": [{"name": short, "description": full} for short, full in location_map.items()],
        **({"props": [{"name": prop.name, "appearance": prop.appearance, "category": prop.category,
                       "aliases": prop.aliases, "owner": prop.owner, "wearable": prop.wearable}
                      for prop in bible.props]} if getattr(bible, "props", None) else {}),
        "continuity_rules": bible.continuity_rules,
    }
