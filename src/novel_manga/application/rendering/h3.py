#!/usr/bin/env python
"""Write each clip a second prompt, in the shape MiniMax H3 was trained to read.

H3 decides what to speak by language.  Every example it was trained on puts the description in
English and the spoken words in <d>[Chinese] ...</d>, so Chinese text means "say this".  Our
prompt is Chinese from top to bottom, which marks two thousand characters of stage direction
as dialogue - and that is exactly what came back: the model recited 决定以灰头鹰身份保护公主
and dropped the line it was given.

Measured on three clips, rewriting the description into English and leaving only the lines in
Chinese took CER from 5.000 to 0.000, 3.778 to 0.778, and 4.750 to 1.000.

The translation is one local Qwen call per clip, so this is free and runs beside planning.
It writes prompt_h3 into the plan next to prompt, keyed by a digest of the Chinese one: re-pack
a chapter and its English prompt is rebuilt rather than silently kept.

    build_h3_prompts.py <novel> [--novel-dir DIR] [--chapters 1-50,60] [--workers N] [--limit N] [--rebuild]

A local-H3 lane (thin_batch.py) runs this for one episode right before rendering it whenever a clip has
no current English prompt - a new plan, a re-pack - so nothing waits for a pass run by hand.  A
translation that fails, or comes back with a different number of sentences than the clip has shots,
is asked again; after three tries the clip is left without one and the lane waits for it, rather than
render a shot under another shot's description.
"""
import novel_manga.episodes as ep_names
from novel_manga.application.configuration import project_root
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = project_root()
from novel_manga.application.configuration import h3_translation_endpoint
from novel_manga.llm.client import ask_json
from novel_manga.application.profiles import h3_compile_inputs, h3_prompt_outdated, h3_source_digest, h3_stamp
from novel_manga.application.production.runs import corrections

from novel_manga.util import atomic_write_json  # noqa: E402
from novel_manga.story.h3 import request_issues, stages_of, subject_lines, asset_subjects, compose, tag_names, clean_note, CJK, CHARACTER_TRAIT

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots", "soundscape"],
          "properties": {"shots": {"type": "array", "items": {"type": "string"}},
                         "soundscape": {"type": "string"}}}
ASK = ("Translate each numbered Chinese shot description into concrete English shot directions that state what the "
       "camera sees and, when explicitly listed, the physical sounds it hears: framing and angle, where the "
       "characters are and what they do, the light, the setting. "
       "Keep the start, action and end in their original order. Preserve whether someone enters or exits, "
       "and whether an object is worn, empty, or separate from its wearer; do not put one person both inside "
       "and outside a suit. Do not repeat an action already completed in the preceding shot. "
       "Use more than one sentence within a shot when one sentence would reverse or omit an action. "
       "Never include spoken dialogue, and never invent any.\n"
       "The structured action list and event prose describe the SAME event, not successive repetitions; "
       "state each physical action once. Never add 'again' or a second copy merely because it is described twice. "
       "Translate only observable behavior; psychological explanations do not introduce a visible person or object.\n"
       "A lighting cue may refer to reflections on an off-screen person's clothing or equipment. Preserve only "
       "the light's source, direction and color; omit the off-screen reflecting object. Never bring that object "
       "into frame, put it in the background, or dress the visible person in it. Clothing follows that person's "
       "own reference, except for an explicitly bound wearable.\n"
       "Preserve explicit counts of independent bodies and anatomical heads separately. The classifier in 三头X "
       "counts three individual animals; it does NOT also give each animal three heads. Only say multi-headed when "
       "the description explicitly supports multiple heads on one body, such as 一个身体、三颗头 or a fusion retaining "
       "three heads. An explicit statement of independent individuals takes precedence over an ambiguous classifier. "
       "Never add anatomical head counts, or merge independently acting subjects, from a species name alone.\n"
       "Refer to each referenced character, environment, garment and prop by the tag given below, never by name or a translated name. "
       "Show a wearable as its wearer Subject wearing the garment Subject; neither the garment label nor its alias introduces another body. "
       "so the sentence points at the same reference picture the tag does. A person without a declared tag "
       "must not be named, shown or given a voice; if the shot addresses them outside the frame, call them only "
       "an unseen off-screen listener.\n"
       "Return one array item per input shot, in order. Also return soundscape: one short positive English "
       "description of the actual ambience and physical sound sources in this scene. It contains no dialogue, "
       "no instructions addressed to the generator and no prohibitions.\n\n")
