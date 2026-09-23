"""The H3 admission gate: a local-H3 lane never submits a NEW request from the Chinese prompt.

Every entry point used to share one gap: english_correction_for_new_take guarded only takes that
carried a director's note, and clip_base happily fell back to clip["prompt"] whenever prompt_h3
was missing or stale - so a direct render (render_clips_thin.py, repair, a manual run) sent
Chinese to a model that reads stage directions aloud.  The gate here fires only on a fresh
submission: an old take that matches its request is reused above it, exactly as before.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import novel_manga.application.rendering.flow as rendering
from support.render_context import uninitialized_runner


PROMPT = "【生成目标】一段 15 秒片段。\n【阶段】1. 他推门。"


def h3_clip(**extra):
    return {"clip_id": "clip_01", "kind": "video", "request_seconds": 15,
            "prompt": PROMPT, "references": [], "lines": [], **extra}


def runner_for(tmp_path, monkeypatch, clip, *, plan=None):
    directory = tmp_path / "nov" / "nov_1"
    directory.mkdir(parents=True, exist_ok=True)
    plan = plan or {"limits": {"max_clip_seconds": 15}, "clips": [clip]}
    (directory / "clip_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    for ref in clip.get("references") or []:            # a referenced card exists on disk
        path = directory.parent / ref["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpeg")
    r = uninitialized_runner()
    r.context.episode_dir, r.context.novel_dir = directory, directory.parent
    r.context.work = directory / "work"
    r.context.clip_plan, r.context.script, r.context.feedback = plan, {"shots": []}, {}
    r.context.cache_only, r.context.max_attempts = False, 1
    r.context.settings = SimpleNamespace(local_h3_base_url="http://h3.local")
    monkeypatch.setattr(r, "prescreens", lambda c: False)
    return r


def test_missing_translation_blocks_the_new_request(tmp_path, monkeypatch):
    """No English prompt at all: the request must not go out in Chinese."""
    r = runner_for(tmp_path, monkeypatch, h3_clip())
    with pytest.raises(RuntimeError, match="no English prompt yet.*build_h3_prompts"):
        r.generate_clip(h3_clip(), 1)


ENGLISH_BODY = ("subject_definitions:\n<Subject 1> is the person shown in <Picture 1>.\n\nsummary:\n\n"
                "detailed_description:\n[Shot 1] <Subject 1> (S1) says <d>[Chinese] 你好</d> and pushes the door.\n\n"
                "overall_soundscape:\nroom tone")


def _bound(**extra):
    return h3_clip(prompt_h3=ENGLISH_BODY, scene_ids=["sc1"], shot_timing=[{"seconds": 4}],
                   references=[{"role": "character", "name": "甲", "path": "c1/turnaround.jpeg"}],
                   dialogue_bindings=[{"stage": 1, "speaker_name": "甲",
                                       "delivery_mode": "visible_dialogue", "text": "你好"}], **extra)


def test_stale_translation_blocks_the_new_request(tmp_path, monkeypatch):
    """The Chinese prompt was re-packed after the translation: the old English is not this clip."""
    from novel_manga.application.profiles import h3_compile_inputs, h3_source_digest
    stale = _bound()
    stale["prompt_h3_of"] = h3_source_digest("【生成目标】别的中文。\n【阶段】1. 别的。", "", None, h3_compile_inputs(stale))
    r = runner_for(tmp_path, monkeypatch, stale)
    with pytest.raises(RuntimeError, match="predates the current Chinese one"):
        r.generate_clip(stale, 1)


def test_reference_reorder_blocks_the_new_request(tmp_path, monkeypatch):
    """Seats reordered: subject and picture numbers would shift, so the compiled stamp changed."""
    from novel_manga.application.profiles import h3_compile_inputs, h3_source_digest
    reordered = _bound()
    reordered["prompt_h3_of"] = h3_source_digest(reordered["prompt"], "", None, h3_compile_inputs(reordered))
    assert not _outdated(reordered)                     # stamp covers the current shape
    reordered["references"] = [{"role": "character", "name": "甲", "path": "c1/turnaround.jpeg"},
                                {"role": "location", "name": "门廊", "path": "l1/establishing.jpeg"}]
    assert _outdated(reordered)                         # a new seat in front: old translation no longer current
    r = runner_for(tmp_path, monkeypatch, reordered)
    with pytest.raises(RuntimeError, match="no current English|predates|prompt_h3_skip"):
        r.generate_clip(reordered, 1)


def test_dialogue_binding_change_blocks_the_new_request(tmp_path, monkeypatch):
    """A bound line switched speaker or mode: the English request disagrees with the plan."""
    from novel_manga.application.profiles import h3_compile_inputs, h3_source_digest
    rebound = _bound()
    rebound["prompt_h3_of"] = h3_source_digest(rebound["prompt"], "", None, h3_compile_inputs(rebound))
    rebound["dialogue_bindings"][0]["delivery_mode"] = "offscreen_dialogue"
    assert _outdated(rebound)
    r = runner_for(tmp_path, monkeypatch, rebound)
    with pytest.raises(RuntimeError):
        r.generate_clip(rebound, 1)


def test_current_translation_still_submits(tmp_path, monkeypatch):
    """The gate stops nothing when the English prompt is current: the request goes out."""
    from novel_manga.application.profiles import h3_compile_inputs, h3_source_digest
    fresh = _bound()
    fresh["prompt_h3_of"] = h3_source_digest(fresh["prompt"], "", None, h3_compile_inputs(fresh))
    r = runner_for(tmp_path, monkeypatch, fresh)
    submitted = []

    class Slot:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rendering, "wait_for_inflight_redraws", lambda refs: None)
    monkeypatch.setattr(rendering, "acquire_inflight_slot", lambda *a, **kw: Slot())
    monkeypatch.setattr(rendering.generation, "submit",
                        lambda ctx, clip, request, output, refs: submitted.append(request))
    monkeypatch.setattr(rendering.time, "sleep", lambda s: None)
    try:
        r.generate_clip(fresh, 1)
    except Exception:                                  # the stub provider ends the take; the request is what matters
        pass
    assert submitted and "【生成目标】" not in submitted[0]["prompt"]


def test_a_matching_cached_take_is_reused_not_regated(tmp_path, monkeypatch):
    """prompt_h3_skip exists to keep a clip H3 rendered from the Chinese prompt: with its matching
    cache present the runner reuses it and the gate never fires."""
    clip = h3_clip(prompt_h3_skip=True)
    directory = tmp_path / "nov" / "nov_1" / "work" / "clips" / "clip_01" / "attempt_01"
    directory.mkdir(parents=True)
    (directory / "clip.mp4").write_bytes(b"video")
    (directory / "request.json").write_text(json.dumps({"clip_id": "clip_01", "duration": 15,
                                                        "prompt": PROMPT, "references": [],
                                                        "reference_sha256": [], "repair_take": 0}), encoding="utf-8")
    r = runner_for(tmp_path, monkeypatch, clip)
    out = r.generate_clip(clip, 1)
    assert out.name == "clip.mp4"                       # 复用，没走到提交闸门


def _outdated(clip) -> bool:
    from novel_manga.application.profiles import h3_prompt_outdated
    return h3_prompt_outdated(clip, "")
