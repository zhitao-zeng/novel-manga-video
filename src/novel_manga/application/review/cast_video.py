"""Watch the whole take against the cast: who is on screen that should not be, and who looks wrong.

The verify review reads four to eight frames against one general question.  On the agent ch12 render
(2026-09-26) it failed 3 of the 11 clips a person could see were wrong - a clone at the edge of the frame, a
second suit, a gold faceplate on the silver one - and the retakes it asked for traded a defect it could see for
one it could not: clips 02 and 36 both came back with a gold helmet and passed.

Two questions about the whole take (Flash-Next, 640p), each measured on those 61 takes against labels:
  - who is there: the cast, the extras it allows, and the English shot description H3 was given, so what the
    script asks for (a second suit flying in) is not counted as extra.  Extra people, clones and extra suits:
    8 of 8.
  - what each person wears, described with nothing said about the cards; then a text-only step holds that
    description against the cards and the book's watch list.  Told what someone should look like, the judge
    repeats it back ("白色盔甲" on a gold helmet): six wordings of that question caught 2-6 of the 12 wrong looks.
    Described first and compared after, 9 of 12, with 1 of 12 clean clips flagged.
What it finds is kept as the issue; the correction sent to a retake says what to draw, never what went wrong,
because H3 draws a described mistake back into the scene (雾月 2026-09-14).  Off unless NOVEL_REVIEW_CAST_VIDEO=1.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from novel_manga.application.review import evidence as review_evidence
from novel_manga.llm import client as model_client
from novel_manga.llm.config import ENDPOINTS, JsonEndpoint
from novel_manga.media.common import sha256_file, sha256_text
from novel_manga.story.h3 import view_of
from novel_manga.review.prompts import shot_contract
from novel_manga.util import atomic_write_json

FOUND = ("extra_person", "extra_object", "face_artifact")
POLICY = "cast-video-v3-observation-first"
SCHEMA = model_client.obj({"people": {"type": "array", "items": {"type": "string"}},
                           **{key: {"type": "boolean"} for key in FOUND}, "note": {"type": "string"}})
QUESTION = ("这是一段动画视频片段。按剧本，画面里应该出现的人只有：{cast}。{extras}\n{looks}\n"
            "这一段的镜头描述（英文，是发给视频模型的原话；里面写到的人和东西都是剧本要的）：\n{shot}\n{world}\n"
            "请只根据你真正看到的回答，不要猜：\n"
            "people：画面里出现过的每一个人（包括只露出一部分的、边缘的、背影、远处的、穿盔甲或机甲的），每人一句话写是谁、什么样子；\n"
            "extra_person：是否有剧本名单以外的人，或者同一个角色同时出现两次（克隆）；\n"
            "extra_object：盔甲、机甲、战衣或道具（穿着的或空的）是否比镜头描述写的多；\n"
            "face_artifact：人脸上是否有明显的异常（流液体、变形、融化、发光等）；\n"
            "note：一句话说明你看到的问题，没有就写“无”。")
DESCRIBE = model_client.obj({"people": {"type": "array", "items": model_client.obj({"who": {"type": "string"},
                                                                                  "wears": {"type": "string"}})}})
DESCRIBE_QUESTION = ("逐个描述这段视频里出现的每一个人或人形物件（包括边缘的、背影、远处的）：who 只写画面位置和可见特征，不猜姓名；"
                     "wears 写发型发色、有没有眼镜和胡子、身上看得见的每件衣服和它的颜色（有没有外套、马甲、领带、帽子）；"
                     "穿盔甲的，写出盔甲、头盔、面甲各是什么颜色（金色就写金色），面甲开着还是合着。只写看到的，不要猜，不要美化。")
COMPARE = model_client.obj({"checks": {"type": "array", "items": model_client.obj({
    "name": {"type": "string"}, "entity_kind": {'type': 'string', 'enum': ['character', 'object']},
    "observed": {"type": "string"}, "expected": {"type": "string"},
    "difference": {"type": "string", "maxLength": 150},
    "visibility": {"type": "string", "enum": ["visible", "uncertain", "outside_frame"]},
    "same_entity": {"type": "boolean"}, "same_color_family": {"type": "boolean"},
    "state_stage": {"type": "integer"},
    "expected_state": {"type": "string", "enum": ["open", "closed", "opening", "closing", "unknown"]},
    "kind": {"type": "string", "enum": ["none", "costume", "color", "state"]},
    "instruction": {"type": "string"}})}})
COMPARE_QUESTION = ("下面是一段视频里每个人的外观描述（看视频的人写的），以及剧本角色在参考卡上的样子。\n"
                    "参考卡：\n{cards}\n{watch}\n视频里的描述：\n{seen}\n\n"
                    "先匹配同一个实体，不能把独立空甲或背景装置的描述算到穿甲人物身上。角色填entity_kind=character；"
                    "独立物件另列一项，entity_kind=object，name用本镜物件名。same_entity表示观察和这项name指同一个实体，"
                    "不是判断它是否属于演员名单。独立空甲不是托尼，但可依据本镜明确要求的同款装备配色检查其颜色，"
                    "不能因为不是人物就漏掉物件的明确配色错误；未指定物件外观则不猜。"
                    "每项先摘录 observed（实际看见）与 expected（本镜明确要求）；visibility 表示该部位清晰可见、看不清或在画外。"
                    "kind 只能选：none=无问题或证据不足；costume=可见的衣物增加或减少；color=明显跨色系变化；"
                    "state=违反本镜明确状态。颜色差异禁止填costume。same_color_family 记录是否只是同色系明暗差异。"
                    "深灰与黑、米白与白、浅棕与米色、银灰与银白及照明导致的明暗都属于同色系，kind=none。"
                    "卡上姿势、开合不作为默认要求，阶段档案也不能覆盖逐镜计划；镜头没要求的状态不判错。"
                    "计划允许人物穿甲加独立空甲时，只要没有第二个人体证据，就不能把空甲当人物的第二个身体。"
                    "没有提到或构图裁掉的部位不能判缺失。difference 最多两句话，先核实是否真有差异，不展开自我辩论；无问题写无。"
                    "先写观察和依据，最后kind必须与difference一致。面罩状态只依据提供的逐镜状态记录，state_stage填写记录编号，"
                    "expected_state原样填写对应state；未提供有效状态时填0/unknown，不能仅凭角色卡提出状态错误。"
                    "instruction 只写本镜应画出的正向状态，"
                    "保留本镜开合过程、画外和构图，不复述错误，不要求露出镜头外的脸。\n{shot}")
CARD_LOOK = model_client.obj({"look": {"type": "string"}})
CARD_LOOK_QUESTION = ("这是{name}的角色参考卡。用一句中文写出卡上人物的发型发色、有没有眼镜和胡子，以及衣着和配色："
                      "看得见的每件衣物或盔甲、各自的颜色，有没有外套、领带、帽子；穿盔甲的写明盔甲、头盔和面甲的颜色，"
                      "只记录图中可见的稳定外观。不要写面甲开合、动作、表情、站坐、背景或机位；这些由每一镜的计划决定。")


def enabled() -> bool:
    return os.environ.get("NOVEL_REVIEW_CAST_VIDEO", "").strip() == "1"


def wrong_looks(answer: dict) -> list[dict]:
    return [c for c in answer.get("looks") or [] if c.get("costume_wrong") or c.get("color_wrong") or c.get("state_wrong")]


def findings(answer: dict) -> list[str]:
    return [key for key in FOUND if answer.get(key)] + (["look_wrong"] if wrong_looks(answer) else [])


def cast_cards(clip: dict, novel_dir: Path) -> dict[str, Path]:
    """Use the request's actual cards, preferring the full figure when both views exist.

    A closed-only request still supplies its closed card. Neither view dictates the shot's changing state.
    """
    cast, cards = set(clip.get("cast") or []), {}
    refs = sorted((r for r in clip.get("references") or [] if r.get("role") == "character" and r.get("name") in cast
                   and (novel_dir / str(r.get("path") or "")).is_file()),
                  key=lambda r: view_of(r) != "turnaround")
    for ref in refs:
        cards.setdefault(ref["name"], novel_dir / ref["path"])
    return cards


def card_look(novel_dir: Path, name: str, card: Path) -> str:
    """What the card shows, in words - asked once per picture (and per wording of the question) and kept in
    series_assets/card_looks.json."""
    store = novel_dir / "series_assets" / "card_looks.json"
    key = str(card.relative_to(novel_dir)) if card.is_relative_to(novel_dir) else str(card)
    digest, asked = sha256_file(card), sha256_text(CARD_LOOK_QUESTION)[:12]
    try:
        saved = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    known = saved.get(key) or {}
    if known.get("artifact_sha256") == digest and known.get("asked") == asked and known.get("look"):
        return known["look"]
    answer = model_client.ask_json([model_client.image_part(card, 1024), {"type": "text", "text": CARD_LOOK_QUESTION.format(name=name)}],
                                   CARD_LOOK, name="card_look", max_tokens=300, timeout=180)
    look = str(answer.get("look") or "").strip()
    if look:
        atomic_write_json(store, {**saved, key: {"artifact_sha256": digest, "asked": asked, "look": look}})
    return look


def watch_list(novel_dir: Path) -> dict[str, str]:
    """review_watch.txt, one '角色名：问句' a line - a mistake H3 makes with someone in this book, asked so that
    true means it happened: '托尼·斯塔克：他的面甲或头盔是不是被画成了金色'."""
    path = novel_dir / "review_watch.txt"
    if not path.is_file():
        return {}
    rows = [line.split("：", 1) for line in path.read_text(encoding="utf-8").splitlines()
            if "：" in line and not line.lstrip().startswith("#")]
    return {name.strip(): text.strip() for name, text in rows if name.strip() and text.strip()}


def watched(clip: dict, novel_dir: Path) -> list[str]:
    """The watch-list lines about this clip's cast."""
    watch = watch_list(novel_dir)
    return [f"{name}：{watch[name]}" for name in clip.get("cast") or [] if name in watch]


