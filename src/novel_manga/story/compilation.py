"""Deterministic clip compilation with per-call settings; no filesystem or model requests."""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import Counter
import copy
import math
import re
from .dialogue import merged_turns
from .framing import blocking_note
from .identity import mentioned_names
from ..models import StoryBible


@dataclass(frozen=True)
class CompilerOptions:
    max_clip_seconds: float
    soft_cut_seconds: float
    max_stages: int
    pack_mode: str
    min_standalone_seconds: float
    chat_screen: dict = field(default_factory=dict)
    anon_voice: dict = field(default_factory=dict)
    genre_rejects: list = field(default_factory=list)
    genre_crowd: str = ''
    entity_forms: dict = field(default_factory=dict)
    entity_generic: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    frame: dict = field(default_factory=dict)
    voices: dict = field(default_factory=dict)
    two_view_cast_limit: int = 2


NO_SUBTITLES = "不要在画面上生成字幕条、台词字幕、字幕栏、说明文字或任何叠加的文字条"

STRIP_PUNCT = r"[\s　，。！？；：、…—,.!?;:\"“”'‘’（）()]"

STAGE_LABELS = ["一", "二", "三", "四", "五", "六"]

EXECUTION_RULES = ("location", "duration", "stage_limit")

SENTENCE_END = re.compile(r"(?<=[。！？!?…；;])")

CLAUSE_END = re.compile(r"(?<=[，、,])")

ORDINALS = ["一名", "另一名", "第三名", "第四名", "第五名"]

LIGHT_NOUNS = re.compile(r"(月光|月色|银月|灯|烛|火光|篝火|阳光|日光|晨光|夕阳|余晖|天光|窗|光芒|金光|纹路的光|灵碑.{0,4}光)")

CAMERA_MOVE = re.compile(r"(推近|推进|拉远|拉开|摇镜|横移|跟拍|环绕|航拍|俯瞰|变焦|甩镜|手持晃动)")

ABSTRACT = re.compile(r"(气质|一丝|氛围|电影感|仿佛|宛如|犹如|似乎|莫名|难以言喻|无法形容)")

READABLE_TEXT = re.compile(r"(大字|写着|字样|显示[“\"]|刻着[“\"])")


def spoken_chars(value: str) -> int:
    return len(re.sub(STRIP_PUNCT, "", value or ""))

def compact(value: str, limit: int | None = None) -> str:
    text = re.sub(r"\s+", "", value or "").strip("；。")
    if limit is not None and len(text) > limit:
        return text[: limit - 1] + "…"
    return text

def is_title_card(shot: dict) -> bool:
    return all(turn["delivery_mode"] == "title_card" for turn in shot["turns"])

def text_chunks(text: str, limit: int) -> list[str]:
    """`text` in consecutive pieces of at most `limit` spoken characters: cut at sentence ends, a sentence that is
    still too long at commas, and a clause that is still too long anywhere.  Nothing is dropped or reordered."""
    units: list[str] = []
    for sentence in (s for s in SENTENCE_END.split(text) if s):
        if spoken_chars(sentence) <= limit:
            units.append(sentence)
            continue
        for clause in (c for c in CLAUSE_END.split(sentence) if c):
            while spoken_chars(clause) > limit:
                units.append(clause[:limit])
                clause = clause[limit:]
            if clause:
                units.append(clause)
    chunks: list[str] = []
    for unit in units:
        if chunks and spoken_chars(chunks[-1] + unit) <= limit:
            chunks[-1] += unit
        else:
            chunks.append(unit)
    return chunks

