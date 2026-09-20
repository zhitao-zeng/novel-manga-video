"""Build the brief an authoring agent needs, from the book the pipeline already knows.

The briefs were hand-kept copies - one directory per episode, five near-identical 任务说明.md - and a
copy goes stale the moment the spec changes.  The dialogue-format rule was added at 00:30 and every
one of the thirty sheets had been prepared from an 18:59 copy, so 319 of the 366 findings against
them are the agents obeying a spec that no longer existed.  The same copies never carried a location
list at all, and six skills across five chapters invented 32 names for four places, some with the
hour baked into the name (诸葛庐碑林深处·黄昏 beside 诸葛庐碑林深处·夜).  A shot whose 场景 matches no
bible location has no plate to be drawn against.

So the spec lives here, once, and everything about the book is read from the novel directory at the
moment the brief is written: the chapter from novel.json, the recap from recap.json, and the cast and
locations of exactly this chapter from cast_index.json.  Nothing to keep in sync by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

from .places import offered_locations, recently_used
from .constants import CAST_RECENT_CHAPTERS

SECONDS = 100
# profile.json carries the bare token the renderer keys on; the agent needs words.
STYLE_WORDS = {"3d": "3D 国漫动画", "anime": "二维日式动画", "realistic": "写实真人风"}
# Nine columns, fixed: planning/storyboard.py matches on these labels and refuses a sheet that
# renames, merges or drops one.
COLUMNS = "| 镜号 | 摄影角度 | 景别 | 画面内容 / 动作 | 场景 | 台词 / 声音 | 机位 / 运镜 / 连续性 | 叙事目的 | 预算秒 |"

TASK_SPEC = f"""## 忠于原著

- 人物、事件、因果和结局以原文为准；不新增人物，不新增原文没有的情节。
- 可以删减作者议论、科普和重复解释。
- 讲述顺序可以按你的设计调整（冷开场、闪回等都可以），但事实不能改变。
- 结尾停在这一章原文的结局上，不要为了留钩子新编情节。

## 心理活动

- 原文里的心理活动可以用该角色的**内心独白**说出来：画外音，是他本人的声音，画面里他的嘴不动；也可以拍成看得见的动作和反应。怎么取舍，按你所用 Skill 的方法决定。
- 内心独白和台词写在一起，标明"内心独白"。

## 镜头怎么分

视频模型一次生成一段 4–15 秒的连续画面，每个镜头单独生成。镜头拆得越碎，前后画面越容易对不上。所以：

- 同一地点、同一人物状态下连续发生的动作，尽量放进同一个镜头里拍完。一个镜头可以有几个动作节拍，也可以有运镜。
- 每镜 **4–15 秒**，不要 1–3 秒的碎镜头。全集大约 10–20 个镜头。
- 只在这几种情况下切镜：换地点、换时间、人物状态变了，或者要给观众新的信息。只是换景别、换角度，就用运镜完成，不切。

## 画面怎么写

- 画面描述只写镜头里看得见的东西。不要写"没有路、没有电线杆"这类否定句，视频模型会把提到的东西画出来。要表现空旷，就写看得见的空旷。
- 每个镜头的开场画面是镜头开始、动作发生之前的那一刻，只有一个瞬间，不要把几个时刻拼进一张画面，也不要把这一镜最激烈的时刻当开场。
- 画面里不要出现可读的文字；石碑、书页上只有线条和纹样。

## 声音

- 每镜写清这一镜里听得到的声音：台词、内心独白、环境声和动作声。
- 跨镜头的声音衔接（J-cut、L-cut、声音桥）和贯穿全片的配乐都做不到，不要靠它们讲故事。
- 瞳孔里的倒影、拉焦、1–2 秒的插入镜头，视频模型都做不准，不要用。

## 分镜表的格式（必须照这个写）

最终分镜表要同时写成 `output/分镜表.md` 和 `output/分镜表.xlsx`（缺 Python 库就自己 pip 安装）。

**xlsx 的表头必须是下面这九列，一字不差，一列不少、不改名、不合并、不加列：**

{COLUMNS}

每列写什么：