def looks(clip: dict, novel_dir: Path, episode_dir: Path) -> dict[str, str]:
    """Appearance from the actual card, without appending contradictory chapter-wide pose/state prose."""
    cards, out = cast_cards(clip, novel_dir), {}
    for name in clip.get("cast") or []:
        worn = card_look(novel_dir, name, cards[name]) if name in cards else ""
        # Cards are the actual clothing/color reference. A phase's prose also contains default states
        # (faceplate open), which contradicted the shot and made repairs undo a planned closing.
        out[name] = worn or "与参考卡一致"
    return out


def question(clip: dict, draw: dict[str, str], world: str) -> str:
    english = str(clip.get("prompt_h3") or "")
    shot = english.split("detailed_description:", 1)[-1].split("overall_soundscape:", 1)[0].strip() or str(clip.get("prompt") or "")
    extras = [str(e.get("name") if isinstance(e, dict) else e) for e in clip.get("extras") or []]
    return QUESTION.format(cast="、".join(clip.get("cast") or []) or "（无人）",
                           extras=f"背景里允许出现：{'、'.join(extras)}。" if extras else "",
                           looks="\n".join(f"{name}：{text}" for name, text in draw.items()), shot=shot, world=world) + shot_contract(clip)


def video_part(video: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        small = Path(tmp) / "small.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", "scale=640:-2", "-c:v", "libx264",
                        "-crf", "30", "-an", str(small)], check=True)
        return {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(small.read_bytes()).decode()}}


