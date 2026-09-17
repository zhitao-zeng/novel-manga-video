"""Regressions from the 2026-09-11 reviews of the fast-tier lanes: finals with gate-failed clips, planning
blocks, English (H3) prompts and their voices, retakes, the render-run count, corrections, card reuse,
the card manifest, review errors and the voice bank."""
from __future__ import annotations
import conductor_dispatch_thin as conductor_dispatch
import conductor_state_thin as conductor_state
import production_render_thin as production_render

from render_context_support import uninitialized_runner

import json
import os
import sys
import time
import types
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_voices_thin  # noqa: E402
import conductor_common_thin as conductor_common
import conductor_flow_thin as conductor_flow
import thin_runs as thin_runs  # noqa: E402
import render_flow_thin as rc  # noqa: E402
import production_flow_thin as production_flow  # noqa: E402
import novel_manga.models as review_models
import novel_manga.review.contracts as review_contracts
import novel_manga.review.storage as review_storage
import review_episode_thin as review_episode
import review_evidence_thin as review_evidence
import review_judges_thin as review_judges  # noqa: E402
from thin_profile import h3_prompt_fingerprint, h3_source_digest, plan_fingerprint  # noqa: E402
from thin_runs import RENDER_RUNS_PER_PLAN, count_run, render_runs  # noqa: E402

NOVEL = "nov"
H3_KEY = {"name": "h3pool", "model": "minimax-h3", "key_var": "", "base_url": "pool", "clip_cap": 15, "parallel": 1,
          "inflight": {"min": 1, "max": 1, "start": 1}}
PAID_KEY = {**H3_KEY, "name": "sd25", "base_url": "", "key_var": "PHANROUTER_API_KEY"}


def episode(tmp_path: Path, n: int = 1) -> Path:
    directory = tmp_path / NOVEL / f"{NOVEL}_{n}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def video_clip(**extra) -> dict:
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡推门走进大殿。【阶段2】林凡抬头看向王座。",
            "request_seconds": 10, "references": [], "lines": []}
    clip.update(extra)
    return clip


