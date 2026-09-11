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

from novel_manga.util import atomic_write_json  # noqa: E402

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
            subject_of[ref["name"]] = picture
            defs.append(f"<Subject {picture}> is the character {ref['name']}, shown in <Picture {picture}>. "
                        f"Take only the face, hair, build and clothing from <Picture {picture}>.")
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


def compose(clip: dict, english: list[str], stages: list) -> str:
    defs, subject_of = subject_lines(clip)
    body = []
    for index, ((_, turns), text) in enumerate(zip(stages, english), 1):
        body.append(f"[Shot {index}] {text}")
        for who, line, offscreen in turns:
            if offscreen or who not in subject_of:
                body.append(f"An off-screen voice says <d>[Chinese] {line}</d>")
            else:
                body.append(f"<Subject {subject_of[who]}> says <d>[Chinese] {line}</d>")
    seconds = clip.get("request_seconds")
    return ("subject_definitions:\n" + "\n".join(defs)
            + f"\n\nsummary:\n[reference generation + audio reference] A continuous {seconds}-second Chinese "
              f"animated short-drama shot in {len(stages)} stages.\n\n"
              "retention_analysis:\nKeep each character's identity from its own picture and the setting from its "
              "own picture. The only spoken words in this clip are the Chinese text inside the <d> tags; "
              "everything else written here describes the picture and must not be spoken.\n\n"
              "detailed_description:\n" + "\n".join(body)
            + "\n\noverall_soundscape:\nRoom tone and the physical sounds of the action described above. "
              "No narrator, no voice-over, no speech other than the <d> lines.\n\n"
              "non_diegetic_music:\nNone.")


def warn(clip: dict, message: str) -> None:
    print(f"  {clip.get('clip_id', '?')}: H3 prompt {message}", file=sys.stderr, flush=True)


def convert(clip: dict, tries: int = TRIES) -> bool:
    """Write clip["prompt_h3"]; True when written.

    A translation that fails, or comes back with a different number of sentences than the clip has
    shots, is asked again.  After `tries` the clip is left without an English prompt - an H3 lane then
    waits for it - instead of being padded out: that attached descriptions to the wrong shots, and
    filled the gap with the Chinese text, which H3 reads aloud."""
    prompt = clip.get("prompt") or ""
    digest = h3_source_digest(prompt)
    if clip.get("prompt_h3_of") == digest:
        return False
    stages = stages_of(prompt)
    if not stages:
        warn(clip, "FAILED: the Chinese prompt has no 【阶段】 block to translate")
        return False
    _, subject_of = subject_lines(clip)
    naming = "".join(f"{name} = <Subject {n}>\n" for name, n in subject_of.items())
    question = [{"type": "text", "text": ASK + naming + "\n" + "\n".join(
        f"{i}. {visual}" for i, (visual, _) in enumerate(stages, 1))}]
    problem = ""
    for _ in range(tries):
        try:
            answer = ask_json(question, SCHEMA, name="h3prompt", max_tokens=200 + 220 * len(stages))
            english = [str(s).strip() for s in (answer.get("shots") or [])]
        except Exception as error:  # noqa: BLE001 - asked again, and reported if it keeps failing
            problem = f"{type(error).__name__}: {str(error)[:160]}"
            continue
        if len(english) == len(stages) and all(english):
            clip["prompt_h3"] = compose(clip, english, stages)
            clip["prompt_h3_of"] = digest
            return True
        problem = f"{len(english)} sentence(s) back for {len(stages)} shots"
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
        video = [c for c in plan["clips"] if c.get("kind") == "video"]
        if args.rebuild:
            for clip in video:
                clip.pop("prompt_h3_of", None)
        made = {clip["clip_id"]: clip for clip in video if convert(clip)}
        if made:
            # The translations take minutes: write them into the plan as it is now, and only onto clips
            # whose Chinese prompt is still the one they were made from.
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = plan
            for clip in current.get("clips", []):
                new = made.get(clip.get("clip_id"))
                if new and new["prompt_h3_of"] == h3_source_digest(clip.get("prompt") or ""):
                    clip["prompt_h3"], clip["prompt_h3_of"] = new["prompt_h3"], new["prompt_h3_of"]
            atomic_write_json(path, current)
            plan = current
        video = [c for c in plan["clips"] if c.get("kind") == "video"]
        return len(made), len(video), sum(1 for clip in video if h3_prompt_outdated(clip))

    done = total = missing = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, (changed, clips, left) in enumerate(pool.map(one, episodes), 1):
            done += changed
            total += clips
            missing += left
            if n % 100 == 0:
                print(f"  {n}/{len(episodes)} 集，已转 {done} 段", flush=True)
    print(f"\n转好 {done} 段（共 {total} 段视频片段）" + (f"；{missing} 段仍没有可用的英文提示词" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