def ask(parts: list[dict], schema: dict, name: str) -> dict:
    judge = JsonEndpoint.from_env(ENDPOINTS[os.environ.get("NOVEL_REVIEW_VIDEO_JUDGE", "flashnext")])
    # 1500, doubled once on truncation: a comparison with long differences ran past 1800 (agent ch12 clip_02).
    return model_client.ask_json(parts, schema, name=name, max_tokens=1500, timeout=300, settings=judge)


def compare(draw: dict[str, str], items: list[str], seen: list[dict], clip: dict | None = None,
            states: dict | None = None) -> list[dict]:
    """The description held against the cards, in text alone - nothing to see, so nothing to agree with."""
    if not draw or not seen:
        return []
    text = COMPARE_QUESTION.format(cards="\n".join(f"{name}：{look}" for name, look in draw.items()),
                                   watch=("这本书里 H3 常出的错：\n" + "\n".join(items)) if items else "",
                                   seen="\n".join(f"{p.get('who')}：{p.get('wears')}" for p in seen),
                                   shot=shot_contract(clip or {}) + '\n已确认逐镜面罩状态：' + json.dumps(states or {}, ensure_ascii=False))
    rows = ask([{"type": "text", "text": text}], COMPARE, "clip_look_compare").get("checks") or []
    return [look_result(row, states=states) for row in rows]


