import production_render_thin as production_render

from render_context_support import uninitialized_runner
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import clip_readiness as ready
import render_flow_thin as rendering
import production_flow_thin as production_flow
from thin_runs import episode_status, render_runs


def clip(cid="clip_01", index=1, seconds=5):
    return {"clip_id": cid, "kind": "video", "shot_indexes": [index], "seconds_estimate": seconds,
            "request_seconds": 15, "prompt": "one shot", "references": [], "spoken_text": "你好"}


@pytest.fixture
def episode(tmp_path):
    directory = tmp_path / "nov" / "nov_1"
    directory.mkdir(parents=True)
    plan = {"limits": {"max_clip_seconds": 15}, "clips": [clip(seconds=40), clip("clip_02", 2)]}
    script = {"shots": [{"index": 1}, {"index": 2}]}
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    (directory / "chapter_script.json").write_text(json.dumps(script))
    return directory, plan, script


def test_overlong_or_missing_source_blocks_only_affected_clip(episode):
    _, plan, script = episode
    assert set(ready.plan_issues(plan, script)) == {"clip_01"}
    plan["clips"][0]["seconds_estimate"] = 5
    plan["clips"][0]["shot_indexes"] = [99]
    assert set(ready.plan_issues(plan, script)) == {"clip_01"}


@pytest.mark.parametrize("parts", [[[1, 3], [3, 3]], [[1, 2], [1, 2]], [[1, 2], [2, 3]]])
def test_explicit_missing_duplicate_and_inconsistent_parts_are_blocked(parts):
    clips = [clip(f"clip_0{i}") for i in [1, 2]]
    for c, part in zip(clips, parts):
        c["shot_parts"] = [{"index": 1, "part": part}]
    assert set(ready.plan_issues({"clips": clips})) == {"clip_01", "clip_02"}


def test_complete_parts_and_mixed_legacy_records_are_not_guessed():
    clips = [clip(f"clip_0{i}") for i in [1, 2]]
    for i, c in enumerate(clips, 1):
        c["shot_parts"] = [{"index": 1, "part": [i, 2]}]
    assert not ready.plan_issues({"clips": clips})
    clips[0].pop("shot_parts")
    assert not ready.plan_issues({"clips": clips})


def runner_for(episode, monkeypatch):
    directory, plan, script = episode
    r = uninitialized_runner()
    r.context.episode_dir, r.context.novel_dir = directory, directory.parent
    r.context.work = directory / "work"
    r.context.clip_plan, r.context.script, r.context.feedback = plan, script, {}
    r.context.cache_only, r.context.max_attempts, r.context.free_retries, r.context.workers = False, 1, False, 2
    r.context.settings = SimpleNamespace(local_h3_base_url="")
    requested = []
    monkeypatch.setattr(r, "build_assets", lambda **kw: None)
    def generate(c, attempt):
        requested.append(c["clip_id"])
        return directory / (c["clip_id"] + ".mp4")
    monkeypatch.setattr(r, "generate_clip", generate)
    monkeypatch.setattr(r, "analyse_clip", lambda c, video: {"passed": True, "video": str(video), "cer": 0,
                                                          "max_volume_db": -5, "issues": []})
    monkeypatch.setattr(r, "assemble", lambda *a: pytest.fail("partial episode cannot be assembled or declared done"))
    return r, requested


def test_bad_clip_never_reaches_generation_but_good_clip_finishes(episode, monkeypatch):
    directory, plan, _ = episode
    old_final = directory / "nov_1.mp4"
    old_final.write_bytes(b"old preview")
    r, requested = runner_for(episode, monkeypatch)
    result = r.run()
    assert requested == ["clip_02"]
    assert result["clips"][0]["attempts"] == []
    assert result["clips"][1]["selected"]["passed"]
    assert set(ready.current_blocks(directory)) == {"clip_01"}
    assert episode_status(directory, False) == "plan_blocked"
    assert old_final.read_bytes() == b"old preview"


@pytest.mark.parametrize("matches", [True, False])
def test_duration_estimate_does_not_discard_a_matching_passed_video(episode, monkeypatch, matches):
    directory, plan, _ = episode
    r, requested = runner_for(episode, monkeypatch)
    attempt = r.context.work / "clips/clip_01/attempt_01"
    attempt.mkdir(parents=True)
    (attempt / "clip.mp4").write_bytes(b"previously passed")
    (attempt / "asr.json").write_text(json.dumps({"passed": True, "cer": 0, "max_volume_db": -5, "issues": []}))
    (attempt / "request.json").write_text(json.dumps({"duration": 15, "references": [], "reference_sha256": [],
                                                    "prompt": plan["clips"][0]["prompt"] if matches else "another request"}))
    monkeypatch.setattr(r, "assemble", lambda rows: {"thin_passed": True})
    result = r.run()
    assert requested == ["clip_02"]
    if matches:
        assert not result["blocked_clips"] and result["clips"][0]["selected"]["video"] == str(attempt / "clip.mp4")
    else:
        assert result["blocked_clips"] and result["clips"][0]["selected"] is None


