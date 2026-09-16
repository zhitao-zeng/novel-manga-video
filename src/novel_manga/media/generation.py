from __future__ import annotations
import os
import re
import wave
from pathlib import Path
from ..util import run
from .common import log, reference_digests
from .policy import COMPLIANCE_SUFFIX, RETRY_SUFFIX, soften_prompt

VOICE_BUDGET_SECONDS = 29.0
VOICE_BUDGET_SHORT_SECONDS = 15.0
AUDIO_TAG_H3 = re.compile(r"<Audio (\d+)>")

def voice_budget_seconds() -> float:
    """The reference-audio budget of this lane, from its clip length cap."""
    try:
        cap = float(os.environ.get("NOVEL_CLIP_SECONDS_MAX", "30") or 30)
    except ValueError:
        cap = 30.0
    return VOICE_BUDGET_SHORT_SECONDS if cap <= 15 else VOICE_BUDGET_SECONDS

def trimmed_voice(path: Path, seconds: float) -> Path:
    """A copy of the voice sample cut to `seconds`, cached next to the bank."""
    out = path.parent / ".trim" / f"{path.stem}.{seconds:g}s.wav"
    if not out.is_file() or out.stat().st_mtime < path.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_suffix(".partial.wav")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", f"{seconds:.2f}", "-c:a", "pcm_s16le", str(partial)])
        os.replace(partial, out)
    return out

def renumber_audio(prompt: str, sent: list[int]) -> str:
    """Point an H3 prompt's <Audio N> at the voices its request actually carries.

    build_h3_prompts.py numbers the voices in the order the plan lists them; the runner sends only
    those that fit the reference-audio budget, most-spoken first.  So the N-th audio of a request was
    often another character's voice (雾月 208 gave 莱恩 比尔's).  `sent` holds plan positions in request
    order: a line about a voice left out is dropped and the others are renumbered, and a prompt whose
    voices all go out in plan order comes back unchanged."""
    position = {plan_index: request_index for request_index, plan_index in enumerate(sent, 1)}
    kept = []
    for line in prompt.split("\n"):
        if any(int(n) not in position for n in AUDIO_TAG_H3.findall(line)):
            continue
        kept.append(AUDIO_TAG_H3.sub(lambda m: f"<Audio {position[int(m.group(1))]}>", line))
    return "\n".join(kept)

def uses_h3_prompt(ctx, clip: dict) -> bool:
        # prompt_h3_skip keeps a clip already rendered from the Chinese prompt whose dialogue checked
        # out: it points the request back at the one that produced the clip, so the cache holds it.
        return bool(ctx.settings.local_h3_base_url and clip.get("prompt_h3") and not clip.get("prompt_h3_skip"))

def clip_base(ctx, clip: dict) -> str:
        """The clip's prompt before any softening: what clip_prompt sends, and what the cache compares."""
        # H3 works out what to speak from the language it is written in, so the local lanes read the English
        # rendering of the same plan; Seedance keeps the Chinese one.  On an English prompt the correction is part of
        # it (build_h3_prompts writes it in English): appended here in Chinese, H3 read it out as dialogue, as it did
        # the Chinese retake note.
        if uses_h3_prompt(ctx, clip):
            # Its <Audio N> count the plan's voices; the request carries those within budget, most-spoken first.
            return renumber_audio(clip["prompt_h3"], [position for position, _ in chosen_voices(ctx, clip)[0]])
        note = str(ctx.feedback.get(clip["clip_id"], "")).strip()
        return clip["prompt"] + (f"\n【导演修正】{note}" if note else "")

def retry_suffix(ctx, clip: dict, attempt: int) -> str:
        """Local retries vary the seed; legacy retry text remains readable by the cache matcher."""
        if attempt <= 1 or ctx.settings.local_h3_base_url:
            return ""
        return RETRY_SUFFIX

def clip_prompt(ctx, clip: dict) -> str:
        prompt = clip_base(ctx, clip)
        return soften_prompt(prompt) if clip.get("_softened") else prompt