def look_result(row: dict, *, states: dict | None = None) -> dict:
    """Only an observed difference on the matching entity may become a retake; no color word dictionary."""
    eligible = row.get('same_entity') is True and row.get('visibility') == 'visible'
    expected = ((states or {}).get(row.get('name')) or {}).get(str(row.get('state_stage')))
    state_backed = expected in {'open', 'closed', 'opening', 'closing'} and row.get('expected_state') == expected
    return {**row, 'costume_wrong': eligible and row.get('kind') == 'costume',
            'color_wrong': eligible and row.get('kind') == 'color' and row.get('same_color_family') is False,
            'state_wrong': eligible and row.get('kind') == 'state' and state_backed}


def check(clip: dict, video: Path, work_dir: Path) -> tuple[dict, dict[str, str]]:
    novel_dir = review_evidence.bible_root(work_dir)
    draw = looks(clip, novel_dir, work_dir.parents[2])
    take = video_part(video)
    answer = ask([take, {"type": "text", "text": question(clip, draw, review_evidence.review_world_context(novel_dir))}],
                 SCHEMA, "clip_cast_video")
    seen = list(ask([take, {"type": "text", "text": DESCRIBE_QUESTION}], DESCRIBE, "clip_describe").get("people") or [])
    from novel_manga.application.packing.visor import recorded_clip_states
    states = recorded_clip_states(work_dir.parents[2], clip)
    return {**answer, "policy": POLICY, "seen": seen,
            "looks": compare(draw, watched(clip, novel_dir), seen, clip, states)}, draw


def instruction(clip: dict, answer: dict, draw: dict[str, str]) -> str:
    """What the retake should draw: the expected state, not the judge's description of the mistake."""
    cast, parts = list(clip.get("cast") or []), []
    if answer.get("extra_person") or answer.get("extra_object"):
        parts.append(f"画面里只有{'、'.join(cast) or '镜头描述写到的人'}，每人只出现一次，不出现其他人物"
                     + ("，也不出现镜头描述之外的盔甲、战衣或道具复制品" if answer.get("extra_object") else ""))
    parts.extend(c.get('instruction') or f"{c['name']}的发型和衣着与参考图完全一致：{draw[c['name']]}"
                 for c in wrong_looks(answer) if c.get('instruction') or c.get("name") in draw)
    if answer.get("face_artifact"):
        parts.append("人物面部干净自然，没有液体、变形或发光")
    return "；".join(parts)


def merge(verdict: dict, answer: dict, clip: dict, draw: dict[str, str]) -> dict:
    verify = {**(verdict.get("verify") or {}), "cast_video": answer}
    found = findings(answer)
    if not found:
        return {**verdict, "verify": verify}
    seen = [str(answer.get("note") or "").strip()] + [f"{c.get('name')}：{c.get('difference')}" for c in wrong_looks(answer)]
    note = "；".join(p for p in seen if p and p != "无") or "、".join(found)
    return {**verdict, "severity": "fail", "identity_ok": False,
            "identity_issue": "；".join(p for p in (verdict.get("identity_issue"), note) if p),
            "visual_defects": verdict.get("visual_defects") or bool({"extra_object", "face_artifact"} & set(found)) or None,
            "feedback": "；".join(dict.fromkeys(p for p in (str(verdict.get("feedback") or "").strip(),
                                                           instruction(clip, answer, draw)) if p)),
            "verify": {**verify, "verdict": "obvious"}}


def review(clip: dict, video: Path, work_dir: Path, verdict: dict) -> dict:
    """Fold in this enabled check; failure to check is review_error, never a pass or a reason to generate."""
    try:
        answer, draw = check(clip, video, work_dir)
    except Exception as error:  # noqa: BLE001 - the frame review stands on its own
        model_client.log(f"{clip.get('clip_id')}: cast video check not made: {type(error).__name__}: {str(error)[:120]}")
        return {**verdict, "severity": "review_error", "error": "whole-take review incomplete",
                "verify": {**(verdict.get("verify") or {}), "cast_video": {"error": str(error)[:300]}}}
    return merge(verdict, answer, clip, draw)