TRIES = 3  # translations per clip before it is left without an English prompt
NOTE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["note"], "properties": {"note": {"type": "string"}}}
NOTE_ASK = ("Rewrite this director's correction for one video clip as a description of the target picture: "
            "a declarative, present-tense description of the corrected picture, not an imperative addressed to the generator: who is in frame, who does what "
            "to whom, who appears exactly once, who is absent. Never describe the mistake, the previous take or what "
            "'the director notes'. Refer to each character by the tag given below, never by name. Never quote dialogue. "
            "Reply with the picture description only - no commentary and no remarks about the tag list; a character without a tag is left out.\n\n")








def warn(clip: dict, message: str) -> None:
    print(f"  {clip.get('clip_id', '?')}: H3 prompt {message}", file=sys.stderr, flush=True)








def english_note(note: str, naming: str) -> str:
    """The clip's director correction in English, for the prompt H3 reads - in Chinese it was read out as dialogue.
    '' when the translation fails or still carries Chinese.  The names are swapped for their tags before the ask,
    so the model has nothing left to map and nothing to remark on."""
    try:
        answer = ask_json([{"type": "text", "text": NOTE_ASK + naming + "\n" + tag_names(note, naming)}], NOTE_SCHEMA, name="h3note", max_tokens=400, settings=h3_translation_endpoint())
    except Exception:  # noqa: BLE001 - convert asks again
        return ""
    return clean_note(str(answer.get("note") or "").strip(), naming)


NOTE_LINE_ASK = ("The last numbered line is the director's correction for this clip, not a shot: translate it as its own "
                 "sentence too, so the answer has exactly as many sentences as there are numbered lines. "
                 "Write that last sentence as a declarative description of the target picture, not an instruction to the generator. "
                 "Apply that correction to every affected shot you translate above it. It overrides conflicting original "
                 "camera, framing (including end-state framing), costume and background descriptions: replace obsolete directions rather than keeping "
                 "both versions. Preserve the original dialogue, action order and story facts.\n")

DELIVERY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["manners"],
                   "properties": {"manners": {"type": "array", "items": {"type": "string"}}}}
DELIVERY_ASK = ("Each numbered Chinese phrase says how one spoken line is delivered - the voice, not the face.\n"
                "Rewrite each as ONE short English clause beginning 'The line is delivered', for example "
                "'The line is delivered in a low, hesitant voice.' or 'The line is delivered as a furious shout.'\n"
                "Describe only the voice: loudness, pitch, pace, steadiness, and whether it breaks or trails off. "
                "Keep a complaint irritated or weary rather than turning it into playful teasing. "
                "Never add crying, sobbing, laughter, screaming or a broken voice unless the Chinese phrase explicitly "
                "names that vocal behavior; emotional words such as 崩溃 and 难过 alone do not. "
                "Never describe the face, the body, the setting or the words themselves, and never add anything spoken.\n"
                "Return one clause per input phrase, in order, in English only.\n\n")
# The planner writes this when the storyboard left the emotion blank, so it says nothing about the voice.
DEFAULT_EMOTION = "平静"


def english_delivery(manners) -> dict:
    """Chinese vocal manner -> the English clause that states it; {} when the model cannot be asked.

    Deliberately its own call rather than more numbered lines on the shot translation.  That ask is for what
    the camera SEES, and it duly turned 恐惧与无奈 into "with a determined expression" - a face, with the
    valence reversed, and nothing for the voice H3 generates.  Failing here costs a line its performance,
    never the clip its prompt, so it returns empty instead of raising.
    """
    wanted = [m for m in dict.fromkeys(manners) if m and m != DEFAULT_EMOTION]
    if not wanted:
        return {}
    try:
        answer = ask_json([{"type": "text", "text": DELIVERY_ASK + "\n".join(f"{i}. {m}" for i, m in enumerate(wanted, 1))}],
                          DELIVERY_SCHEMA, name="h3delivery", max_tokens=60 * len(wanted) + 200,
                          settings=h3_translation_endpoint())
    except Exception:  # noqa: BLE001 - the words are still spoken, just without the manner
        return {}
    got = [str(s).strip() for s in (answer.get("manners") or [])]
    if len(got) != len(wanted):
        return {}
    return {zh: en for zh, en in zip(wanted, got)
            if en and not CJK.search(en) and delivery_acceptable(zh, en)}


