from __future__ import annotations
import json
from .common import log, reference_digests
from .policy import COMPLIANCE_SUFFIX, RETRY_TAIL, RETRY_TAIL_H3, soften_prompt

from . import generation

class CacheMiss(RuntimeError):
    """--cache-only: the clip would have to be generated."""

def without_retry(prompt: str) -> str:
        return RETRY_TAIL.sub("", prompt, count=1)

def references_match(saved: dict, references, digests: list[str]) -> bool:
        """The saved request used these very pictures: the same paths and, where it recorded them, the same
        contents.  A request written before digests existed is compared on paths alone, so the cache built
        up to 2026-09-10 stays valid instead of re-rendering wholesale."""
        saved_digests = saved.get("reference_sha256")
        return saved.get("references") == [str(p) for p in references] and (saved_digests is None or list(saved_digests) == digests)

def request_matches(ctx, clip: dict, saved: dict, references, digests: list[str]) -> bool:
        """A take was made for this clip as it now stands: the same pictures and length, and a prompt that is the
        clip's base plus only what a run adds - a retake note, the output filter's compliance line and, on Seedance,
        the softened wording (prescreen or an input refusal).  Every mix of those is the same clip: the list of
        accepted wordings missed softened-then-compliance, and that take was set aside and paid for again on every
        re-entry.  An English (H3) prompt is never softened: a take made from softened wording, or carrying a
        Chinese note H3 reads out, is not this clip."""
        if int(saved.get('repair_take',0)) != int(clip.get('repair_take',0)):
            return False
        if not (references_match(saved, references, digests) and int(saved.get("duration", 0)) == int(clip["request_seconds"])):
            return False
        base = generation.clip_base(ctx, clip)
        english = generation.uses_h3_prompt(ctx, clip)
        forms = {base} if english else {base, soften_prompt(base, getattr(ctx, "softening_rules", None))}
        tail = RETRY_TAIL_H3 if english else RETRY_TAIL
        prompt = str(saved.get("prompt", ""))
        variants = [prompt] + ([prompt[: -len(COMPLIANCE_SUFFIX)]] if prompt.endswith(COMPLIANCE_SUFFIX) and not english else [])
        return any(tail.sub("", variant, count=1) in forms for variant in variants)

def cached_take(ctx, clip: dict, attempt: int) -> bool:
        """Whether take `attempt` of the clip is already in the cache, made for the clip as it now stands."""
        directory = ctx.work / "clips" / clip["clip_id"] / f"attempt_{attempt:02d}"
        video = directory / "clip.mp4"
        if not ((directory / "request.json").is_file() and video.is_file() and video.stat().st_size > 0):
            return False
        try:
            saved = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        references = tuple(ctx.novel_dir / ref["path"] for ref in clip.get("references", []) if ref.get("role") != "voice")
        return request_matches(ctx, clip, saved, references, reference_digests(references))


def current_video(ctx, clip, attempt, directory, references, digests, history):
    output = directory / "clip.mp4"
    if (directory / "request.json").is_file() and output.is_file() and output.stat().st_size > 0:
        # Reuse only a clip generated from this exact prompt and references.
        # Keying on the path alone silently served a stale clip after the
        # chapter was re-planned.
        saved = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        # Compared with the wording clip_base chose, whatever a run added to it (request_matches).  Built from
        # clip["prompt"] alone, it let a local-H3 lane keep clips H3 had rendered from the Chinese prompt, which
        # H3 reads aloud (雾月 1732 on 2026-09-11 kept 11 of its 18 that way).
        if request_matches(ctx, clip, saved, references, digests):
            log(f"{clip['clip_id']} attempt {attempt}: clip matches this request, skipping generation")
            return output
        if ctx.cache_only:
            # Looking at the cache must not change it: the clip stays where it is (a --prune after this run
            # deletes whatever was set aside).
            log(f"{clip['clip_id']} attempt {attempt}: the cached clip was made from another request (cache-only: left in place)")
        else:
            # Move the clip AND its provider task sidecar aside together: the
            # provider refuses to reuse a task whose request hash differs, and
            # a leftover sidecar would make every changed clip fail at submit.
            if (ctx.work.parent / "repair_history/history.json").is_file():
                history.archived_take(ctx.work.parent, clip["clip_id"], output)
            for name in ("clip.mp4", "clip.mp4.task.json", "clip.mp4.partial", "native.wav", "asr.json", "asr_raw.json", "chunks.json"):
                source = directory / name
                if source.exists():
                    target = directory / name.replace("clip.mp4", "clip.stale.mp4").replace("native.wav", "native.stale.wav").replace("asr", "stale_asr").replace("chunks", "stale_chunks")
                    target.unlink(missing_ok=True)
                    source.rename(target)
            log(f"{clip['clip_id']} attempt {attempt}: request changed since the cached clip, regenerating")
    return None


def other_video(ctx, clip, attempt, directory, references, digests):
    for other in sorted((ctx.work / "clips" / clip["clip_id"]).glob("attempt_*")):
        other_video = other / "clip.mp4"
        if other == directory or not (other / "request.json").is_file() or not other_video.is_file() or other_video.stat().st_size == 0:
            continue
        saved = json.loads((other / "request.json").read_text(encoding="utf-8"))
        # The same wording and the same pictures.  Comparing paths alone handed back a video of the old card
        # after the card was redrawn - the very video the check above had just set aside for that reason.
        if request_matches(ctx, clip, saved, references, digests):
            # A retry exists to replace a clip that failed the speech gate;
            # reusing that same clip would just fail it again.  Only a video
            # that passed (or was never judged - a resumed run) is reused.
            if attempt > 1 and (other / "asr.json").is_file():
                try:
                    if not json.loads((other / "asr.json").read_text(encoding="utf-8")).get("passed", True):
                        continue
                except (OSError, ValueError):
                    pass
            log(f"{clip['clip_id']} attempt {attempt}: reusing the matching video from {other.name}")
            return other_video
    return None
