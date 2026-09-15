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
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
os.environ.setdefault("QWEN38_LOCAL_BASE_URL", ",".join(f"http://127.0.0.1:{p}/v1" for p in range(18120, 18125)))
os.environ.setdefault("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project")
os.environ.setdefault("QWEN38_LOCAL_API_KEY_VAR", "H3_PROMPT_NO_KEY")
from thin_review import ask_json  # noqa: E402
from thin_profile import h3_prompt_outdated, h3_source_digest  # noqa: E402
from thin_runs import corrections  # noqa: E402

from novel_manga.util import atomic_write_json  # noqa: E402
from h3_request_checks import request_issues

STAGE = re.compile(r"【阶段[^】]*】(.*?)(?=【阶段|画面呈现|$)", re.S)
SOUND = re.compile(r"声音：.*?(?=结束时：|$)", re.S)
TURN = re.compile(r"中文普通话，([^，]*)，(.*?)(?:开口说|说)：\{([^}]*)\}(，画面中无人开口)?")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots"],
          "properties": {"shots": {"type": "array", "items": {"type": "string"}}}}
ASK = ("Translate each numbered Chinese shot description into ONE English sentence that states only what the "
       "camera sees: framing and angle, where the characters are and what they do, the light, the setting. "
       "Never include spoken dialogue, and never invent any.\n"
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
CJK = re.compile(r"[\u4e00-\u9fff]")


def stages_of(prompt: str) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """Each stage as (visual Chinese text, [(speaker, line, offscreen)])."""
    out = []
    for block in STAGE.findall(prompt):
        turns = [(who.strip(), text.strip(), bool(off)) for _, who, text, off in TURN.findall(block)]
        visual = re.sub(r"\s+", " ", SOUND.sub("", block)).strip(" 。")
        out.append((visual, turns))
    return out


def subject_lines(clip: dict) -> tuple[list[str], dict]:
    """The subject_definitions block, and the name -> <Subject N> map the shots will use."""
    defs, subject_of, picture = [], {}, 0
    for ref in (clip.get("references") or []):
        if ref.get("role") == "character":
            picture += 1
            crowd=clip.get('crowd_roles',{}).get(ref['name'])
            if crowd:
                defs.append(f"<Picture {picture}> provides shared clothing only for {crowd['count']} distinct unnamed supporting people. "
                            'Their faces and hairstyles must be different from each other and must not copy the face in this picture. '
                            'For a pair, one has a narrow face and the other a broad face. This is a clothing reference, not one repeated identity.')
                continue
            subject_of[ref["name"]] = picture
            # One instance, and nobody else wears this face: 雾月's most common defect (321 clips on 2026-09-14) was
            # the lead's face or coat on a second person, and the Chinese binding's "只出现一次" never reached H3.
            defs.append(f"<Subject {picture}> is the character {ref['name']}, shown in <Picture {picture}>. "
                        f"Take only the face, hair, build and clothing from <Picture {picture}>. Exactly one "
                        f"<Subject {picture}> appears in the video; no other person has <Subject {picture}>'s face, hair or clothes.")
        elif ref.get("role") == "location":
            picture += 1
            defs.append(f"<Picture {picture}> is the setting {ref['name']}: take its architecture, ground, "
                        f"fixed props and light from it, and none of the people in it.")
    voice = 0
    for ref in (clip.get("references") or []):
        if ref.get("role") == "voice":
            voice += 1
            if ref["name"] in subject_of:
                defs.append(f"<Audio {voice}> is the voice-timbre reference for <Subject {subject_of[ref['name']]}>.")
    return defs, subject_of


def compose(clip: dict, english: list[str], stages: list, note: str = "") -> str:
    """The six sections MiniMax's own guide prescribes (skills/h3-prompt-writing/references/ref-en.txt), and only
    those: a `director_note` section is not in the format, so a correction goes into the summary.  Speakers carry
    stable (Sx) ids next to their subject tag; an off-screen line uses the guide's exact phrase and is followed by
    the statement that the on-screen characters' lips stay closed (the "wrong mouth moves" defect)."""
    defs, subject_of = subject_lines(clip)
    speaker_ids: dict = {}

    def sid(key: str) -> str:
        if key not in speaker_ids:
            speaker_ids[key] = f"(S{len(speaker_ids) + 1})"
        return speaker_ids[key]

    body = []
    for index, ((_, turns), text) in enumerate(zip(stages, english), 1):
        body.append(f"[Shot {index}] {text}")
        for who, line, offscreen in turns:
            if who in subject_of and not offscreen:
                body.append(f"<Subject {subject_of[who]}> {sid(who)} says <d>[Chinese] {line}</d>")
            elif who in subject_of:
                body.append(f"<Subject {subject_of[who]}> {sid(who)} says in an off-screen voiceover <d>[Chinese] {line}</d> "
                            "The on-screen characters' lips remain closed.")
            else:
                body.append(f"An off-screen voice {sid(who or 'off-screen')} says in an off-screen voiceover <d>[Chinese] {line}</d> "
                            "The on-screen characters' lips remain closed.")
    seconds = clip.get("request_seconds")
    retention = []
    for ref in (clip.get("references") or []):
        if ref.get("role") == "character" and ref["name"] in subject_of:
            n = subject_of[ref["name"]]
            retention.append(f"<Subject {n}>: fully_preserved - the identity, face, hair and clothing of <Picture {n}>; one instance in every shot it appears in.")
    return ("subject_definitions:\n" + "\n".join(defs)
            + f"\n\nsummary:\n[reference generation + audio reference] A continuous {seconds}-second Chinese "
              f"animated short-drama shot in {len(stages)} stages."
            + (f" Direction for this take: {note}" if note else "") + "\n\n"
              "retention_analysis:\n" + "\n".join(retention) + ("\n" if retention else "")
            + "The setting comes from its own picture, none of the people in it. The only spoken words in this clip are "
              "the Chinese text inside the <d> tags; everything else written here describes the picture and must not be spoken.\n\n"
              "detailed_description:\n" + "\n".join(body)
            + "\n\noverall_soundscape:\nRoom tone and the physical sounds of the action described above. "
              "No narrator, no voice-over, no speech other than the <d> lines.\n\n"
              "non_diegetic_music:\nNone.")


def warn(clip: dict, message: str) -> None:
    print(f"  {clip.get('clip_id', '?')}: H3 prompt {message}", file=sys.stderr, flush=True)


def tag_names(text: str, naming: str) -> str:
    """Resolve full names and unambiguous short forms to their supplied subject tags."""
    names, shorts = {}, {}
    for line in naming.splitlines():
        if " = " not in line:
            continue
        name, tag = (part.strip() for part in line.split(" = ", 1))
        if name:
            names[name] = tag
            short = name.split("·")[0]
            if len(short) >= 2:
                shorts.setdefault(short, set()).add(tag)
    mapping = {**{name: next(iter(tags)) for name, tags in shorts.items() if len(tags) == 1}, **names}
    if not mapping:
        return text
    # One longest-first replacement: 林凡 must not consume 林凡青, and a
    # shared abbreviated name must not silently choose one of two people.
    pattern = re.compile("|".join(re.escape(name) for name in sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda match: mapping[match.group()], text)


META = re.compile(r"tag list|translat|original text|system prompt|the user|please verify|contradiction|instruction says", re.I)


def clean_note(text: str, naming: str) -> str:
    """The instruction without the model's asides.  Asked to map names to tags itself, the model answered with
    commentary - "a character name (莱恩·格雷) that is not in the provided tag list, I have translated it as
    <Subject 1>", "the user's request contains a contradiction" - and the CJK check threw the whole answer away
    three tries in a row (雾月 pilot, 2026-09-13: six episodes looping).  Names become tags, a sentence that
    talks about the task or still carries Chinese is dropped, the rest must be non-empty."""
    text = tag_names(text, naming)
    kept = [part for part in re.split(r"(?<=[.;!?])\s+", text) if part.strip() and not CJK.search(part) and not META.search(part)]
    return " ".join(kept).strip()


def english_note(note: str, naming: str) -> str:
    """The clip's director correction in English, for the prompt H3 reads - in Chinese it was read out as dialogue.
    '' when the translation fails or still carries Chinese.  The names are swapped for their tags before the ask,
    so the model has nothing left to map and nothing to remark on."""
    try:
        answer = ask_json([{"type": "text", "text": NOTE_ASK + naming + "\n" + tag_names(note, naming)}], NOTE_SCHEMA, name="h3note", max_tokens=400)
    except Exception:  # noqa: BLE001 - convert asks again
        return ""
    return clean_note(str(answer.get("note") or "").strip(), naming)


NOTE_LINE_ASK = ("The last numbered line is the director's correction for this clip, not a shot: translate it as its own "
                 "sentence too, so the answer has exactly as many sentences as there are numbered lines.\n")


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
    digest = h3_source_digest(prompt, note,clip.get('crowd_roles'))
    if clip.get("prompt_h3_of") == digest and clip.get("prompt_h3") and not request_issues(clip):
        return False
    stages = stages_of(prompt)
    if not stages:
        warn(clip, "FAILED: the Chinese prompt has no 【阶段】 block to translate")
        return False
    _, subject_of = subject_lines(clip)
    naming = "".join(f"{name} = <Subject {n}>\n" for name, n in subject_of.items())
    picture=0
    for ref in clip.get('references',[]):
        if ref.get('role') in {'character','location'}:
            picture+=1
        crowd=clip.get('crowd_roles',{}).get(ref.get('name'))
        if crowd and ref.get('role')=='character':
            naming+=f"{ref['name']} = the {crowd['count']} distinct unnamed supporting people wearing the clothing from <Picture {picture}>\n"
    visuals = [tag_names(visual, naming) for visual, _ in stages]
    tagged = tag_names(note, naming) if note else ""

    def ask_lines(lines: list[str], extra: str) -> list[str]:
        question = [{"type": "text", "text": ASK + extra + naming + "\n" + "\n".join(f"{i}. {text}" for i, text in enumerate(lines, 1))}]
        answer = ask_json(question, SCHEMA, name="h3prompt", max_tokens=200 + 220 * len(lines))
        return [str(s).strip() for s in (answer.get("shots") or [])]

    problem = ""
    folded = False
    for _ in range(tries):
        try:
            if note and not folded:
                english = ask_lines(visuals + [tagged], NOTE_LINE_ASK)
                if len(english) == len(visuals) + 1 and all(english):
                    direction = clean_note(english[-1], naming)
                    if direction:
                        candidate = compose(clip, english[:-1], stages, direction)
                        conflicts = request_issues({'prompt_h3':candidate})
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
        if len(english) == len(visuals) and all(english):
            candidate = compose(clip, english, stages, '')
            conflicts = request_issues({'prompt_h3':candidate})
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
        from clip_readiness import inspect_episode
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
                if new and new["prompt_h3_of"] == h3_source_digest(clip.get("prompt") or "", now.get(clip.get("clip_id"), ""),clip.get('crowd_roles')):
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


if __name__ == "__main__":
    sys.exit(main())