# Vocal behaviours the ASK forbids unless the Chinese phrase itself names them: a 崩溃 once became
# "a voice that breaks", the constraint was in the prompt and the answer sailed through anyway
# (four-layer audit #6) - so the check lives here, not only in the ask.
_NAMED_VOCAL = ("哭", "泣", "呜咽", "喊", "吼", "叫", "笑", "破音", "破嗓", "哽咽", "嘶哑", "沙哑", "颤抖", "发抖")
_ADDED_VOCAL = re.compile(r"\b(cry|crying|sob|sobbing|scream|screaming|shout|breaks?|broken|laugh|laughter|choke[ds]?|sobbing)\b", re.I)
# The face and the body are the camera's business (the shot translation covers them); the manner
# clause is for the voice H3 generates, and 恐惧与无奈 once came back as "with a determined expression".
_BODY_CREEP = re.compile(r"\b(face|facial|expression|eyes?|brow|mouth|lips?|jaw|hands?|fists?|shoulders?|body|posture)\b", re.I)


def delivery_acceptable(chinese: str, english: str) -> bool:
    """A manner clause is kept only when it invents nothing: no unnamed vocal behaviour, no body."""
    if _ADDED_VOCAL.search(english) and not any(word in chinese for word in _NAMED_VOCAL):
        return False
    if _BODY_CREEP.search(english):
        return False
    return True