def chosen_voices(ctx, clip: dict) -> tuple[list[tuple[int, Path]], list[str], float]:
        """The clip's reference voices kept under the service's budget, as (position among the plan's voice
        references, sample) in the order they are sent; the ones left out; and the seconds used.

        Seedance 2.5 refuses a request whose reference audio adds up to more
        than 30.2 s.  Speakers with more lines in this clip come first, and a
        voice that would push the total over the budget is left out (logged),
        so a two- or three-hander still ships with the voices that matter most.
        """
        spoken: dict[str, int] = {}
        for line in clip.get("lines", []):
            spoken[line.get("speaker_name", "")] = spoken.get(line.get("speaker_name", ""), 0) + len(str(line.get("text", "")))
        voices = [ref for ref in clip.get("references", []) if ref.get("role") == "voice"]
        candidates = [(position, ref) for position, ref in enumerate(voices, 1) if (ctx.novel_dir / ref["path"]).is_file()]
        candidates.sort(key=lambda item: -spoken.get(item[1].get("name", ""), 0))
        budget = getattr(ctx, "voice_budget", None) or voice_budget_seconds()
        # A tight budget (the 15 s lane) is shared by the two main speakers as
        # trimmed samples rather than spent on one of them.
        share = budget if budget >= VOICE_BUDGET_SECONDS or len(candidates) < 2 else round(budget / 2, 1)
        chosen, total, dropped = [], 0.0, []
        for position, ref in candidates:
            path = ctx.novel_dir / ref["path"]
            try:
                with wave.open(str(path), "rb") as handle:
                    seconds = handle.getnframes() / float(handle.getframerate() or 16000)
            except (wave.Error, OSError):
                seconds = budget  # unreadable header: assume it fills the budget
            if seconds > share + 0.05:
                try:
                    path = trimmed_voice(path, share)
                    seconds = share
                except Exception as error:  # noqa: BLE001 - fall back to the budget check on the full sample
                    log(f"{clip['clip_id']}: could not trim {path.name}: {type(error).__name__}")
            if total + seconds > budget:
                dropped.append(f"{ref.get('name')}({seconds:.0f}s)")
                continue
            chosen.append((position, path))
            total += seconds
        return chosen, dropped, total

def reference_voices(ctx, clip: dict) -> tuple[Path, ...]:
        """The voice samples sent with the clip (chosen_voices), logged."""
        chosen, dropped, total = chosen_voices(ctx, clip)
        if chosen or dropped:
            log(f"{clip['clip_id']}: reference voices {[p.stem for _, p in chosen]} ({total:.0f}s)" + (f", over budget: {dropped}" if dropped else ""))
        return tuple(path for _, path in chosen)


def build_request(ctx, clip, attempt):
    retry = retry_suffix(ctx, clip, attempt)
    prompt = clip_prompt(ctx, clip) + retry + (COMPLIANCE_SUFFIX if clip.get('_compliance') else '')
    references = tuple(ctx.novel_dir / ref['path'] for ref in clip.get('references', []) if ref.get('role') != 'voice')
    digests = reference_digests(references)
    request = {'clip_id': clip['clip_id'], 'attempt': attempt, 'duration': clip['request_seconds'],
               'prompt': prompt, 'references': [str(p) for p in references], 'reference_sha256': digests,
               'workflow': 'thin-seedance-native-dialogue-v1', 'repair_take': int(clip.get('repair_take', 0))}
    if ctx.settings.local_h3_base_url:
        request['seed_variant'] = int(clip.get('repair_take', 0))*100 + attempt - 1
    return request, references, digests, retry


def submit(ctx, clip, request, output, references):
    voices = reference_voices(ctx, clip)
    return ctx.provider.create_video(request['prompt'], None, output, duration=float(clip['request_seconds']),
            additional_images=references, reference_audios=voices,
            **({'seed_variant': request['seed_variant']} if ctx.settings.local_h3_base_url else {}))