- **镜号**：这一镜的编号，全表唯一，例如 1、2、3。
- **摄影角度**：平视、略俯、微仰、大俯拍这类。
- **景别**：大远景、全景、中景、中近景、近景、特写这类。
- **画面内容 / 动作**：这一镜看得见的内容和发生的动作。先写动作发生前的那一刻，再写这一镜里发生了什么。**不能为空。**
- **场景**：这一镜发生的地点，**必须从下面「这一集的地点」里原样抄一个名字**。**不能为空。**
- **台词 / 声音**：这一镜里听得见的一切写在同一格：台词、内心独白、环境声、动作声，写法见下。
- **机位 / 运镜 / 连续性**：相机在哪、怎么动，以及和前后镜的接续关系。
- **叙事目的**：这一镜为什么存在，一句话。
- **预算秒**：这一镜的时长，**写纯数字**（例如 7 或 7.5），必须大于 0。

### 「台词 / 声音」这一格怎么写（必须统一）

这一格会被机器拆成"谁说了什么"和"听得到什么声音"两部分，所以写法要统一。

**台词每句单独一行，一行只放一句，格式固定为：**

```
说话人（发声方式）：“台词原文”
```

- 引号**一律用中文双引号 “”**，不要用 「」、半角引号，也不要不加引号。
- **发声方式只能从下面五个里选一个**，写别的机器认不出来：
  - **（说）** —— 人物在画面里开口说话，能看见嘴动
  - **（画外音）** —— 声音来自画面外，或画面里的人不对口型
  - **（内心独白）** —— 心里想的，用本人的声音，画面里嘴不动
  - **（唱）** —— 明确的哼唱，格子里写哼唱方式，不要放普通台词
  - **（聊天消息）** —— 手机或电脑屏幕上的聊天文字，不是说出来的
- 语气写在发声方式后面，用顿号隔开，例如 `秦宇（说、压低声音）：“……”`。
  「口型同步」「气声」「低沉」这类不是发声方式，是语气，要写在顿号后面。

**环境声和动作声单独写一行，以 `声音：` 开头，不加引号，不要和台词挤在同一行：**

```
声音：清晨的风、远处鸟鸣、衣料摩擦
```

**这一镜没有台词就只写 `声音：` 那一行**，不要写"（无台词）"。

**这一格只能有这两种行**：台词行和 `声音：` 行。不要写括号里的导演注解，不要写"（收束：……）""（此镜无对白）"这类说明，也不要写否定句（"无爆炸""无强光"）——要交代的东西写进「机位 / 运镜 / 连续性」或「叙事目的」那两列。

例子：

```
秦宇（内心独白）：“竟看了一整晚，还真是入迷了。”
青年导游（说、不耐烦）：“小伙子，别挡道。”
声音：清晨的风、远处鸟鸣、拍打衣服的声音
```

**硬性要求（不满足这一表就会被退回）：**

- 每个格子只写文字或数字，**不要用公式**，也不要留合计行的公式。
- **预算秒必须是数值**：写 `7`，不要写 `7s`、`约7秒`、`7-8`。
- **镜号不能重复**，也不能为空。
- 表头那一行之前可以有标题和说明行，但表头之后每一行都是一个镜头。

## 其余交付物（全部写进 `output/`）