def write_plan(directory: Path, clips: list[dict]) -> dict:
    plan = {"policy": "thin-clip-plan-v9-15s", "clips": clips}
    (directory / "clip_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan


def write_report(directory: Path, plan: dict, **fields) -> None:
    report = {"clip_plan_fingerprint": plan_fingerprint(plan), "review_feedback": {}, "failed_clips": [],
              "gate_failed_clips": [], "assembly": {"thin_passed": True}, "clips": []}
    report.update(fields)
    (directory / "thin_media_report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (directory / f"{directory.name}.mp4").write_bytes(b"mp4")  # the final the report stands for


def later(path: Path, seconds: float) -> None:
    stamp = time.time() + seconds
    os.utime(path, (stamp, stamp))


def conductor(tmp_path: Path, keys: list[dict] | None = None, chapters: str = "1-3") -> conductor_flow.Conductor:
    (tmp_path / NOVEL).mkdir(exist_ok=True)
    (tmp_path / NOVEL / "bible_growth.json").write_text(json.dumps({"99": {}}), encoding="utf-8")
    config = {"novel_dir": str(tmp_path / NOVEL), "tmp_dir": str(tmp_path / "conductor"), "keys": keys or [],
              "ranges": [{"chapters": chapters, "plan_mode": 15}],
              "planning": {"block_size": 3, "blocks_min": 1, "blocks_max": 1, "margin": 0}, "qwen": {"urls": []}}
    return conductor_flow.Conductor(config, dry_run=True)


# ---------------------------------------------------------------- render runs (6, 12)
def test_the_conductor_reads_the_render_runs_thin_batch_counts(tmp_path):
    directory = episode(tmp_path)
    write_plan(directory, [video_clip()])
    (directory / "review_feedback.json").write_text("{}", encoding="utf-8")
    count_run(directory)
    count_run(directory)
    assert render_runs(directory) == 2
    assert conductor_state.chapter(conductor(tmp_path), 1)["runs"] == 2
    later(directory / "clip_plan.json", 5)  # written again with the same clips: the count stands
    assert render_runs(directory) == 2
    (directory / "review_feedback.json").write_text(json.dumps({"clip_01": "修正"}, ensure_ascii=False), encoding="utf-8")
    later(directory / "review_feedback.json", 5)  # a new correction is new grounds to try again
    assert render_runs(directory) == 0


# ---------------------------------------------------------------- planning blocks (2)
def test_a_planning_block_with_chapters_left_unplanned_runs_again_a_bounded_number_of_times(tmp_path):
    c = conductor(tmp_path)
    write_plan(episode(tmp_path, 1), [video_clip()])
    (episode(tmp_path, 2) / "planning_failed.json").write_text("{}", encoding="utf-8")
    (episode(tmp_path, 3) / "planning_skipped.json").write_text("{}", encoding="utf-8")
    block = c.blocks[0]
    for run in range(1, conductor_common.PLAN_BLOCK_RUNS + 1):
        conductor_dispatch.tick_planning(c, congested=False)  # dry run: the block "starts"
        assert block["proc"] and not block["done"]
        conductor_dispatch.tick_planning(c, congested=False)  # ...and its process is gone
        if run < conductor_common.PLAN_BLOCK_RUNS:
            assert not block["done"] and block["runs"] == run and block["retry_at"] > time.time()
            block["retry_at"] = 0.0
    assert block["done"] and block["runs"] == conductor_common.PLAN_BLOCK_RUNS


def test_a_planning_block_is_done_once_its_chapters_are_planned_or_skipped(tmp_path):
    c = conductor(tmp_path)
    conductor_dispatch.tick_planning(c, congested=False)
    for n in (1, 2):
        write_plan(episode(tmp_path, n), [video_clip()])
    (episode(tmp_path, 3) / "planning_skipped.json").write_text("{}", encoding="utf-8")
    conductor_dispatch.tick_planning(c, congested=False)
    assert c.blocks[0]["done"] and c.blocks[0]["runs"] == 1


def test_a_chapter_too_short_to_plan_is_marked_skipped(tmp_path):
    batch = object.__new__(production_flow.Batch)
    batch.args = types.SimpleNamespace(replan=False, min_chapter_chars=300, dry_run=False)
    batch.novel_dir, batch.novel_id, batch.rows = tmp_path / NOVEL, NOVEL, {7: {}}
    batch.chapter = lambda index: types.SimpleNamespace(text_count=42)
    batch.plan(7)
    marker = tmp_path / NOVEL / f"{NOVEL}_7" / "planning_skipped.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["chars"] == 42
    assert conductor_state.settled(conductor(tmp_path, chapters="7-7"), 7)


# ---------------------------------------------------------------- finals with gate failures (1)
def warned_episode(tmp_path: Path) -> Path:
    directory = episode(tmp_path)
    clip = video_clip(prompt_h3="english")
    clip["prompt_h3_of"] = h3_source_digest(clip["prompt"])
    write_report(directory, write_plan(directory, [clip]), gate_failed_clips=["clip_01"])
    return directory


def test_a_free_lane_takes_back_a_final_with_gate_failures_until_its_runs_are_used(tmp_path):
    directory = warned_episode(tmp_path)
    paid = conductor_state.chapter(conductor(tmp_path, [PAID_KEY]), 1)
    assert paid["blocked"] and not paid["done"]  # a preview on a paid lane waits for a person
    free = conductor(tmp_path, [H3_KEY])
    assert not conductor_state.chapter(free, 1)["done"] and not conductor_state.chapter(free, 1)["blocked"]
    assert 1 in conductor_state.range_stats(free, free.ranges[0])["renderable"]
    for _ in range(RENDER_RUNS_PER_PLAN):
        count_run(directory)
    state = conductor_state.chapter(free, 1)
    assert state["blocked"] and not state["done"] and 1 not in conductor_state.range_stats(free, free.ranges[0])["renderable"]


def test_a_free_lane_takes_back_a_final_cut_before_a_correction(tmp_path):
    directory = episode(tmp_path)
    write_report(directory, write_plan(directory, [video_clip()]))
    (directory / f"{NOVEL}_1.mp4").write_bytes(b"mp4")
    free = conductor(tmp_path, [H3_KEY])
    assert conductor_state.chapter(free, 1)["done"]
    (directory / "review_feedback.json").write_text(json.dumps({"clip_01": "修正"}, ensure_ascii=False), encoding="utf-8")
    later(directory / "review_feedback.json", 5)
    assert not conductor_state.chapter(free, 1)["done"]


def batch_for(tmp_path: Path, monkeypatch, **overrides) -> production_flow.Batch:
    batch = object.__new__(production_flow.Batch)
    args = dict(rerender=False, no_render=False, dry_run=False, workers=0, inflight=4, tier=None, prescreen=False,
                moderation_repair=True, prune=False, cache_only=False, retake_failed=False)
    args.update(overrides)
    batch.args = types.SimpleNamespace(**args)
    batch.novel_dir, batch.novel_id, batch.rows = tmp_path / NOVEL, NOVEL, {1: {}}
    batch.reviewing, batch.fast, batch.env, batch.notes = False, True, {}, {}
    batch.commands = []

    def run(command, log_path):
        batch.commands.append(command)
        return 0, ""
    monkeypatch.setattr(batch, "run", run)
    monkeypatch.setattr(batch, "prepare_cards", lambda chapter: None)
    monkeypatch.setattr(batch, "fill_result", lambda chapter: None)
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    return batch


def scripts_run(batch: production_flow.Batch) -> list[str]:
    return [Path(command[1]).name for command in batch.commands]


def test_thin_batch_retakes_a_final_with_gate_failures_on_a_free_lane_only(tmp_path, monkeypatch):
    directory = warned_episode(tmp_path)
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL", raising=False)
    paid = batch_for(tmp_path, monkeypatch)
    production_render.render(paid, 1)
    assert paid.rows[1]["render"] == "done_with_warnings" and not paid.commands
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    free = batch_for(tmp_path, monkeypatch)
    production_render.render(free, 1)
    assert scripts_run(free) == ["render_clips_thin.py"] and render_runs(directory) == 1
    for _ in range(RENDER_RUNS_PER_PLAN - 1):
        count_run(directory)
    spent = batch_for(tmp_path, monkeypatch)
    production_render.render(spent, 1)
    assert spent.rows[1]["render"] == "done_with_warnings" and not spent.commands


def test_a_round_turned_away_by_another_renders_lock_spends_no_run(tmp_path, monkeypatch):
    directory = warned_episode(tmp_path)
    (directory / "thin_media_report.json").unlink()
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL", raising=False)
    (directory / ".render.lock").write_text(str(os.getpid()), encoding="utf-8")
    batch = batch_for(tmp_path, monkeypatch)
    for _ in range(RENDER_RUNS_PER_PLAN + 1):
        production_render.render(batch, 1)
    assert batch.rows[1]["render"].startswith("locked by pid") and render_runs(directory) == 0


# ---------------------------------------------------------------- corrections and English prompts (8)
def test_a_new_correction_or_english_prompt_makes_a_final_stale(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    clip = video_clip(prompt_h3="english v1")
    plan = write_plan(directory, [clip])
    write_report(directory, plan, prompt_h3_fingerprint=h3_prompt_fingerprint(plan))
    batch = batch_for(tmp_path, monkeypatch)
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    assert batch.render_status(1) == "done"
    (directory / "review_feedback.json").write_text(json.dumps({"clip_01": "修正"}, ensure_ascii=False), encoding="utf-8")
    assert batch.render_status(1) == "stale"
    (directory / "review_feedback.json").unlink()
    write_plan(directory, [{**clip, "prompt_h3": "english v2"}])
    assert batch.render_status(1) == "stale"
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL")
    assert batch.render_status(1) == "done"  # a Seedance lane renders from the Chinese prompt


# ---------------------------------------------------------------- H3 prompts wired in (4)
def test_a_free_lane_converts_an_episode_without_english_prompts_before_rendering_it(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    write_plan(directory, [video_clip()])
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    batch = batch_for(tmp_path, monkeypatch)

    def run(command, log_path):
        batch.commands.append(command)
        if command[1].endswith("build_h3_prompts.py"):
            plan = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))
            for clip in plan["clips"]:
                clip["prompt_h3"], clip["prompt_h3_of"] = "english", h3_source_digest(clip["prompt"])
            (directory / "clip_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        return 0, ""
    monkeypatch.setattr(batch, "run", run)
    production_render.render(batch, 1)
    assert scripts_run(batch)[:2] == ["build_h3_prompts.py", "render_clips_thin.py"]


def test_an_episode_the_converter_cannot_finish_waits_without_spending_a_run(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    write_plan(directory, [video_clip()])
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    batch = batch_for(tmp_path, monkeypatch)
    production_render.render(batch, 1)
    assert batch.rows[1]["render"] == "skipped (H3 prompt not ready)"
    assert scripts_run(batch) == ["build_h3_prompts.py"] and render_runs(directory) == 0


@pytest.fixture
def h3prompts(monkeypatch):
    for name in ("QWEN38_LOCAL_BASE_URL", "QWEN38_LOCAL_MODEL", "QWEN38_LOCAL_API_KEY_VAR"):
        monkeypatch.setenv(name, "unused")  # the module sets defaults for these on import
    import build_h3_prompts
    return build_h3_prompts


def test_a_translation_with_the_wrong_number_of_shots_is_asked_again_then_left_out(h3prompts, monkeypatch):
    answers = iter([{"shots": ["One."]}, {"shots": ["Wide shot of the hall.", "He looks up at the throne."]}])
    monkeypatch.setattr(h3prompts, "ask_json", lambda *args, **kwargs: next(answers))
    clip = video_clip()
    assert h3prompts.convert(clip)
    assert "He looks up at the throne." in clip["prompt_h3"] and "大殿" not in clip["prompt_h3"]
    assert clip["prompt_h3_of"] == h3_source_digest(clip["prompt"])
    calls = []

    def short(*args, **kwargs):
        calls.append(1)
        return {"shots": ["Only one."]}
    monkeypatch.setattr(h3prompts, "ask_json", short)
    fresh = video_clip()
    assert not h3prompts.convert(fresh)
    assert len(calls) == h3prompts.TRIES and "prompt_h3" not in fresh


def test_a_failed_translation_call_is_retried(h3prompts, monkeypatch):
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("judge port busy")
        return {"shots": ["A.", "B."]}
    monkeypatch.setattr(h3prompts, "ask_json", flaky)
    assert h3prompts.convert(video_clip()) and len(calls) == 2


def test_the_converter_takes_just_the_episodes_named(h3prompts, monkeypatch, tmp_path):
    for n in (1, 2):
        write_plan(episode(tmp_path, n), [video_clip()])
    monkeypatch.setattr(h3prompts, "ask_json", lambda *args, **kwargs: {"shots": ["A.", "B."]})
    monkeypatch.setattr(sys, "argv", ["build_h3_prompts.py", NOVEL, "--novel-dir", str(tmp_path / NOVEL), "--chapters", "2", "--workers", "1"])
    assert h3prompts.main() == 0
    plans = {n: json.loads((episode(tmp_path, n) / "clip_plan.json").read_text(encoding="utf-8")) for n in (1, 2)}
    assert "prompt_h3" not in plans[1]["clips"][0] and plans[2]["clips"][0]["prompt_h3"]


# ---------------------------------------------------------------- the runner (1, 3, 9)
def runner(tmp_path: Path, local: str | None = "pool") -> rc.ThinMediaRunner:
    r = uninitialized_runner()
    r.context.novel_dir = tmp_path / NOVEL
    r.context.novel_dir.mkdir(parents=True, exist_ok=True)
    r.context.settings = types.SimpleNamespace(local_h3_base_url=local)
    r.context.feedback, r.context.max_attempts, r.context.free_retries = {}, 2, bool(local)
    r.context.work = r.context.novel_dir / f"{NOVEL}_1" / "work"
    r.context.prescreen, r.context.inflight, r.context.moderation_repair, r.context.cache_only = False, 0, True, False
    return r


def test_local_retries_do_not_add_spoken_director_notes(tmp_path):
    h3, seedance = runner(tmp_path), runner(tmp_path, local=None)
    clip = video_clip(prompt_h3="english")
    assert h3.retry_suffix(clip, 1) == h3.retry_suffix(clip, 2) == h3.retry_suffix(clip, 3) == ""
    assert seedance.retry_suffix(clip, 2) == seedance.retry_suffix(clip, 3) == rc.RETRY_SUFFIX
    for suffix in (h3.retry_suffix(clip, 2), h3.retry_suffix(clip, 4), rc.RETRY_SUFFIX):
        assert h3.without_retry("prompt" + suffix) == "prompt"
    assert h3.without_retry('prompt' + rc.RETRY_SUFFIX_H3 + ' This is take 4.') == 'prompt'


def test_a_free_lane_gives_cached_failures_fresh_takes(tmp_path, monkeypatch):
    def takes(r: rc.ThinMediaRunner) -> list[int]:
        made = []

        def generate(clip, attempt):
            made.append(attempt)
            clip["_generated"] = attempt > 2  # takes 1 and 2 come from the cache
            return tmp_path / f"take{attempt}.mp4"
        monkeypatch.setattr(r, "generate_clip", generate)
        monkeypatch.setattr(r, "analyse_clip", lambda clip, video: {"passed": False, "cer": 1.0, "max_volume_db": -3.0, "issues": ["missing"]})
        assert r.process_clip(video_clip())["selected"]
        return made
    assert takes(runner(tmp_path)) == [1, 2, 3, 4]
    assert takes(runner(tmp_path, local=None)) == [1, 2]


def write_voice(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * int(16000 * seconds))


def test_audio_tags_point_at_the_voices_the_request_carries(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_CLIP_SECONDS_MAX", "15")  # 15 s of voice: two six-second samples fit, three do not
    r = runner(tmp_path)
    names = ["莱恩", "比尔", "卡拉"]
    for name in names:
        write_voice(r.context.novel_dir / "series_assets" / "voices" / f"{name}.wav", 6.0)
    voices = [{"role": "voice", "name": n, "path": f"series_assets/voices/{n}.wav"} for n in names]
    lines = [{"speaker_name": "卡拉", "text": "很长很长的一句台词"}, {"speaker_name": "莱恩", "text": "中等的台词"}, {"speaker_name": "比尔", "text": "短"}]
    prompt_h3 = ("subject_definitions:\n"
                 "<Audio 1> is the voice-timbre reference for <Subject 1>.\n"
                 "<Audio 2> is the voice-timbre reference for <Subject 2>.\n"
                 "<Audio 3> is the voice-timbre reference for <Subject 3>.\n\nsummary:\nA shot.")
    clip = video_clip(references=voices, lines=lines, prompt_h3=prompt_h3)
    assert [p.stem for p in r.reference_voices(clip)] == ["卡拉", "莱恩"]  # most-spoken first; 比尔 is over budget
    base = r.clip_base(clip)
    assert "<Audio 1> is the voice-timbre reference for <Subject 3>." in base
    assert "<Audio 2> is the voice-timbre reference for <Subject 1>." in base
    assert "<Subject 2>" not in base and base.endswith("summary:\nA shot.")
    duo = video_clip(references=voices[:2], lines=[{"speaker_name": "莱恩", "text": "长一点的台词"}, {"speaker_name": "比尔", "text": "短"}],
                     prompt_h3="<Audio 1> is 莱恩.\n<Audio 2> is 比尔.")
    assert r.clip_base(duo) == duo["prompt_h3"]  # voices already go out in plan order: nothing changes


def test_a_video_of_a_redrawn_card_is_not_taken_from_another_attempt(tmp_path, monkeypatch):
    r = runner(tmp_path, local=None)
    card = r.context.novel_dir / "series_assets" / "characters" / "character_001" / "turnaround.jpeg"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"old card")
    clip = video_clip(references=[{"role": "character", "name": "林凡", "path": "series_assets/characters/character_001/turnaround.jpeg"}])
    old = rc.reference_digests([card])
    for attempt in (1, 2):
        directory = r.context.work / "clips" / "clip_01" / f"attempt_{attempt:02d}"
        directory.mkdir(parents=True)
        (directory / "clip.mp4").write_bytes(b"video of the old card")
        (directory / "request.json").write_text(json.dumps({
            "prompt": r.clip_prompt(clip) + r.retry_suffix(clip, attempt), "references": [str(card)],
            "reference_sha256": old, "duration": 10}, ensure_ascii=False), encoding="utf-8")
    card.write_bytes(b"new card")
    made = []

    class Provider:
        def create_video(self, prompt, image, output, duration, additional_images=(), reference_audios=()):
            made.append(prompt)
            output.write_bytes(b"new video")
    r.context.provider = Provider()
    monkeypatch.setattr(rc, "wait_for_inflight_redraws", lambda paths: [])
    monkeypatch.setattr(rc, "media_duration", lambda path: 10.0)
    video = r.generate_clip(clip, 1)
    assert made and video.read_bytes() == b"new video"


def test_parallel_card_builds_keep_each_others_manifest_records(tmp_path):
    root = tmp_path / NOVEL / "series_assets"
    bible = types.SimpleNamespace(characters=[], locations=["大殿", "山门"], visual_style="2D", style_fingerprint="fp")
    factory = rc.FramedAssetFactory(types.SimpleNamespace(style_master_path=None), None)
    factory._location_prompt = lambda bible, location: f"{location}的空镜"

    def ensure_card(prompt, output, reference=None, aspect_ratio=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"card")
        if output.parent.name == "location_001":  # another process finishes its card meanwhile
            rc.FramedAssetFactory.build_selected(factory, root, bible, set(), {"location_002"})
        return types.SimpleNamespace(path=output)
    factory.ensure_card = ensure_card
    rc.FramedAssetFactory.build_selected(factory, root, bible, set(), {"location_001"})
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert [row["asset_id"] for row in manifest["locations"]] == ["location_001", "location_002"]


# ---------------------------------------------------------------- review errors (15)
def test_a_review_the_judge_could_not_finish_goes_back_in_the_queue(tmp_path):
    directory = episode(tmp_path)
    write_report(directory, write_plan(directory, [video_clip()]))
    review = directory / "episode_review.json"
    review.write_text(json.dumps({"policy": thin_runs.REVIEW_POLICY, "clips": {"clip_01": {"severity": "review_error"}}}), encoding="utf-8")
    later(review, 5)
    c = conductor(tmp_path)
    assert conductor_state.chapter(c, 1)["unreviewed"]
    review.write_text(json.dumps({"policy": thin_runs.REVIEW_POLICY, "clips": {"clip_01": {"severity": "review_error"}}, "error_rounds": 3}), encoding="utf-8")
    later(review, 10)
    assert not conductor_state.chapter(c, 1)["unreviewed"]


def test_a_resumed_review_judges_again_only_the_clips_it_failed_on(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    clips = [video_clip(clip_id="clip_01"), video_clip(clip_id="clip_02")]
    write_plan(directory, clips)
    videos = {}
    for clip in clips:
        path = directory / "work" / "clips" / clip["clip_id"] / "attempt_01" / "clip.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"mp4")
        videos[clip["clip_id"]] = path
    (directory / "thin_media_report.json").write_text(json.dumps({"clips": [
        {"clip_id": cid, "selected": {"video": str(path), "hypothesis": ""}} for cid, path in videos.items()]}), encoding="utf-8")
    review = directory / "episode_review.json"
    review.write_text(json.dumps({"policy": review_contracts.POLICY, "clips": {
        "clip_01": {"video": str(videos["clip_01"]), "take": review_storage.take_identity(videos["clip_01"]), "severity": "pass"},
        "clip_02": {"video": str(videos["clip_02"]), "severity": "review_error", "error": "ReadTimeout"}}}), encoding="utf-8")
    later(review, 5)
    (tmp_path / NOVEL / "story_bible.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(review_evidence, "load_review_rules", lambda novel_dir: None)
    monkeypatch.setattr(review_models, "StoryBible", types.SimpleNamespace(model_validate_json=lambda text: types.SimpleNamespace(characters=[])))
    judged = []

    def judge(clip, video, *rest):
        judged.append(clip["clip_id"])
        return {"severity": "pass"}
    monkeypatch.setattr(review_judges, "judge_clip", judge)
    report = review_episode.review_episode(directory)
    assert judged == ["clip_02"] and report["clips"]["clip_01"]["severity"] == "pass" and report["error_rounds"] == 0
    review_episode.review_episode(directory)  # the same takes: every verdict stands, nothing is judged again
    assert judged == ["clip_02"]
    monkeypatch.setenv("NOVEL_REVIEW_FRESH", "1")
    review_episode.review_episode(directory)  # asked for a fresh review: every clip again
    assert judged == ["clip_02", "clip_01", "clip_02"]


# ---------------------------------------------------------------- voice bank after --prune (13)
def test_the_voice_bank_cuts_from_the_clip_once_prune_removed_native_wav(tmp_path):
    directory = episode(tmp_path)
    video = directory / "work" / "clips" / "clip_01" / "attempt_01" / "clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    write_plan(directory, [video_clip(lines=[{"speaker_name": "林凡", "text": "我们走吧，天快黑了"}])])
    (directory / "thin_media_report.json").write_text(json.dumps({"clips": [{"clip_id": "clip_01", "selected": {
        "video": str(video), "chunks": [{"subtitle": "script_span", "matched_lines": "我们走吧，天快黑了", "start": 0.5, "end": 3.0}]}}]},
        ensure_ascii=False), encoding="utf-8")
    found = build_voices_thin.attributed_chunks(tmp_path / NOVEL)
    assert found["林凡"] == [(2.5, video, 0.5, 3.0, f"{NOVEL}_1")]