def test_reused_passed_video_restores_pruned_audio_before_assembly(episode, monkeypatch):
    from novel_manga.util import media_duration
    directory, plan, _ = episode
    r, requested = runner_for(episode, monkeypatch)
    attempt = directory / "work/clips/clip_01/attempt_01"
    attempt.mkdir(parents=True)
    video = attempt / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "0.3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    record = {"passed": True, "video": str(video), "cer": 0, "max_volume_db": -5, "issues": []}
    (attempt / "asr.json").write_text(json.dumps(record))
    r.context._approved_cached = {"clip_01": record}
    monkeypatch.setattr(r, "analyse_clip", rendering.ThinMediaRunner.analyse_clip.__get__(r))
    result = r.process_clip(plan["clips"][0])
    assert result["selected"]["passed"] and not requested
    assert (attempt / "native.wav").is_file()
    assert abs(media_duration(video) - media_duration(attempt / "native.wav")) < .1


def test_failed_asset_build_only_blocks_clips_with_missing_images(episode, monkeypatch):
    directory, plan, _ = episode
    plan["clips"][0]["seconds_estimate"] = 5
    plan["clips"][0]["references"] = [{"role": "character", "asset_id": "character_001", "path": "missing.jpeg"}]
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    r, requested = runner_for(episode, monkeypatch)
    def failed(**kw):
        raise RuntimeError("card generation did not finish")
    monkeypatch.setattr(r, "build_assets", failed)
    result = r.run()
    assert requested == ["clip_02"]
    assert result["blocked_clips"]["clip_01"][0].startswith("asset:")
    assert ready.read(directory / ready.REPORT)["next_action"]["clip_01"] == "restore_asset"


def batch_for(episode):
    directory, _, _ = episode
    batch = production_flow.Batch.__new__(production_flow.Batch)
    batch.episode_dir = lambda n: directory
    batch.render_status = lambda n: episode_status(directory, False)
    batch.rows, batch.fast, batch.reviewing = {1: {}}, False, False
    batch.args = SimpleNamespace(cache_only=False, no_render=False, dry_run=False, rerender=False, retake_failed=False)
    return batch


def test_all_blocked_spends_no_run_or_asset_or_translation_calls(episode, monkeypatch):
    directory, plan, _ = episode
    plan["clips"] = plan["clips"][:1]
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    batch = batch_for(episode)
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    batch.h3_ready = lambda n: pytest.fail("do not translate an impossible request")
    batch.prepare_cards = lambda n: pytest.fail("do not buy cards for an impossible request")
    production_render.render(batch, 1)
    assert batch.rows[1]["render"] == "plan_blocked"
    assert render_runs(directory) == 0
    assert episode_status(directory, True) == "plan_blocked"


def test_missing_card_is_built_before_admission_and_all_missing_spends_no_run(episode, monkeypatch):
    directory, plan, _ = episode
    plan["clips"] = [clip()]
    plan["clips"][0]["references"] = [{"role": "character", "asset_id": "character_001", "path": "missing.jpeg"}]
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL", raising=False)
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    batch = batch_for(episode)
    builds = []
    batch.prepare_cards = lambda n: builds.append(n)
    production_render.render(batch, 1)
    assert builds == [1] and render_runs(directory) == 0
    assert ready.current_blocks(directory) and not (directory / ".render.lock").exists()
    (directory.parent / "missing.jpeg").write_bytes(b"completed image")
    assert not ready.current_blocks(directory)  # asset arrival unblocks without rewriting the plan


def test_plan_or_script_change_releases_a_waiting_request(episode):
    directory, plan, _ = episode
    ready.save_check(directory, plan, ready.plan_issues(plan))
    assert ready.current_blocks(directory)
    plan["clips"][0]["seconds_estimate"] = 5
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    assert not ready.current_blocks(directory)
    assert not ready.inspect_episode(directory)[1]


def test_last_submission_boundary_refuses_a_missing_image_before_touching_cache(tmp_path):
    r = uninitialized_runner()
    r.context.cache_only, r.context.novel_dir = False, tmp_path
    c = clip()
    c["references"] = [{"role": "character", "path": "missing.jpeg"}]
    with pytest.raises(RuntimeError, match="blocked before generation"):
        r.generate_clip(c, 1)
    assert not list(tmp_path.iterdir())


def test_h3_translation_skips_structurally_blocked_clips(episode, monkeypatch):
    import build_h3_prompts as h3
    from thin_profile import h3_source_digest
    directory, _, _ = episode
    calls = []
    def convert(c, **kwargs):
        calls.append(c["clip_id"])
        c.update(prompt_h3="translated", prompt_h3_of=h3_source_digest(c["prompt"]))
        return True
    monkeypatch.setattr(h3, "convert", convert)
    monkeypatch.setattr(h3.sys, "argv", ["h3", "nov", "--novel-dir", str(directory.parent), "--workers", "1"])
    assert h3.main() == 0 and calls == ["clip_02"]