1. 按所用 Skill 自己的流程，把每一步的产出写成文件，文件名带步骤编号。
2. 如果 Skill 本身没有"从小说到剧本"的步骤，可以先简要整理分场写成 `output/01_分场.md`，再按 Skill 的方法做分镜。
3. `output/运行记录.md`：简要记录每一步做了什么、替用户做了哪些选择以及理由。
"""


def split_location(entry: str) -> tuple[str, str]:
    name, _, description = str(entry).partition("：")
    return name.strip(), description.strip()


def _head(title: str, chapter: int, chapter_title: str, episode: int, recap: list[dict],
          style: str, frame: str) -> str:
    # chapter_title already reads 第3章 初遇孟瑶, so naming the number again nests brackets round it.
    what = f"《{title}》{chapter_title or f'第 {chapter} 章'}全文"
    lines = [f"# 任务说明\n",
             f"把 `input/source.txt`（{what}）改编成**长篇系列短剧的第 {episode} 集**，"
             "并做成可以交给 AI 视频模型逐镜生成的分镜表。\n",
             "## 目标\n",
             f"- 一集约 **{SECONDS} 秒**，**{frame} 横屏**，**{STYLE_WORDS.get(style, style)}**。"
             "画风、人物和地点见 `input/人物地点与画风.md`。"]
    if episode <= 1:
        lines.append("- 这是系列第一集：观众看完要知道主角是谁、他身上发生了什么，并想看下一集。\n")
    else:
        lines.append(f"- 这是系列第 {episode} 集。前面几集已经播过（见下面的前情），"
                     "观众已经认识主角，**不要重新介绍他，也不要重拍前情里的事**；"
                     f"本集只拍第 {chapter} 章的内容，结尾停在这一章原文的结局上。\n")
        lines.append("## 前情（已经播过，不要重拍）\n")
        for item in recap:
            if int(item.get("chapter", 0)) < chapter:
                lines.append(f"- 第 {item['chapter']} 集：{item.get('summary', '').strip()}")
        lines.append("")
    return "\n".join(lines)


def task_note(novel: dict, chapter: int, recap: list[dict], profile: dict) -> str:
    titles = {int(c["index"]): c.get("title", "") for c in novel.get("chapters", [])}
    return _head(novel.get("title", ""), chapter, titles.get(chapter, ""), chapter, recap,
                 profile.get("style", "3D 国漫"), profile.get("frame", "16:9")) + "\n" + TASK_SPEC


def cast_note(bible: dict, cast_index: dict, chapter: int, places: list[str]) -> str:
    """Characters and locations of this chapter, named exactly as the pipeline will match them."""
    here = {name for name, chapters in cast_index.get("characters", {}).items()
            if chapter in chapters}
    out = ["# 人物、地点与画风\n", "## 画风\n", bible.get("visual_style", "").strip(),
           f"\n配色：{bible.get('palette', '').strip()}\n", "## 人物（本章出场）\n"]
    for character in bible.get("characters", []):
        if not isinstance(character, dict) or character.get("name") not in here:
            continue
        out.append(f"**{character['name']}**（{character.get('role', '配角')}）")
        out.append(f"- 年龄：{character.get('age', '未写明')}")
        out.append(f"- 外貌：{character.get('appearance', '')}")
        out.append(f"- 服装：{character.get('wardrobe', '')}\n")
    out.append("没有台词的路人是背景群众，按简短描述写进画面即可，不要给他们单独的人物设定。\n")

    out.append("## 这一集的地点（「场景」列必须原样抄这里的名字）\n")
    out.append("分镜表「场景」那一列填的名字，会被拿去匹配已经画好的空场景卡。"
               "名字差一个字就匹配不上，那一镜就没有背景可用。\n")
    for entry in places:
        name, description = split_location(entry)
        out.append(f"- **{name}** —— {description or '（暂无描写）'}")
    out.append("\n同一个地方在每一镜都要写成完全一样的名字：不要加时段（写「凉亭」就一直写「凉亭」，"
               "不要写成「凉亭·夜」），不要加方位或状态，也不要自己起简称或改写。\n")
    # A closed list with no way out makes the model force a new place into an old name, the same way
    # a closed cast list made it hand a scene to whoever was on the list.  Give it somewhere to go.
    out.append("如果这一章真的出现了上面没有的地方，就自己起一个名字写进「场景」，"
               "并在 `output/新增地点.md` 里写清楚这个名字和一句空场描写"
               "（这个地方长期不变的样子：建筑结构、空间布局、固定陈设与材质、时段与主光源；不要写人）。"
               "不要为了凑上面的名单，把一个新地方硬套到旧名字上。\n")
    return "\n".join(out)


def chapter_text(novel: dict, chapter: int) -> str:
    """The chapter's own text, cut from the source by the chapter table novel.json carries.

    One pass over the source: a title may repeat later as an ordinary line, so the first line that
    matches a chapter title wins and the chapter runs to whichever start comes next.
    """
    lines = Path(novel["source"]).read_text(encoding="utf-8").splitlines()
    titles = {c.get("title", "").strip(): int(c["index"]) for c in novel.get("chapters", [])}
    starts: dict[int, int] = {}
    for position, line in enumerate(lines):
        index = titles.get(line.strip())
        if index is not None:
            starts.setdefault(index, position)
    if chapter not in starts:
        raise ValueError(f"在原文里找不到第 {chapter} 章的标题")
    here = starts[chapter]
    later = [position for position in starts.values() if position > here]
    return "\n".join(lines[here:min(later) if later else None]).strip() + "\n"


def write_brief(novel_dir: Path, chapter: int, out_dir: Path, skill: str = "") -> list[Path]:
    novel = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    profile = json.loads((novel_dir / "profile.json").read_text(encoding="utf-8"))
    cast_index = json.loads((novel_dir / "cast_index.json").read_text(encoding="utf-8"))
    recap_path = novel_dir / "recap.json"
    recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.exists() else []

    growth_path = novel_dir / "bible_growth.json"
    added_at: dict[str, int] = {}
    if growth_path.exists():
        for chapter_no, entry in json.loads(growth_path.read_text(encoding="utf-8")).items():
            for location in entry.get("locations") or []:
                added_at.setdefault(split_location(location)[0], int(chapter_no))
    text = chapter_text(novel, chapter)
    places = offered_locations(
        list(bible.get("locations", [])), chapter=chapter,
        named_here=lambda name: bool(name) and name in text,
        recent=recently_used(cast_index.get("locations", {}), chapter, CAST_RECENT_CHAPTERS),
        added_at=added_at, window=CAST_RECENT_CHAPTERS)

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in (("任务说明.md", task_note(novel, chapter, recap, profile)),
                       ("人物地点与画风.md", cast_note(bible, cast_index, chapter, places)),
                       ("source.txt", text)):
        path = out_dir / name
        path.write_text(body, encoding="utf-8")
        written.append(path)
    if skill:
        # Beside input/, not inside it: this is what drives the run, not something the agent reads.
        path = out_dir.parent / "prompt.txt"
        path.write_text(run_prompt(skill, novel, chapter), encoding="utf-8")
        written.append(path)
    return written


# Each installed skill set is driven by one sentence; everything else about the run is the same.
SKILL_TASKS = {
    "drama": "请使用本项目 .claude/skills/ 下已安装的 Drama Skills 这一组 Skill"
             "（short-drama 开头的那些，入口是 short-drama：原著分析、开发、写作、分镜等）"
             "完成任务：把 input/source.txt（小说《{title}》{chapter_title}全文）"
             "改编成系列短剧的第 {n} 集，并按这套 Skill 的方法做出分镜表。",
    "community": "请使用本项目 .claude/skills/ 下已安装的 shortfilm-prompt（社区短片提示词 Skill）"
                 "完成任务：把 input/source.txt（小说《{title}》{chapter_title}全文）"
                 "改编成系列短剧的第 {n} 集，并按这套 Skill 的方法做出分镜表。",
    "dream": "请使用本项目 .claude/skills/ 下已安装的 zy-cinematic-realism（造梦师电影化写实 Skill）"
             "完成任务：把 input/source.txt（小说《{title}》{chapter_title}全文）"
             "改编成系列短剧的第 {n} 集，并按这套 Skill 的方法做出分镜表。",
    "leos": "请使用本项目 .claude/skills/ 下已安装的 Leos 六部门导演组 Skill "
            "完成任务：把 input/source.txt（小说《{title}》{chapter_title}全文）"
            "改编成系列短剧的第 {n} 集，并按这套 Skill 的方法做出分镜表。",
    "visual": "请使用本项目 .claude/skills/ 下已安装的 Visual Skills 的视频 Skill（video）"
              "完成任务：把 input/source.txt（小说《{title}》{chapter_title}全文）"
              "改编成系列短剧的第 {n} 集，并按这套 Skill 的方法做出分镜表。",
    "shanyin": "请使用本项目 .claude/skills/ 下已安装的两个 Skill 完成任务：\n"
               "1. 先用 screenwriting-master（山音超级编剧大师），把 input/source.txt"
               "（小说《{title}》{chapter_title}全文）改编成系列短剧第 {n} 集的剧本；\n"
               "2. 再用 director-master（山音超级导演大师），从这个剧本出发完成导演流程，做出分镜表。",
}

RUN_PROMPT = """这是一次无人值守的批量运行，没有人会回复你，不要停下来等待回答。

{task}

具体要求见 input/任务说明.md，人物、地点与画风见 input/人物地点与画风.md。开始前先把这三个输入文件完整读一遍，再读 Skill 的说明，按 Skill 自己规定的流程和格式来做。

Skill 中凡是要求"暂停、等待用户指令"、"向用户提问确认"或"需要用户明确授权后才能继续"的地方：先按该 Skill 自己的自检标准检查并修正，然后视为用户已确认、已授权，继续下一步；需要用户做的选择，由你根据原著内容自行决定，并把选择和理由写进对应的输出文件。

所有过程文件和最终结果都写进 output/ 目录。完成后在 output/运行记录.md 里总结。
"""


def run_prompt(skill: str, novel: dict, chapter: int) -> str:
    if skill not in SKILL_TASKS:
        raise ValueError(f"不认识的 skill {skill!r}；有的是 {sorted(SKILL_TASKS)}")
    titles = {int(c["index"]): c.get("title", "") for c in novel.get("chapters", [])}
    task = SKILL_TASKS[skill].format(title=novel.get("title", ""), n=chapter,
                                     chapter_title=titles.get(chapter, f"第 {chapter} 章"))
    return RUN_PROMPT.format(task=task)
