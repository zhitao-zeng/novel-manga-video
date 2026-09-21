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
from novel_manga.application.profiles import h3_prompt_outdated, h3_source_digest, h3_compile_inputs
from novel_manga.application.production.runs import corrections

from novel_manga.util import atomic_write_json  # noqa: E402
from novel_manga.story.h3 import request_issues, stages_of, subject_lines, compose, tag_names, clean_note, CJK, CHARACTER_TRAIT

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots"],
          "properties": {"shots": {"type": "array", "items": {"type": "string"}}}}
ASK = ("Translate each numbered Chinese shot description into ONE English sentence that states only what the "
       "camera sees: framing and angle, where the characters are and what they do, the light, the setting. "
       "Never include spoken dialogue, and never invent any.\n"
       "Preserve explicit counts of independent bodies and anatomical heads separately. The classifier in 三头X "
       "counts three individual animals; it does NOT also give each animal three heads. Only say multi-headed when "
       "the description explicitly supports multiple heads on one body, such as 一个身体、三颗头 or a fusion retaining "
       "three heads. An explicit statement of independent individuals takes precedence over an ambiguous classifier. "
       "Never add anatomical head counts, or merge independently acting subjects, from a species name alone.\n"
       "Refer to each character by the tag given below, never by name and never by a translated name, "
       "so the sentence points at the same reference picture the tag does.\n"
       "Return one sentence per input shot, in order.\n\n")
TRIES = 3  # translations per clip before it is left without an English prompt
NOTE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["note"], "properties": {"note": {"type": "string"}}}
NOTE_ASK = ("Rewrite this director's correction for one video clip as a direct instruction to the video generator: "
            "imperative mood, present tense, describing only what the correct picture shows - who is in frame, who does what "
            "to whom, who appears exactly once, who is absent. Never describe the mistake, the previous take or what "
            "'the director notes'. Refer to each character by the tag given below, never by name. Never quote dialogue. "
            "Reply with the instruction only - no commentary and no remarks about the tag list; a character without a tag is left out.\n\n")








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
                 "sentence too, so the answer has exactly as many sentences as there are numbered lines.\n")

DELIVERY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["manners"],
                   "properties": {"manners": {"type": "array", "items": {"type": "string"}}}}
DELIVERY_ASK = ("Each numbered Chinese phrase says how one spoken line is delivered - the voice, not the face.\n"
                "Rewrite each as ONE short English clause beginning 'The line is delivered', for example "
                "'The line is delivered in a low, hesitant voice.' or 'The line is delivered as a furious shout.'\n"
                "Describe only the voice: loudness, pitch, pace, steadiness, and whether it breaks or trails off. "
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
    return {zh: en for zh, en in zip(wanted, got) if en and not CJK.search(en)}


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
    digest = h3_source_digest(prompt, note, clip.get('crowd_roles'), h3_compile_inputs(clip))
    if clip.get("prompt_h3_of") == digest and clip.get("prompt_h3") and not request_issues(clip):
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
    picture=0
    for ref in clip.get('references',[]):
        if ref.get('role') in {'character','location'}:
            picture+=1
        crowd=clip.get('crowd_roles',{}).get(ref.get('name'))
        if crowd and ref.get('role')=='character':
            naming+=f"{ref['name']} = the {crowd['count'] or 'several'} distinct unnamed supporting people wearing the clothing from <Picture {picture}>\n"
    visuals = [tag_names(visual, naming) for visual, _ in stages]
    directed = bool(clip.get('scene_ids'))
    if directed:
        # Unlike spoken lines, authored physical sound cues must survive the
        # SOUND removal above and reach H3 in English, in their own shot.
        if len(clip.get('shot_sound', [])) != len(stages):
            warn(clip, 'FAILED: authored sound does not match shot count')
            return False
        visuals = [visual + (' 同期声：' + tag_names(clip['shot_sound'][i], naming) if clip['shot_sound'][i] else '')
                   for i, visual in enumerate(visuals)]
    tagged = tag_names(note, naming) if note else ""

    def ask_lines(lines: list[str], extra: str) -> list[str]:
        feedback = ('\nThe previous output failed: ' + problem +
                    '. Use only the declared Subject tags; groups described without a Subject tag stay distinct unnamed people.\n') if problem else ''
        ask = ASK
        if directed:
            ask = ask.replace('states only what the camera sees:', 'states what the camera sees and the physical sounds it hears:')
            ask += ('Preserve each shot\'s physical sound sources, their onset, fading or stopping. '
                    'Preserve explicitly scripted breath, crying, animal calls or wordless humming. '
                    'Use only English outside the separately bound spoken dialogue; add no new spoken content.\n')
        question = [{"type": "text", "text": ask + extra + feedback + naming + "\n" + "\n".join(f"{i}. {text}" for i, text in enumerate(lines, 1))}]
        answer = ask_json(question, SCHEMA, name="h3prompt", max_tokens=200 + 220 * len(lines), settings=h3_translation_endpoint())
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
                        candidate = compose(clip, english[:-1], stages, direction, delivery)
                        conflicts = request_issues({**clip, 'prompt_h3':candidate})
                        if not conflicts:
                            clip['prompt_h3'] = candidate
                            clip['prompt_h3_of'] = digest
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
            candidate = compose(clip, english, stages, '', delivery)
            conflicts = request_issues({**clip, 'prompt_h3':candidate})
            if not conflicts:
                clip['prompt_h3'] = candidate
                clip['prompt_h3_of'] = digest
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
        episodes = [d for d in episodes if d.name.rsplit("_", 1)[-1].isdigit() and int(d.name.rsplit("_", 1)[-1]) in wanted]
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
        due = [clip for clip in video if args.rebuild or h3_prompt_outdated(clip, notes.get(clip["clip_id"], ""))]
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
                if new and new["prompt_h3_of"] == h3_source_digest(clip.get("prompt") or "", now.get(clip.get("clip_id"), ""), clip.get('crowd_roles'), h3_compile_inputs(clip)):
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
