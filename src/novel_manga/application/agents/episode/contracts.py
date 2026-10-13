"""Adopt an independently authored episode into the existing render formats.

This boundary binds addresses, references and authored words. It performs no
model calls, screenplay normalization, prompt compilation or automatic cutting.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import PurePosixPath

from novel_manga.application.profiles import h3_stamp
from novel_manga.application.repair.agent import sections, validate_prompt
from novel_manga.story.dialogue import BOUND_SPEECH as SPEECH, character_pictures, dialogue_punctuation, speech_is_offscreen, subject_map
from novel_manga.story.h3 import CJK, NEGATIVE_DIRECTION, asset_subjects, request_issues
from novel_manga.story.scene import wears_bindings
from novel_manga.story.fields import ACTION_KEYS
from novel_manga.story.field_validation import require_text, require_action, require_wears, delivery_issue
from novel_manga.story.voice_delivery import visual_request

POLICY = "episode-agent-authored-v1"
DELIVERY_MODES = {"visible_dialogue", "offscreen_dialogue", "chat_message", "singing"}




def _strings(value, field, *, empty=True):
    if not isinstance(value, list) or (not empty and not value):
        raise ValueError(f"{field} requires {'an' if empty else 'a nonempty'} array")
    for item in value:
        require_text(item, field, empty=False)
    if len(set(value)) != len(value):
        raise ValueError(f"{field} repeats an entry")
    return value


def _seconds(value, field):
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} requires positive seconds")
    return value


def _catalog(catalog):
    rows = catalog.get("assets") if isinstance(catalog, dict) else catalog
    if not isinstance(rows, list):
        raise ValueError("catalog requires an asset array")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("catalog asset requires an object")
        for key in ("id", "name", "path"):
            require_text(row.get(key), f"catalog.{key}", empty=False)
        path = PurePosixPath(row["path"])
        if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise ValueError("catalog.path must be book-relative")
        if row.get("role") not in {"character", "location", "prop", "voice", "scene_state"}:
            raise ValueError("catalog asset requires a current reference role")
        if row["id"] in result:
            raise ValueError("catalog repeats an asset id")
        result[row["id"]] = copy.deepcopy(row)
    return result


def _shot(row, expected, sources):
    if not isinstance(row, dict) or type(row.get("index")) is not int or row["index"] != expected:
        raise ValueError("shots require consecutive index values starting at 1")
    shot = copy.deepcopy(row)
    for key in ("characters", "in_frame", "segment_ids"):
        _strings(shot.get(key), f"shot {expected}.{key}", empty=key != "segment_ids")
    if not set(shot["in_frame"]) <= set(shot["characters"]):
        raise ValueError(f"shot {expected}.in_frame must be a subset of characters")
    for key, alias in (("start_state", "start"), ("end_state", "end")):
        if key not in shot and alias in shot:
            shot[key] = shot[alias]
    for key in ("location", "start_state", "event", "end_state", "camera"):
        require_text(shot.get(key), f"shot {expected}.{key}")
    if isinstance(shot.get("sfx"), list):
        for sound in shot["sfx"]:
            require_text(sound, f"shot {expected}.sfx item")
        shot["authored_sfx"] = copy.deepcopy(shot["sfx"])
        shot["sfx"] = "; ".join(shot["sfx"])
    for key in ("light", "sfx", "shot_scale"):
        require_text(shot.setdefault(key, ""), f"shot {expected}.{key}")
    for key in ("scene_id", "shot_id", "scene_time", "cut"):
        if key in shot:
            require_text(shot[key], f"shot {expected}.{key}")
    for key in ("extras", "props", "independent_props", "scene_objects", "listeners"):
        if key == "extras" and isinstance(shot.get(key), str):
            shot[key] = [shot[key]] if shot[key] else []
        _strings(shot.setdefault(key, []), f"shot {expected}.{key}")
    actions = shot.setdefault("actions", [])
    if not isinstance(actions, list):
        raise ValueError(f"shot {expected}.actions requires an array")
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("action requires an object")
        # Model drafts sometimes use description for the same authored action
        # text. Bind that unambiguous spelling without changing actor or target.
        if 'action' not in action and isinstance(action.get('description'), str):
            action['action'] = action.pop('description')
        require_action(action)
    require_wears(shot.setdefault("wears", {}))
    if not isinstance(shot.get("turns"), list):
        raise ValueError(f"shot {expected}.turns requires an array")
    for turn in shot["turns"]:
        if not isinstance(turn, dict) or turn.get("delivery_mode") not in DELIVERY_MODES:
            raise ValueError("turn requires an object and current delivery_mode")
        for key in ("speaker_name", "text"):
            require_text(turn.get(key), f"turn.{key}", empty=False)
        for key in ("emotion", "chat_target"):
            require_text(turn.setdefault(key, ""), f"turn.{key}")
        if "inner_monologue" in turn and type(turn["inner_monologue"]) is not bool:
            raise ValueError("inner_monologue requires a boolean")
        if issue := delivery_issue(turn):
            raise ValueError(issue)
        if turn["delivery_mode"] == "visible_dialogue" and turn["speaker_name"] not in shot["in_frame"]:
            raise ValueError(f"shot {expected}: visible speaker must belong to the authored in_frame: "
                             f"{turn['speaker_name']}; in_frame={shot['in_frame']}")
    if any(sid not in sources for sid in shot["segment_ids"]):
        raise ValueError(f"shot {expected} selects an unknown source segment")
    shot.update(origin_index=expected, segment_id=shot["segment_ids"][0],
                source_refs=[{"segment_id": sid, "source_quote": sources[sid]["text"]}
                             for sid in shot["segment_ids"]],
                source_quote="\n".join(sources[sid]["text"] for sid in shot["segment_ids"]),
                visual_prompt=shot["start_state"], motion_prompt=shot["event"])
    return shot


def _reference_bindings(clip, blocks):
    """Check numbered reference associations, without interpreting their prose."""
    subjects = subject_map(clip)
    expected = {number: {picture for picture, _ in own}
                for name, own in character_pictures(clip).items()
                for number in [subjects[name]]}
    expected.update({number: {picture} for number, picture, _ in asset_subjects(clip)})
    definitions = blocks["subject_definitions"]
    rows = list(re.finditer(r"(?m)^<(Subject|Audio) (\d+)>[^\n]*", definitions))
    declared = {}
    for i, row in enumerate(rows):
        key = (row[1], int(row[2]))
        if key in declared:
            raise ValueError("subject_definitions repeats a reference definition")
        declared[key] = definitions[row.end():rows[i+1].start() if i+1 < len(rows) else len(definitions)]
        declared[key] = row[0] + declared[key]
    voices = [ref for ref in clip["references"] if ref["role"] == "voice"]
    if len({r["name"] for r in voices}) != len(voices):
        raise ValueError("select one voice reference per canonical speaker")
    wanted = {("Subject", number) for number in expected} | {("Audio", i) for i in range(1, len(voices)+1)}
    if set(declared) != wanted:
        raise ValueError("subject_definitions disagrees with selected references")
    for number, pictures in expected.items():
        actual = {int(n) for n in re.findall(r"<Picture (\d+)>", declared[("Subject", number)])}
        allowed = set(pictures)
        owner = next((name for name, n in subjects.items() if n == number), None)
        if owner:
            allowed.update(p for p, ref in enumerate((r for r in clip["references"] if r["role"] != "voice"), 1)
                           if owner in ref.get("wearers", []))
        if not pictures <= actual or not actual <= allowed:
            raise ValueError("Subject/Picture binding disagrees with selected reference order")
    for kind, count in (("Picture", sum(r["role"] != "voice" for r in clip["references"])),
                        ("Audio", len(voices)), ("Subject", len(expected))):
        actual = {int(n) for n in re.findall(r"<" + kind + r" (\d+)>", clip["prompt_h3"])}
        if actual != set(range(1, count+1)):
            raise ValueError(f"{kind} tags disagree with selected references")
    speakers = list(dict.fromkeys(r["speaker_name"] for r in clip["dialogue_bindings"]))
    for number, ref in enumerate(voices, 1):
        name = ref["name"]
        if name not in speakers:
            raise ValueError("selected voice reference has no authored speaker")
        text = declared[("Audio", number)]
        bound_subjects = {int(n) for n in re.findall(r"<Subject (\d+)>", text)}
        owner = subjects.get(name)
        if bound_subjects != ({owner} if owner else set()) or re.findall(r"\(S(\d+)\)", text) != [str(speakers.index(name)+1)]:
            raise ValueError("Audio reference must bind its canonical speaker and speaker ID")


def _check_prompt(clip):
    clip = visual_request(clip)
    blocks = sections(clip["prompt_h3"])
    if any(not value for value in blocks.values()):
        raise ValueError("all six H3 sections require content; use None. for an empty section")
    _reference_bindings(clip, blocks)
    stages = list(re.finditer(r"\[Shot (\d+)\](.*?)(?=\[Shot \d+\]|\Z)", blocks["detailed_description"], re.S))
    if [int(s[1]) for s in stages] != list(range(1, clip["stage_count"]+1)):
        raise ValueError("H3 Shot headers must preserve the authored stage order and count")
    elapsed = 0.0
    for stage, timing in zip(stages, clip["shot_timing"]):
        durations = re.findall(r"Planned duration: ([\d.]+) seconds\.", stage[2])
        if len(durations) != 1 or not math.isclose(float(durations[0]), timing["seconds"], abs_tol=.001):
            raise ValueError("H3 planned duration disagrees with authored shot timing")
        cuts = re.findall(r"At (\d\d):(\d\d\.\d+),", stage[2])
        if (int(stage[1]) > 1 or cuts) and (len(cuts) != 1 or not math.isclose(int(cuts[0][0])*60+float(cuts[0][1]), elapsed, abs_tol=.001)):
            raise ValueError("H3 cut time disagrees with authored shot timing")
        elapsed += timing["seconds"]
    actual = [speech for stage in stages for speech in SPEECH.finditer(stage[2])]
    speakers = list(dict.fromkeys(r["speaker_name"] for r in clip["dialogue_bindings"]))
    voice_ids = {ref["name"]: number for number, ref in enumerate(
        (r for r in clip["references"] if r["role"] == "voice"), 1)}
    heard = set()
    for speech, row in zip(actual, clip["dialogue_bindings"]):
        name = row["speaker_name"]
        if dialogue_punctuation(speech[4]) != dialogue_punctuation(row["text"]) or int(speech[2]) != speakers.index(name)+1:
            raise ValueError("H3 speech must preserve exact authored words and speaker IDs")
        tail = speech.string[speech.end():].split("<d>", 1)[0].split("[Shot ", 1)[0]
        bindings = re.findall(r"The speaking voice follows the timbre referenced from <Audio (\d+)>\.", tail)
        if bindings or (name in voice_ids and name not in heard):
            if bindings != ([str(voice_ids[name])] if name in voice_ids else []):
                raise ValueError("H3 actual speech must bind the selected voice reference")
        heard.add(name)
    problems = request_issues(clip)
    if problems:
        raise ValueError("; ".join(problems))


def _adopt(draft, *, catalog, source_segments, profile, target_seconds=(120, 150),
           clip_seconds_max=15, format_rows=None):
    if not isinstance(draft, dict) or not isinstance(draft.get("chapter_script"), dict):
        raise ValueError("episode requires an object with chapter_script")
    title = require_text(draft.get("title"), "title", empty=False)
    notes = draft.get("adaptation_notes")
    if isinstance(notes, list):
        for note in notes:
            require_text(note, "adaptation_notes item")
        notes = copy.deepcopy(notes)
    else:
        notes = require_text(notes, "adaptation_notes")
    minimum, maximum = target_seconds
    _seconds(minimum, "target_seconds minimum")
    _seconds(maximum, "target_seconds maximum")
    if minimum > maximum:
        raise ValueError("target_seconds minimum must be at most the maximum")
    _seconds(clip_seconds_max, "clip_seconds_max")
    assets = _catalog(catalog)
    sources = {}
    for row in source_segments:
        if not isinstance(row, dict):
            raise ValueError("source segment requires an object")
        sid = require_text(row.get("segment_id"), "segment_id", empty=False)
        require_text(row.get("text"), "source text")
        if sid in sources:
            raise ValueError("source segments repeat an id")
        sources[sid] = row
    raw_shots, raw_clips = draft["chapter_script"].get("shots"), draft.get("clips")
    if not isinstance(raw_shots, list) or not raw_shots or not isinstance(raw_clips, list) or not raw_clips:
        raise ValueError("episode requires nonempty shots and clips")
    shots = []
    for i, row in enumerate(raw_shots, 1):
        try:
            shots.append(_shot(row, i, sources))
        except ValueError as error:
            if format_rows is None:
                raise
            format_rows.append({'shot_indexes': [i], 'binding_or_timing_issue': str(error)})
    if format_rows:
        # Report independent source-draft problems together, without constructing
        # any usable plan from invalid shots or making the Agent discover them one by one.
        seconds = [c.get('request_seconds') for c in raw_clips if isinstance(c, dict)]
        if len(seconds) == len(raw_clips) and all(type(s) in {int, float} and math.isfinite(s) for s in seconds):
            total = sum(seconds)
            if not minimum <= total <= maximum:
                format_rows.append({'binding_or_timing_issue':
                    f'episode requests {total:g}s; must request {minimum:g}–{maximum:g} seconds in total'})
        return None, None
    clips, coverage = [], []
    for number, raw in enumerate(raw_clips, 1):
        if not isinstance(raw, dict):
            raise ValueError("clip requires an object")
        indexes = raw.get("shot_indexes")
        if not isinstance(indexes, list) or not indexes or any(type(i) is not int or not 1 <= i <= len(shots) for i in indexes):
            raise ValueError("clip shot_indexes requires current integer shot addresses")
        coverage.extend(indexes)
        current = [shots[i-1] for i in indexes]
        if len({s["location"] for s in current}) > 1:
            raise ValueError("each clip requires one location; author a new clip for a location transition")
        seconds = _seconds(raw.get("request_seconds"), "request_seconds")
        if not 3 <= seconds <= clip_seconds_max:
            raise ValueError(f"generated clips must request 3–{clip_seconds_max:g} seconds")
        for shot in current:
            duration = _seconds(shot.get("duration_seconds", seconds if len(current) == 1 else None), "duration_seconds")
            shot["duration_seconds"] = duration
        if not math.isclose(sum(s["duration_seconds"] for s in current), seconds, abs_tol=.001):
            raise ValueError("authored shot durations must sum to clip request_seconds")
        ids = _strings(raw.get("reference_ids"), "reference_ids")
        if any(rid not in assets for rid in ids):
            raise ValueError("clip selects an unknown reference id")
        references, picture, audio = [], 0, 0
        for rid in ids:
            ref = copy.deepcopy(assets[rid])
            if ref["role"] == "voice":
                audio += 1
                ref["tag"] = f"@音频{audio}"
            else:
                picture += 1
                ref["tag"] = f"@图片{picture}"
            ref.setdefault("asset_id", ref["id"])
            if ref["role"] == "prop":
                wearers = list(dict.fromkeys(n for s in current for n, p in s["wears"].items() if p == ref["name"]))
                if wearers:
                    ref["wearers"] = wearers
                if any(ref["name"] in s["independent_props"] for s in current):
                    ref["independent"] = True
            references.append(ref)
        bindings = [{"stage": stage, "source_stage": shot["index"], **copy.deepcopy(turn)}
                    for stage, shot in enumerate(current, 1) for turn in shot["turns"]
                    if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}]
        clip = {"clip_id": f"clip_{number:02d}", "kind": "video", "location": current[0]["location"],
                "shot_indexes": list(indexes), "shot_parts": [{"index": i, "part": [1, 1]} for i in indexes],
                "stage_count": len(current), "segment_ids": list(dict.fromkeys(sid for s in current for sid in s["segment_ids"])),
                "seconds_estimate": seconds, "request_seconds": seconds,
                "cast": list(dict.fromkeys(n for s in current for n in s["characters"])),
                "reference_ids": list(ids), "references": references, "dialogue_bindings": bindings,
                "action_bindings": [{"stage": i, "source_stage": s["index"], "actions": copy.deepcopy(s["actions"])}
                                    for i, s in enumerate(current, 1)],
                "wears_bindings": wears_bindings(current),
                "lines": [{k: copy.deepcopy(row[k]) for k in ("speaker_name", "delivery_mode", "text", "inner_monologue") if k in row} for row in bindings],
                "spoken_text": "".join(r["text"] for r in bindings),
                "chat_lines": [copy.deepcopy(t) for s in current for t in s["turns"] if t["delivery_mode"] == "chat_message"],
                "listeners": list(dict.fromkeys(n for s in current for n in s["listeners"])),
                "extras": list(dict.fromkeys(n for s in current for n in s["extras"])),
                "shot_sound": [s["sfx"] for s in current],
                "shot_timing": [{"seconds": s["duration_seconds"], "cut": s.get("cut", "")} for s in current],
                "prompt": "", "prompt_chars": 0, "prompt_h3": "",
                "render_family": profile.get("render_family", ""), "h3_style_line": profile.get("h3_style_line", "")}
        if all(s.get("scene_id") for s in current):
            clip.update(scene_ids=list(dict.fromkeys(s["scene_id"] for s in current)),
                        shot_ids=[s.get("shot_id", str(s["index"])) for s in current])
        if profile.get("inner_voice_delivery") == "postmix" and any(r.get("inner_monologue") for r in bindings):
            if not all(r.get("inner_monologue") for r in bindings) or len({r["speaker_name"] for r in bindings}) != 1:
                raise ValueError("postmix requires a separate pure inner_monologue clip with one thinker")
            thinker = bindings[0]["speaker_name"]
            voices = [r for r in references if r["role"] == "voice" and r["name"] == thinker]
            cards = [r for r in references if r["role"] == "character" and r["name"] == thinker]
            if not voices or not cards:
                raise ValueError("postmix requires explicitly selected thinker character and voice references")
            clip.update(audio_delivery="postmix", inner_voice={"speaker": thinker, "text": clip["spoken_text"],
                        "voice_references": copy.deepcopy(voices), "character_references": copy.deepcopy(cards)})
            clip["references"] = [r for r in references if r["role"] != "voice"]
        try:
            from .speech_budget import capacity_issue
            if problem := capacity_issue(clip):
                raise ValueError(problem)
            from .requests import compose_request
            if 'h3' in raw and 'prompt_h3' in raw:
                raise ValueError('choose structured h3 or complete prompt_h3, not both')
            clip['prompt_h3'] = compose_request(clip, raw.get('h3', raw.get('prompt_h3')))
            _check_prompt(clip)
        except ValueError as error:
            if format_rows is None:
                raise
            visual = visual_request(clip)
            directions = re.sub(r"<d>.*?</d>", lambda m: "\n" * m[0].count("\n"),
                                clip["prompt_h3"], flags=re.S)
            format_rows.append({"clip_id": clip["clip_id"], "shot_indexes": list(indexes),
                "request_issues": request_issues(visual), "binding_or_timing_issue": str(error),
                "subject_ids": subject_map(visual),
                "dialogue_bindings": visual.get("dialogue_bindings", []),
                "speech_format": ["<Subject n> (Sx) says <d>[Chinese] exact words</d>",
                    "<Subject n> (Sx) says in an off-screen voiceover <d>[Chinese] exact words</d>",
                    "An off-screen voice (Sx) says <d>[Chinese] exact words</d>"],
                "reference_order": [{k: r.get(k) for k in ("tag", "role", "name")}
                                    for r in visual.get("references", [])],
                "cjk_excerpts": [line.strip() for line in directions.splitlines() if CJK.search(line)],
                "negative_excerpts": [line.strip() for line in directions.splitlines() if NEGATIVE_DIRECTION.search(line)]})
        else:
            if format_rows is None:
                clip["prompt_h3_of"] = h3_stamp(clip)
        clips.append(clip)
    if coverage != list(range(1, len(shots)+1)):
        raise ValueError("clips must cover each authored shot exactly once in authored shot order")
    total = sum(c["request_seconds"] for c in clips)
    if not minimum <= total <= maximum:
        if format_rows is None:
            raise ValueError(f"episode must request {minimum:g}–{maximum:g} seconds in total")
        format_rows.append({'binding_or_timing_issue':
            f'episode requests {total:g}s; must request {minimum:g}–{maximum:g} seconds in total'})
    script = copy.deepcopy(draft["chapter_script"])
    script.update(video_title=title, adaptation_notes=notes, profile=copy.deepcopy(profile), shots=shots, clip_count=len(clips))
    plan = {"policy": POLICY, "phases": [], "limits": {"max_clip_seconds": clip_seconds_max},
            "totals": {"clip_count": len(clips), "video_clip_count": len(clips), "title_card_count": 0,
                       "shot_count": len(shots), "estimated_seconds": total, "requested_seconds": total,
                       "profile": copy.deepcopy(profile)}, "clips": clips}
    return script, plan


def adopt(draft, *, catalog, source_segments, profile, target_seconds=(120, 150), clip_seconds_max=15):
    """Return caller-independent chapter_script and clip_plan after every check passes."""
    return _adopt(draft, catalog=catalog, source_segments=source_segments, profile=profile,
                  target_seconds=target_seconds, clip_seconds_max=clip_seconds_max)


def collect_format_issues(draft, *, catalog, source_segments, profile, target_seconds=(120, 150), clip_seconds_max=15):
    """Inspect every request in memory; return errors only, never an unvalidated plan."""
    rows = []
    _adopt(draft, catalog=catalog, source_segments=source_segments, profile=profile,
           target_seconds=target_seconds, clip_seconds_max=clip_seconds_max, format_rows=rows)
    return rows


def validate_existing_request(clip, prompt):
    """Bind a new final request to the prepared plan, without rewriting that plan."""
    from novel_manga.application.repair.agent import sections
    from novel_manga.story.dialogue import dialogue_punctuation, subject_map
    from novel_manga.story.h3 import request_issues
    from novel_manga.story.voice_delivery import visual_request
    visual = visual_request({**clip, 'prompt_h3': prompt})
    if 'dialogue_bindings' not in visual:
        raise ValueError('prepared clip requires dialogue_bindings')
    blocks = sections(prompt)
    if any(re.search(r'</?d>', text) for name, text in blocks.items() if name != 'detailed_description'):
        raise ValueError('final request speech belongs only in detailed_description shot bodies')
    _reference_bindings(visual, blocks)
    problems = request_issues(visual)
    if problems:
        raise ValueError('; '.join(problems))
    stages = re.findall(r'\[Shot (\d+)\]', blocks['detailed_description'])
    count = int(clip.get('stage_count') or len(clip.get('shot_indexes', [])))
    if stages != [str(i) for i in range(1, count + 1)]:
        raise ValueError('final request must preserve the prepared shot order and count')
    subjects = subject_map(visual)
    bindings = visual['dialogue_bindings']
    speech = list(SPEECH.finditer(blocks['detailed_description']))
    if len(speech) != len(bindings):
        raise ValueError('final request must preserve every prepared dialogue once')
    speakers = list(dict.fromkeys(row['speaker_name'] for row in bindings))
    voices = {ref['name']: i for i, ref in enumerate(
        (ref for ref in visual['references'] if ref.get('role') == 'voice'), 1)}
    heard = set()
    for number, (said, row) in enumerate(zip(speech, bindings)):
        owner = row['speaker_name']
        subject = int(said[1]) if said[1] else None
        if (dialogue_punctuation(said[4]) != dialogue_punctuation(row['text']) or int(said[2]) != speakers.index(owner) + 1
                or subject != subjects.get(owner)
                or speech_is_offscreen(said) != (row['delivery_mode'] == 'offscreen_dialogue' or owner not in subjects)):
            raise ValueError('final request changed protected dialogue or speaker binding')
        next_speech = speech[number + 1].start() if number + 1 < len(speech) else len(said.string)
        tail = said.string[said.end():next_speech].split('[Shot ', 1)[0]
        audio = re.findall(r'The speaking voice follows(?: the timbre referenced from)?\s+<Audio (\d+)>\.', tail)
        if audio or (owner in voices and owner not in heard):
            if audio != ([str(voices[owner])] if owner in voices else []):
                raise ValueError('final request changed a prepared speaker audio reference')
        heard.add(owner)
    timing = clip.get('shot_timing') or []
    if timing:
        elapsed = 0.0
        bodies = re.split(r'\[Shot \d+\]', blocks['detailed_description'])[1:]
        for i, (body, row) in enumerate(zip(bodies, timing), 1):
            cuts = re.findall(r'At (\d\d):(\d\d\.\d+),', body)
            if i > 1 and (len(cuts) != 1 or abs(int(cuts[0][0]) * 60 + float(cuts[0][1]) - elapsed) > .001):
                raise ValueError('final request changed a prepared shot cut time')
            duration = re.findall(r'Planned duration: ([\d.]+) seconds\.', body)
            if duration and (len(duration) != 1 or abs(float(duration[0]) - row['seconds']) > .001):
                raise ValueError('final request changed a prepared shot duration')
            elapsed += float(row['seconds'])


def validate_patch(old_clip, new_prompt):
    """Validate an English-only repair while retaining the existing clip identity."""
    require_text(new_prompt, "prompt_h3", empty=False)
    validate_existing_request(old_clip, new_prompt)
    # Input recompilation can invalidate the English without changing the
    # adopted plan. With no prior English, that plan is the binding authority.
    if old_clip.get('prompt_h3'):
        validate_prompt(old_clip, new_prompt,
                        dialogue_bindings=visual_request(old_clip)['dialogue_bindings'])