def convert(clip: dict, tries: int = TRIES, note: str = "") -> bool:
    """Write clip["prompt_h3"]; True when written.

    A translation that fails, or comes back with a different number of sentences than the clip has
    shots, is asked again.  After `tries` the clip is left without an English prompt - an H3 lane then
    waits for it - instead of being padded out: that attached descriptions to the wrong shots, and
    filled the gap with the Chinese text, which H3 reads aloud.

    A director's correction rides along as one more numbered line (asked on its own, this model commented on
    the tag list instead of translating).  A single-stage clip often comes back with the correction folded into
    the shot - one sentence for two lines - so after that the correction is merged into every shot's text and
    translated as part of it: the count always matches and the instruction still reaches the picture
    (雾月 2026-09-13: 190 episodes looped on the folded answer)."""
    prompt = clip.get("prompt") or ""
    note = str(note or "").strip()

    def merge_correction_into_prompt() -> None:
        """The correction stops being a parallel truth (audit #5): once its English is in, the
        Chinese prompt carries it too, and the note is spent - the plan is the one source, the
        cache key moves with it, and no stage of the pipeline keeps quoting an old picture."""
        if not note or clip.get("prompt_correction_merged"):
            return
        merge = f"\n【导演修正】{note}"
        clip.setdefault("prompt_before_correction", prompt)
        clip["prompt"] = prompt + merge
        clip["prompt_correction_merged"] = True

    # The stamp this conversion writes is versioned ("2:<digest>") and covers the reference
    # seating; a bare stamp from before is compared under the old formula instead.
    from novel_manga.application.profiles import h3_stamp
    stamp = h3_stamp(clip, note)
    if clip.get("prompt_h3_of") == stamp and clip.get("prompt_h3") and not request_issues(clip):
        return False
    stages = stages_of(prompt)
    if not stages:
        warn(clip, "FAILED: the Chinese prompt has no 【阶段】 block to translate")
        return False
    _, subject_of = subject_lines(clip)
    # The name alone does not say what the character IS.  In a book where most of the cast are
    # dragons, a translator given only "奥尔德 = <Subject 2>" has to guess, and it guessed wrong:
    # a human was written with claws, a tail and dragon scales.  The Chinese prompt already carries
    # each character's 辨识特征 in its 【人物】 block, which the 【阶段】 slice never passed along.
    traits = dict(CHARACTER_TRAIT.findall(prompt))
    naming = "".join(
        f"{name} = <Subject {n}>" + (f" ({traits[name]})" if traits.get(name) else "") + "\n"
        for name, n in subject_of.items())
    for n, _, ref in asset_subjects(clip):
        if ref.get('role') in {'location', 'prop'} and ref.get('name') not in subject_of:
            naming += f"{ref['name']} = <Subject {n}>\n"
            naming += ''.join(f'{alias} = <Subject {n}>\n' for alias in ref.get('aliases', []) if alias not in subject_of)
    picture=0
    for ref in clip.get('references',[]):
        if ref.get('role') == 'prop' and ref.get('wearers'):
            wearer_tags = [f'<Subject {subject_of[n]}>' for n in ref['wearers'] if n in subject_of]
            if wearer_tags:
                naming += (f"Wearable {ref['name']}: a garment/armor worn by {', '.join(wearer_tags)}. "
                           "A standing or moving description of this worn garment refers to its wearer, not an empty suit. "
                           "Distinguish a separate explicitly unoccupied copy only when the shot calls for one.\n")
        if ref.get('role') in {'character','location'}:
            picture+=1
        crowd=clip.get('crowd_roles',{}).get(ref.get('name'))
        if crowd and ref.get('role')=='character':
            naming+=f"{ref['name']} = the {crowd['count'] or 'several'} distinct unnamed supporting people wearing the clothing from <Picture {picture}>\n"
    visuals = [tag_names(visual, naming) for visual, _ in stages]
    directed = bool(clip.get('scene_ids'))
    has_sound = 'shot_sound' in clip
    if has_sound:
        # Unlike spoken lines, authored physical sound cues must survive the
        # SOUND removal above and reach H3 in English, in their own shot.
        if len(clip.get('shot_sound', [])) != len(stages):
            warn(clip, 'FAILED: authored sound does not match shot count')
            return False
        visuals = [visual + (' 同期声：' + tag_names(clip['shot_sound'][i], naming) if clip['shot_sound'][i] else '')
                   for i, visual in enumerate(visuals)]
    tagged = tag_names(note, naming) if note else ""

    soundscape = ''
    def ask_lines(lines: list[str], extra: str) -> list[str]:
        nonlocal soundscape
        feedback = ('\nThe previous output failed: ' + problem +
                    '. Use only the declared Subject tags; groups described without a Subject tag stay distinct unnamed people.\n') if problem else ''
        ask = ASK
        if has_sound:
            ask += ('Preserve each shot\'s physical sound sources, their onset, fading or stopping. '
                    'Preserve explicitly scripted breath, crying, animal calls or wordless humming. '
                    'Use only English outside the separately bound spoken dialogue; add no new spoken content.\n')
        question = [{"type": "text", "text": ask + extra + feedback + naming + "\n" + "\n".join(f"{i}. {text}" for i, text in enumerate(lines, 1))}]
        answer = ask_json(question, SCHEMA, name="h3prompt", max_tokens=400 + 350 * len(lines), settings=h3_translation_endpoint())
        soundscape = str(answer.get('soundscape') or '').strip()
        if CJK.search(soundscape):
            raise ValueError('soundscape must be in English')
        return [str(s).strip() for s in (answer.get("shots") or [])]

    manners = ([str(r.get('emotion') or '') for r in clip['dialogue_bindings']] if 'dialogue_bindings' in clip
               else [turn[3] for _, turns in stages for turn in turns if len(turn) > 3])
    delivery = english_delivery(manners)
    problem = ""
    folded = False
    for _ in range(tries):
        try:
            if note and not folded:
                english = ask_lines(visuals + [tagged], NOTE_LINE_ASK)
                if len(english) == len(visuals) + 1 and all(english):
                    direction = clean_note(english[-1], naming)
                    if direction:
                        candidate = compose(clip, english[:-1], stages, direction, delivery, soundscape)
                        conflicts = request_issues({**clip, 'prompt_h3':candidate})
                        if not conflicts:
                            clip['prompt_h3'] = candidate
                            clip['prompt_h3_of'] = stamp
                            merge_correction_into_prompt()
                            return True
                        problem = '; '.join(conflicts)
                        continue
                    problem = "the director's correction did not come back in English"
                else:
                    problem = f"{len(english)} sentence(s) back for {len(visuals) + 1} lines"
                folded = len(english) == len(visuals) or problem.startswith("the director")
                continue
            lines = [f"{visual}（导演修正：{tagged}）" for visual in visuals] if note else visuals
            english = ask_lines(lines, "")
        except Exception as error:  # noqa: BLE001 - asked again, and reported if it keeps failing
            problem = f"{type(error).__name__}: {str(error)[:160]}"
            continue
        if len(english) == len(visuals) and all(english) and not (directed and any(CJK.search(s) for s in english)):
            candidate = compose(clip, english, stages, '', delivery, soundscape)
            conflicts = request_issues({**clip, 'prompt_h3':candidate})
            if not conflicts:
                clip['prompt_h3'] = candidate
                clip['prompt_h3_of'] = stamp
                merge_correction_into_prompt()
                return True
            problem = '; '.join(conflicts)
            continue
        problem = f"{len(english)} sentence(s) back for {len(visuals)} lines"
    warn(clip, f"FAILED after {tries} tries: {problem}")
    return False