def lint_stage(shot: dict) -> list[str]:
    """Restraint test: the stage must still stand once adjectives are removed."""
    issues = []
    start, event, end = shot.get("visual_prompt", ""), shot.get("motion_prompt", ""), shot.get("end_state", "")
    camera, light = shot.get("camera", ""), shot.get("light", "")
    if not light and not LIGHT_NOUNS.search(start):
        issues.append("no_light_source")
    if not camera:
        issues.append("no_camera_position")
    if len(compact(camera)) > 60 or len(compact(light)) > 60:
        issues.append("camera_or_light_over_60_chars")
    if CAMERA_MOVE.search(camera + event + start):
        issues.append("camera_move_words")
    if ABSTRACT.search(start + event + end):
        issues.append("abstract_wording")
    if READABLE_TEXT.search(start + event + end + light) and not chat_turns(shot):
        issues.append("readable_text")
    if len(compact(start)) > 120:
        issues.append("start_state_over_120_chars")
    if not any(t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue", "silent_action", "chat_message", "singing"} for t in shot["turns"]):
        issues.append("no_audible_or_visible_action")
    return issues

def chat_turns(shot: dict) -> list[dict]:
    return [t for t in shot["turns"] if t["delivery_mode"] == "chat_message" and t["text"].strip()]

def anchor_of(name: str, bible: StoryBible, limit: int = 34, character=None, prefer: tuple[str, ...] = ()) -> str:
    """A few words that tell this character apart, for the prompt to say out loud.  `character` stands in for
    the bible entry: the phase's look, when this chapter has one (thin_phases)."""
    character = character or next((c for c in bible.characters if c.name == name), None)
    if character is None:
        return ""
    for field in (*prefer, "silhouette", "hair", "palette", "appearance"):  # a phase names its changed field first
        value = str(getattr(character, field, "") or "").strip()
        if value in {"无", "none", "-", "无特殊", "暂无"} or len(value) < 6:
            continue  # a placeholder is worse than saying nothing
        if value:
            text = value.replace("\n", " ")
            cut = text[:limit]
            end = max(cut.rfind(mark) for mark in "。，；,;")
            return cut[:end] if end > limit // 2 else cut
    return ""

def plan_totals(clips: list[dict], shots: list[dict], ctx: dict) -> dict:
    video_clips = [clip for clip in clips if clip["kind"] == "video"]
    totals = {
        "clip_count": len(clips),
        "video_clip_count": len(video_clips),
        "title_card_count": len(clips) - len(video_clips),
        "shot_count": len(shots),
        "estimated_seconds": round(sum(clip["seconds_estimate"] for clip in clips), 1),
        "requested_seconds": sum(clip["request_seconds"] for clip in clips),
        "mean_stages_per_clip": round(sum(clip["stage_count"] for clip in video_clips) / max(1, len(video_clips)), 2),
        "max_prompt_chars": max((clip["prompt_chars"] for clip in video_clips), default=0),
        "lint_stage_count": sum(len(clip.get("lint", {})) for clip in video_clips),
        "lint_by_code": {code: sum(list(v).count(code) for clip in video_clips for v in clip.get("lint", {}).values()) for code in ("no_light_source", "no_camera_position", "camera_or_light_over_60_chars", "camera_move_words", "abstract_wording", "readable_text", "start_state_over_120_chars", "no_audible_or_visible_action")},
        "visual_grammar": (ctx["grammar"] or {}).get("name"),
        "profile": ctx["profile"],
    }
    totals["lint_by_code"] = {k: v for k, v in totals["lint_by_code"].items() if v}
    return totals


class ClipCompiler:
    def __init__(self, options: CompilerOptions):
        self.options = copy.deepcopy(options)
        self.decisions = []

    def sound_clause(self, shot):
        return self._sound_clause(copy.deepcopy(shot))

    def select_cast(self, clip):
        local = copy.deepcopy(clip)
        cast = self._clip_cast(local)
        return cast, local.get('background_only')

    def clip_cast(self, clip):
        return self.select_cast(clip)[0]

    def mentioned_characters(self, text, names):
        return mentioned_names(text, names, self.options.entity_forms, self.options.aliases, self.options.entity_generic)

    spoken_chars = staticmethod(spoken_chars)

    compact = staticmethod(compact)

    def shot_seconds(self, shot: dict) -> float:
        seconds = 1.0
        for turn in shot["turns"]:
            mode = turn["delivery_mode"]
            if mode in {"visible_dialogue", "offscreen_dialogue"}:
                seconds += self.spoken_chars(turn["text"]) / 4.0 + 1.0
            elif mode == "silent_action":
                seconds += 3.0
            elif mode == "chat_message":
                # card mode: the message lives on our chat card, the clip only
                # shows the reaction - one beat, not the reading time
                seconds += 1.0 if str(self.options.chat_screen.get("render", "card")) == "card" else self.spoken_chars(turn["text"]) / 5.0 + 1.5
            elif mode == "singing":
                seconds += 6.0
        return max(3.0, round(seconds, 2))

    is_title_card = staticmethod(is_title_card)

    def _cut_checks(self, current: dict, last: dict, shot: dict, seconds: float) -> dict[str, bool]:
        """Every cut condition the packer tests, in the order it tests them."""
        return {
            "location": shot["location"] != current["location"],
            "clip_hint": bool(shot.get("clip_hint") and shot.get("clip_hint") != last.get("clip_hint")),
            "duration": current["seconds"] + seconds > self.options.max_clip_seconds,
            "stage_limit": len(current["shots"]) >= self.options.max_stages,
            "source_chunk": bool(not shot.get("clip_hint") and current["seconds"] >= self.options.soft_cut_seconds
                                 and shot["segment_id"] != last["segment_id"]),
        }

    text_chunks = staticmethod(text_chunks)

    def split_long_shot(self, shot: dict) -> list[dict]:
        """A stage too long for one clip, as consecutive stages that each fit one.

        The packer cuts only between stages, so a stage longer than the cap used to become a clip of its own whose
        request was simply clamped: 星海 484's 71-second stage went out as a 15-second request and 45 % of its lines
        were never spoken.  Its turns are dealt out in order into parts that fit - a line too long for any clip is
        first cut at sentence ends - and every part keeps the stage's picture, the later ones carrying on from it."""
        shot = copy.deepcopy(shot)
        if self.is_title_card(shot) or self.shot_seconds(shot) <= self.options.max_clip_seconds:
            return [shot]
        limit = max(8, int((self.options.max_clip_seconds - 2.0) * 4))  # one line alone: 1 s for the stage + 1 s + chars / 4
        turns = [{**turn, "text": piece} if piece != turn["text"] else turn
                 for turn in shot["turns"]
                 for piece in (self.text_chunks(turn["text"], limit)
                               if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"} and self.spoken_chars(turn["text"]) > limit
                               else [turn["text"]])]
        parts: list[list[dict]] = []
        for turn in turns:
            if parts and self.shot_seconds({**shot, "turns": parts[-1] + [turn]}) <= self.options.max_clip_seconds:
                parts[-1].append(turn)
            else:
                parts.append([turn])
        if len(parts) == 1:
            return [shot]  # one silent action longer than a clip: nothing to cut at
        self.decisions.append({"kind": "split_stage", "stage": shot.get("origin_index"), "seconds": round(self.shot_seconds(shot), 2),
                          "parts": len(parts), "cap": self.options.max_clip_seconds})
        pieces = []
        picture = "".join(str(shot.get(key, "")) for key in ("visual_prompt", "motion_prompt", "end_state"))
        on_screen = [name for name in shot.get("characters", []) if name in picture]
        for number, part in enumerate(parts, 1):
            piece = {**shot, "turns": part, "split_part": [number, len(parts)]}
            if number > 1:
                # The stage's action happens once, in its first part, which ends where the stage ends; the later parts
                # carry on from there and only finish the lines.  Copying the action into every part had a character
                # push the same door open three times.
                end = self.compact(shot.get("end_state", ""))
                text = f"承接上一段结束时的画面：{end}" if end else "承接上一段结束时的画面"
                # Who was in the picture stays in it.  clip_cast keeps a silent character of a crowded stage only when the
                # stage text names them, and with the stage's own text gone they dropped to the background, card and all
                # (星海 706: 金曜 and 伊芙 at the table in part 1, gone from parts 2 and 3).  Named only where clip_cast would
                # drop them - more than two listed - so every other part keeps its wording, and its rendered takes.
                speakers = [t["speaker_name"] for t in part if t.get("delivery_mode") == "visible_dialogue" and t.get("speaker_name")]
                listed = list(dict.fromkeys([*shot.get("characters", []), *speakers]))
                kept = [name for name in on_screen if name not in text and name not in speakers]
                if len(listed) > 2 and kept:
                    text += "；" + "、".join(kept) + "仍在画面中"
                piece["visual_prompt"] = text
                piece["motion_prompt"] = "人物保持上一段结束时的位置和姿态，接着把话说完，不重复上一段的动作"
            pieces.append(piece)
        return pieces

    def pack(self, shots: list[dict]) -> list[dict]:
        shots = copy.deepcopy(shots)
        self.decisions.clear()
        clips: list[dict] = []
        current: dict | None = None
        for shot in [piece for shot in shots for piece in self.split_long_shot(shot)]:
            if self.is_title_card(shot):
                if current:
                    clips.append(current)
                    current = None
                clips.append({"kind": "title_card", "location": shot["location"], "shots": [shot], "seconds": 3.0})
                continue
            seconds = self.shot_seconds(shot)
            if seconds > self.options.max_clip_seconds:
                # Only a single action longer than a whole clip is left like this (split_long_shot cuts at turns).
                self.decisions.append({"kind": "single_stage_over_cap", "stage": shot.get("origin_index"),
                                  "seconds": round(seconds, 2), "cap": self.options.max_clip_seconds})
            if current is not None:
                last = current["shots"][-1]
                checks = self._cut_checks(current, last, shot, seconds)
                acted = {name: hit for name, hit in checks.items() if self.options.pack_mode != "execution" or name in EXECUTION_RULES}
                cut = any(acted.values())  # in "planned" mode this is the same disjunction as before
                if cut:
                    violated = [name for name, hit in checks.items() if hit]
                    self.decisions.append({
                        "kind": "cut", "after_stage": last.get("origin_index"), "before_stage": shot.get("origin_index"),
                        "decision_reason": next(name for name, hit in acted.items() if hit), "violated_constraints": violated,
                        "candidate_seconds": round(current["seconds"] + seconds, 2),
                        "candidate_stages": len(current["shots"]) + 1,
                    })
                    clips.append(current)
                    current = None
            if current is None:
                current = {"kind": "video", "location": shot["location"], "shots": [], "seconds": 0.0}
            current["shots"].append(shot)
            current["seconds"] = round(current["seconds"] + seconds, 2)
        if current:
            clips.append(current)
        return self.absorb_small_clips(clips)

    def absorb_small_clips(self, clips: list[dict]) -> list[dict]:
        """A leftover clip under 8 s joins the neighbour it fits into (next first)."""
        clips = copy.deepcopy(clips)
        result: list[dict] = list(clips)
        changed = True
        while changed:
            changed = False
            for index, clip in enumerate(result):
                if clip["kind"] != "video" or clip["seconds"] >= self.options.min_standalone_seconds:
                    continue
                for neighbour_index in (index + 1, index - 1):
                    if not 0 <= neighbour_index < len(result):
                        continue
                    neighbour = result[neighbour_index]
                    if (
                        neighbour["kind"] == "video"
                        and neighbour["location"] == clip["location"]
                        and neighbour["seconds"] + clip["seconds"] <= self.options.max_clip_seconds
                        and len(neighbour["shots"]) + len(clip["shots"]) <= self.options.max_stages
                    ):
                        shots = clip["shots"] + neighbour["shots"] if neighbour_index > index else neighbour["shots"] + clip["shots"]
                        self.decisions.append({"kind": "absorbed", "small_clip_seconds": clip["seconds"],
                                          "into": "next" if neighbour_index > index else "previous",
                                          "merged_seconds": round(neighbour["seconds"] + clip["seconds"], 2)})
                        neighbour["shots"] = shots
                        neighbour["seconds"] = round(neighbour["seconds"] + clip["seconds"], 2)
                        result.pop(index)
                        changed = True
                        break
                if changed:
                    break
        return result

    lint_stage = staticmethod(lint_stage)

    chat_turns = staticmethod(chat_turns)

    def screen_clause(self, shot: dict) -> str:
        """What the phone screen shows: the chat template plus the messages, verbatim,
        as the only readable text the video may contain.  Sender names go above the
        bubble, never inside it."""
        turns = self.chat_turns(shot)
        if not turns:
            return ""
        if str(self.options.chat_screen.get("render", "card")) == "card":
            # The messages are drawn by chat_card.py and cut in as their own segment;
            # asking the video model for legible Chinese only produces garbled text.
            return ("屏幕内容：不要拍屏幕内容——手机或电脑屏幕背对镜头、被手指遮住或只见反光，屏幕上不出现任何文字；"
                    "镜头给看屏幕的人的表情和动作。")
        me = self.options.chat_screen.get("self_name", "")
        bubbles = []
        for t in turns:
            who, text = t["speaker_name"], t["text"].strip()
            if me and who == me:
                bubbles.append(f"本人{who}的绿色气泡靠右，正文「{text}」")
            else:
                bubbles.append(f"昵称「{who}」显示在气泡上方，白色气泡正文「{text}」")
        group = f"群名「{self.options.chat_screen['group_name']}」，" if self.options.chat_screen.get("group_name") else ""
        return (f"屏幕内容：手机屏幕特写占画面主体、屏幕正对镜头，{self.options.chat_screen['app']}界面（{group}{self.options.chat_screen['layout']}），"
                f"依次弹出{len(turns)}条消息，气泡内只有正文、不带任何括号或昵称，文字为清晰可读的简体中文、无乱码、与下列内容逐字一致：{'；'.join(bubbles)}。")

    def _sound_clause(self, shot: dict) -> str:
        parts = []
        seen: dict[str, int] = {}
        for turn in merged_turns(shot):
            mode = turn["delivery_mode"]
            emotion = turn.get("emotion") or "平静"
            who = turn["speaker_name"]
            if mode == "visible_dialogue":
                parts.append(f"中文普通话，{emotion}，{who}开口说：{{{turn['text']}}}")
            elif mode == "singing":
                manner = turn["text"].strip() or "轻声哼唱一段温柔的无词旋律"
                parts.append(f"{who}{manner}：原创的、没有歌词的哼唱，不是任何已有歌曲，口型为哼唱，音量柔和")
            elif mode == "offscreen_dialogue":
                voice = self.options.anon_voice.get(who, f"画外的{who}")
                if who in {"无名族人", "无名少年", "无名少女"}:
                    nth = seen.get(who, 0)
                    seen[who] = nth + 1
                    voice = voice.replace("一名", ORDINALS[min(nth, len(ORDINALS) - 1)], 1)
                parts.append(f"中文普通话，{emotion}，{voice}说：{{{turn['text']}}}，画面中无人开口")
        if shot.get("sfx") and self.compact(shot["sfx"]) not in {"无", "没有", "无声", "无音效", "空", "none"}:
            parts.append(f"<{shot['sfx']}>")
        if self.chat_turns(shot):
            parts.append("<手机消息提示音>")
        if not parts:
            parts.append("只有环境声，无人说话")
        return "；".join(parts)

    def _clip_cast(self, clip: dict) -> list[str]:
        """Named actors who need a reference image in this clip.

        An actor earns a reference by speaking on camera or by being named in a
        stage's picture text.  A silent extra who is merely listed as present
        (a guest at the far table) gets no image: with two similar-looking women
        referenced, the video model swaps costumes between them.  Such extras are
        still described in the stage text as background.
        """
        listed: list[str] = []
        active: set[str] = set()
        for shot in clip["shots"]:
            explicit = shot.get("in_frame") or []
            active.update(explicit)
            for name in dict.fromkeys([*shot["characters"], *explicit]):
                if name not in listed:
                    listed.append(name)
            picture = "".join(str(shot.get(k, "")) for k in ("visual_prompt", "motion_prompt", "end_state"))
            # By full name, alias or short form: the prose says 薇奥拉 for 薇奥拉公主 and 琥珀猫 for 琥珀·高德, and a
            # substring test on the full name sent both to the background (雾月 761, 2026-09-13).
            named = set(self.mentioned_characters(picture, shot["characters"]))
            for name in shot["characters"]:
                if name in named or name in picture:
                    active.add(name)
            for turn in shot["turns"]:
                if turn["delivery_mode"] == "visible_dialogue" and turn["speaker_name"]:
                    active.add(turn["speaker_name"])
                    if turn["speaker_name"] not in listed:
                        listed.append(turn["speaker_name"])
        if len(listed) <= 2:
            return listed
        kept = [name for name in listed if name in active]
        clip["background_only"] = [name for name in listed if name not in active]
        return kept or listed

    anchor_of = staticmethod(anchor_of)

    def compile_prompt(self, clip: dict, bible: StoryBible, cast: list[str], bindings: list[str], location_binding: str, grammar: dict | None = None, frame: dict | None = None) -> str:
        clip = copy.deepcopy(clip)
        genre_rejects = list(self.options.genre_rejects)
        frame = frame or self.options.frame
        shots = clip["shots"]
        cast_text = "、".join(cast) if cast else "无具名人物"
        start = self.compact(shots[0]["motion_prompt"], 30)
        end = self.compact(shots[-1]["end_state"], 30)
        lines = [
            f"【生成目标】生成一段{frame['text']}的中国国漫短剧片段，约{clip['request_seconds']}秒。核心主体是{cast_text}，主要事件是从“{start}”到“{end}”。"
        ]
        if bindings:
            lines.append("【人物】" + "。".join(bindings) + "。")
        if cast:
            # 133 of 613 flagged clips in review had people who were never cast.
            others = "；远处模糊背景里只允许" + "、".join(clip["background_only"]) if clip.get("background_only") else ""
            extras = list(dict.fromkeys(e for shot in shots for e in (shot.get("extras") or [])))
            if extras:
                lines.append(f"【人数】画面中始终只有这{len(cast)}位具名人物：{cast_text}，另加{len(extras)}位无参考图的配角（按描述画，不得画成具名人物的样子）：{'、'.join(extras)}；"
                             f"除此之外不出现任何人（老者、路人、随从、背景人物都不要）{others}。")
            else:
                lines.append(f"【人数】画面中始终只有这{len(cast)}位人物：{cast_text}；无名角色只在画外发声、不入镜；不出现任何未列出的人（老者、路人、随从、背景人物都不要）{others}。")
        if clip.get("identity_notes"):
            lines.append("【身份区分】" + self.compact(clip["identity_notes"]) + "。")
        if clip.get("background_only"):
            lines.append("【远景人物】" + "、".join(clip["background_only"]) + "只作远处模糊背景，不入近景、不开口，本段不提供他们的参考图；不得把他们的长相或服饰用在有参考图的角色身上。")
        lines.append("【场景】" + location_binding + "。")
        if grammar:
            axes = "；".join(
                f"{label}：{grammar[key]}" for label, key in (("光影与对比", "light_contrast"), ("色彩与曝光", "color_exposure"), ("镜头与机位", "lens_camera"), ("构图与空间", "composition_space")) if grammar.get(key)
            )
            if axes:
                lines.append("【视觉语法】" + axes + "。")
        for index, shot in enumerate(shots):
            label = STAGE_LABELS[index]
            if index == 0:
                head = f"{shot['shot_scale']}开场。开始时：{self.compact(shot['visual_prompt'])}"
            else:
                head = f"切至{shot['shot_scale']}。承接上一阶段：{self.compact(shots[index - 1]['end_state'])}。画面：{self.compact(shot['visual_prompt'])}"
            def carried(field: str, label: str) -> str:
                value = self.compact(shot.get(field, ""))
                if not value:
                    return ""
                previous = self.compact(shots[index - 1].get(field, "")) if index else ""
                if value in {"同上", previous} and index:
                    return f"{label}同上。"
                return f"{label}：{value}。"
            witness = carried("camera", "机位")
            source_light = carried("light", "光源")
            extras_note = f"本阶段无参考图的配角：{'、'.join(shot['extras'])}（按描述画）。" if shot.get("extras") else ""
            listen_note = (f"本阶段只有{'、'.join(shot['characters'])}正脸入镜；{'、'.join(shot['listeners'])}只露背影或在画外，不入近景、嘴不动。"
                           if shot.get("listeners") else "")
            lines.append(
                f"【阶段{label}·{shot['shot_scale']}】{head}。{witness}{source_light}主要事件：{self.compact(shot['motion_prompt'])}。"
                f"{blocking_note(shot)}{extras_note}{listen_note}"
                f"{self.screen_clause(shot)}声音：{self._sound_clause(shot)}。结束时：{self.compact(shot['end_state'])}。"
            )
        scales = "、".join(dict.fromkeys(shot["shot_scale"] for shot in shots))
        ambience = list(dict.fromkeys(shot["sfx"] for shot in shots if shot.get("sfx")))
        ambience_text = "、".join(ambience) if ambience else "现场环境声"
        lines.append(f"画面呈现{(grammar or {}).get('style_line') or bible.visual_style}。")
        lines.append(f"镜头采用{frame['text']}，按阶段切换景别（{scales}），每个阶段开始时切一次画面，阶段内机位固定不运镜；{frame['composition']}；同一场景内保持人物左右位置和视线方向不变。")
        lines.append(f"声音包括角色对白、<{ambience_text}>和与动作同步的音效；无背景音乐。")
        if any(self.chat_turns(shot) for shot in shots) and str(self.options.chat_screen.get("render", "card")) != "card":
            group = f"群名「{self.options.chat_screen['group_name']}」、" if self.options.chat_screen.get("group_name") else ""
            lines.append(f"【保持一致】保持人物身份、数量、服装、固定道具位置、空间方向和声音关系稳定；除手机屏幕上的{group}昵称和指定的聊天消息外，画面中不出现其他文字、数字、字幕、Logo或水印；屏幕上的消息文字必须与指定内容逐字一致、简体中文、无乱码，昵称在气泡上方而不在气泡内；聊天界面在各阶段保持同一布局；不出现血液和伤口；不新增具名人物。")
        else:
            lines.append("【保持一致】保持人物身份、数量、服装、固定道具位置、空间方向和声音关系稳定；画面中不出现任何文字、数字、Logo或水印；不出现血液和伤口；不新增具名人物。")
        avoid = [self.compact(shot.get("avoid", "")) for shot in shots if shot.get("avoid")]
        avoid = list(dict.fromkeys(a for a in avoid if a))
        rejects = [str(r) for r in (grammar or {}).get("rejects", []) if r]
        if str(self.options.chat_screen.get("render", "card")) == "card":
            # The chat screen is drawn by us now, so the "except the chat messages"
            # exemption in the genre and grammar rejects no longer applies.
            rejects = [r.replace("（手机屏幕上剧本指定的聊天消息除外）", "") for r in rejects]
            genre_rejects = [r.replace("（手机屏幕上剧本指定的聊天消息除外）", "") for r in genre_rejects]
        lines.append("【不要】" + "；".join([*avoid, *genre_rejects, *rejects, NO_SUBTITLES]) + "。")
        return "\n".join(lines)

    def shots_for_plan(self, plan: dict, shots: list[dict], clip_ids: set[str] | None = None) -> dict[str, list[dict]]:
        """Recover each clip's own stage parts without moving cuts or repeating a whole split stage.

        New entries record shot_parts. Older plans identify standalone split parts in split_long_stages; other
        old packer splits are recovered by their ordered occurrences. A range that cannot be recovered is an
        error, so callers leave that episode untouched instead of guessing a new cut.
        A targeted rebuild still counts siblings from the whole plan, but validates
        and returns only its requested clips. Unrelated broken stages cannot block it.
        """
        shots = copy.deepcopy(shots)
        by_index = {s["index"]: s for s in shots}
        legacy_parts = {cid: (part, len(ids)) for ids in (plan.get("split_long_stages") or {}).get("split", {}).values()
                        for part, cid in enumerate(ids, 1)}
        spans = {}
        for clip in plan.get("clips", []):
            if clip.get("kind") != "video" or not clip.get("shot_indexes"):
                continue
            indexes = clip["shot_indexes"]
            if clip.get("shot_parts"):
                spans[clip["clip_id"]] = clip["shot_parts"]
            elif clip["clip_id"] in legacy_parts:
                if len(set(indexes)) != 1:
                    if clip_ids is not None and clip["clip_id"] not in clip_ids:
                        continue
                    raise ValueError(f"{clip['clip_id']}: split part has multiple source stages")
                spans[clip["clip_id"]] = [{"index": indexes[0], "part": list(legacy_parts[clip["clip_id"]])}]
            else:
                # The buggy rebuild wrote every part's repeated source index into each sibling clip.
                # A greedy stage split cannot put two of its parts back in one bounded clip, so these
                # legacy repeats name one occurrence; explicit shot_parts, above, remain authoritative.
                spans[clip["clip_id"]] = [{"index": i} for i in dict.fromkeys(indexes)]
        occurrences = Counter(s["index"] for own in spans.values() for s in own)
        needed = {s["index"] for cid, own in spans.items() if clip_ids is None or cid in clip_ids for s in own}
        parts = {i: self.split_long_shot(copy.deepcopy(by_index[i])) for i in needed if i in by_index}
        used: Counter = Counter()
        recovered = {}
        for cid, own in spans.items():
            selected = []
            for span in own:
                index = span["index"]
                used[index] += 1
                if clip_ids is not None and cid not in clip_ids:
                    continue
                if index not in parts:
                    raise ValueError(f"{cid}: source stage {index} is missing")
                pieces = parts[index]
                part, total = span.get("part") or (used[index], occurrences[index])
                if part == total == 1:
                    # An old uncut stage stays one stage. Completing its cast must not alter its cuts
                    # merely because today's packer would split it; actual split siblings use total > 1.
                    selected.append(copy.deepcopy(by_index[index]))
                    continue
                if len(pieces) != total or not 1 <= part <= len(pieces):
                    raise ValueError(f"{cid}: stage {index} has {len(pieces)} parts, plan requires part {part}/{total}")
                selected.append(copy.deepcopy(pieces[part - 1]))
            if clip_ids is None or cid in clip_ids:
                recovered[cid] = selected
        return recovered

    plan_totals = staticmethod(plan_totals)