def episode_numbers(spec: str) -> set[int]:
    numbers: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if "-" in part:
            start, end = part.split("-", 1)
            numbers.update(range(int(start), int(end) + 1))
        elif part:
            numbers.add(int(part))
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("novel")
    parser.add_argument("--novel-dir", type=Path, help="the novel's output directory (default outputs/<novel>)")
    parser.add_argument("--chapters", help='only these episodes, e.g. "1597" or "1-50,60"')
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir or ROOT / "outputs" / args.novel
    episodes = sorted(d for d in novel_dir.glob(f"{args.novel}_*") if (d / "clip_plan.json").is_file())
    if args.chapters:
        wanted = episode_numbers(args.chapters)
        episodes = [d for d in episodes if ep_names.is_episode(d.name) and ep_names.chapter_of(d.name) in wanted]
    if args.limit:
        episodes = episodes[: args.limit]
    print(f"{args.novel}: {len(episodes)} 集，{args.workers} 路并行", flush=True)

    def one(episode: Path) -> tuple[int, int, int]:
        path = episode / "clip_plan.json"
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0, 0, 0
        from novel_manga.application.preparation.readiness import inspect_episode
        _, blocked = inspect_episode(episode)
        video = [c for c in plan["clips"] if c.get("kind") == "video" and c["clip_id"] not in blocked]
        notes = {key: str(value) for key, value in corrections(episode).items()}  # director corrections, per clip
        # Every clip whose English prompt is due - all of them with --rebuild - and does not get one is a failure:
        # an older English prompt left in place is not a rebuild (it used to pass as one, exit code 0).
        # Strict: conversion itself requires the versioned stamp, so a pre-v2 or unstamped English
        # prompt is recompiled once here - the seating becomes provable, the videos stay untouched.
        due = [clip for clip in video if args.rebuild or h3_prompt_outdated(clip, notes.get(clip["clip_id"], ""), strict=True)]
        if args.rebuild:
            for clip in due:
                clip.pop("prompt_h3_of", None)
        made = {clip["clip_id"]: clip for clip in due if convert(clip, note=notes.get(clip["clip_id"], ""))}
        if made:
            # The translations take minutes: write them into the plan as it is now, and only onto clips
            # whose Chinese prompt is still the one they were made from.
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = plan
            now = {key: str(value) for key, value in corrections(episode).items()}
            for clip in current.get("clips", []):
                new = made.get(clip.get("clip_id"))
                if new and new["prompt_h3_of"] == h3_stamp(clip, now.get(clip.get("clip_id"), "")):
                    clip["prompt_h3"], clip["prompt_h3_of"] = new["prompt_h3"], new["prompt_h3_of"]
            atomic_write_json(path, current)
            plan = current
        return len(made), len(video), len(due) - len(made)

    done = total = missing = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, (changed, clips, left) in enumerate(pool.map(one, episodes), 1):
            done += changed
            total += clips
            missing += left
            if n % 100 == 0:
                print(f"  {n}/{len(episodes)} 集，已转 {done} 段", flush=True)
    print(f"\n转好 {done} 段（共 {total} 段视频片段）" + (f"；{missing} 段没转成" if missing else ""))
    return 1 if missing else 0
