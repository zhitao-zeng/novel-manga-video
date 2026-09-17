"""Regressions from the reviews of 723cade/19628e8 and d76e1f5 (issues 22-30): one reading of an episode for
thin_batch and the conductor, reviews before the conductor stops, previews only a person can finish, unconfirmed
submissions, the continuity of split stages, failed H3 rebuilds, corrections after a re-plan."""
from __future__ import annotations
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.flow as packing_flow
import novel_manga.application.packing.service as packing_service
import production_render_thin as production_render

import json
import os
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import conductor_flow_thin as conductor_flow
import conductor_state_thin as conductor_state
import novel_manga.application.production.runs as thin_runs
import production_flow_thin as production_flow  # noqa: E402
from novel_manga.providers.base import ImageResult  # noqa: E402
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.application.profiles import h3_prompt_fingerprint, h3_source_digest, plan_fingerprint

NOVEL = "nov"
H3_KEY = {"name": "h3pool", "model": "minimax-h3", "key_var": "", "base_url": "pool", "clip_cap": 15, "parallel": 1,
          "inflight": {"min": 1, "max": 1, "start": 1}}
PAID_KEY = {**H3_KEY, "name": "sd25", "base_url": "", "key_var": "PHANROUTER_API_KEY"}
CLIP = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡推门走进大殿。", "request_seconds": 10, "references": []}


def episode(tmp_path: Path, n: int = 1) -> Path:
    directory = tmp_path / NOVEL / f"{NOVEL}_{n}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def plan_with(directory: Path, clips: list[dict]) -> dict:
    plan = {"policy": "thin-clip-plan-v9-15s", "clips": clips}
    path = directory / "clip_plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    stamp = time.time() + len(clips) + (path.stat().st_mtime % 7)
    os.utime(path, (stamp, stamp))  # a rewrite in the same instant still reads as a change
    return plan


def report_for(directory: Path, plan: dict, **fields) -> None:
    report = {"clip_plan_fingerprint": plan_fingerprint(plan), "review_feedback": {}, "failed_clips": [],
              "gate_failed_clips": [], "assembly": {"thin_passed": True}}
    report.update(fields)
    (directory / "thin_media_report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (directory / f"{directory.name}.mp4").write_bytes(b"mp4")


def conductor(tmp_path: Path, keys: list[dict] | None = None) -> conductor_flow.Conductor:
    (tmp_path / NOVEL).mkdir(exist_ok=True)
    (tmp_path / NOVEL / "bible_growth.json").write_text(json.dumps({"99": {}}), encoding="utf-8")
    config = {"novel_dir": str(tmp_path / NOVEL), "tmp_dir": str(tmp_path / "conductor"), "keys": keys or [],
              "ranges": [{"chapters": "1-1", "plan_mode": 15}], "qwen": {"urls": []},
              "planning": {"block_size": 1, "blocks_min": 1, "blocks_max": 1, "margin": 0}}
    return conductor_flow.Conductor(config, dry_run=True)


# ---------------------------------------------------------------- 22: one reading of an episode
def test_the_conductor_reads_an_episode_as_thin_batch_does(tmp_path):
    directory = episode(tmp_path)
    clip = {**CLIP, "prompt_h3": "english v1"}
    plan = plan_with(directory, [clip])
    report_for(directory, plan, prompt_h3_fingerprint=h3_prompt_fingerprint(plan))
    free = conductor(tmp_path, [H3_KEY])
    assert conductor_state.chapter(free, 1)["done"]
    plan_with(directory, [{**clip, "prompt_h3": "english v2"}])  # the English prompt changed after the final
    assert not conductor_state.chapter(free, 1)["done"] and 1 in conductor_state.range_stats(free, free.ranges[0])["renderable"]
    (directory / "thin_media_report.json").unlink()  # what a re-plan does
    paid = conductor(tmp_path, [PAID_KEY])
    assert not conductor_state.chapter(paid, 1)["done"] and 1 in conductor_state.range_stats(paid, paid.ranges[0])["renderable"]


# ---------------------------------------------------------------- 23: reviews before stopping
def test_the_conductor_does_not_stop_while_a_final_waits_for_its_review(tmp_path):
    directory = episode(tmp_path)
    report_for(directory, plan_with(directory, [CLIP]))
    c = conductor(tmp_path)
    assert c.tick()  # rendered, not reviewed: a review batch goes out and the conductor carries on
    review = directory / "episode_review.json"
    review.write_text(json.dumps({"policy": thin_runs.REVIEW_POLICY, "clips": {}}), encoding="utf-8")
    later = (directory / f"{NOVEL}_1.mp4").stat().st_mtime + 5
    os.utime(review, (later, later))
    assert not c.tick()


# ---------------------------------------------------------------- 24: a preview a retake cannot fix
def test_a_preview_that_failed_only_a_media_check_waits_for_a_person(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    clip = {**CLIP, "prompt_h3": "english"}
    clip["prompt_h3_of"] = h3_source_digest(clip["prompt"])
    report_for(directory, plan_with(directory, [clip]), assembly={"thin_passed": False})
    free = conductor(tmp_path, [H3_KEY])
    state = conductor_state.chapter(free, 1)
    assert state["blocked"] and not state["done"] and 1 not in conductor_state.range_stats(free, free.ranges[0])["renderable"]
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    batch = object.__new__(production_flow.Batch)
    batch.args = types.SimpleNamespace(rerender=False, cache_only=False, retake_failed=False, no_render=False, dry_run=False, workers=0,
                                       inflight=4, tier=None, prescreen=False, moderation_repair=True, prune=False)
    batch.novel_dir, batch.novel_id, batch.rows = tmp_path / NOVEL, NOVEL, {1: {}}
    batch.reviewing, batch.fast, batch.env, batch.notes, commands = False, True, {}, {}, []
    monkeypatch.setattr(batch, "run", lambda command, log_path: (commands.append(command), (0, ""))[1])
    monkeypatch.setattr(batch, "fill_result", lambda chapter: None)
    production_render.render(batch, 1)
    assert not commands and batch.rows[1]["render"] == "done_with_warnings"  # no re-assembly of the same clips


# ---------------------------------------------------------------- 27: unconfirmed submissions stay held
def provider_with(handler) -> PhanRouterMediaProvider:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.video_ratio, provider.video_resolution = "9:16", "720p"
    provider.settings = types.SimpleNamespace(video_model="sd2.5", image_model="gpt-image-2", phanrouter_base_url="https://cloud.test/phanrouter",
                                              request_timeout=30.0, poll_timeout=5.0, inline_reference_images=True)
    provider.video_headers, provider.image_headers = {"Authorization": "Bearer runtime-secret"}, {}
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def test_an_unconfirmed_video_submission_stays_held_until_someone_allows_it(tmp_path, monkeypatch):
    posts = []

    def handle(request):
        if request.method == "POST":
            posts.append(1)
            if len(posts) == 1:
                raise httpx.ReadTimeout("answer lost", request=request)
            return httpx.Response(200, json={"task_id": "cgt-2"})
        if request.url.path.endswith("/tasks/cgt-2"):
            return httpx.Response(200, json={"code": "success", "data": {"task_id": "cgt-2", "status": "succeeded", "model": "sd2.5",
                                                                        "url": "https://media.test/r.mp4", "output_format": "mp4", "error": None}})
        if request.url == httpx.URL("https://media.test/r.mp4"):
            return httpx.Response(200, content=b"mp4")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setattr("novel_manga.providers.phanrouter_tasks.time.sleep", lambda _: None)
    monkeypatch.delenv("NOVEL_RESUBMIT_UNCONFIRMED", raising=False)
    provider = provider_with(handle)
    frame = ImageResult(path=tmp_path / "frame.jpeg", public_url="https://media.test/frame.jpeg")
    call = lambda: provider.create_video("女孩回头。", frame, tmp_path / "clip.mp4", duration=8)  # noqa: E731
    with pytest.raises(SubmissionUncertain):
        call()
    record_path = tmp_path / "clip.mp4.task.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["submit_uncertain_at"] -= 6 * 3600  # hours later: still not sent again on its own
    record_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SubmissionUncertain, match="--resubmit-unconfirmed"):
        call()
    assert len(posts) == 1
    monkeypatch.setenv("NOVEL_RESUBMIT_UNCONFIRMED", "1")  # someone has checked the bill
    call()
    assert len(posts) == 2 and (tmp_path / "clip.mp4").read_bytes() == b"mp4"


def test_an_unconfirmed_image_submission_is_recorded_and_held(tmp_path, monkeypatch):
    posts = []

    def handle(request):
        posts.append(1)
        raise httpx.ReadTimeout("answer lost", request=request)

    monkeypatch.setattr("novel_manga.providers.phanrouter_tasks.time.sleep", lambda _: None)
    monkeypatch.delenv("NOVEL_RESUBMIT_UNCONFIRMED", raising=False)
    provider = provider_with(handle)
    for _ in range(2):
        with pytest.raises(SubmissionUncertain):
            provider.create_image("一张角色卡", tmp_path / "card.jpeg")
    assert len(posts) == 1
    assert json.loads((tmp_path / "card.jpeg.task.json").read_text(encoding="utf-8"))["submit_uncertain_at"]


# ---------------------------------------------------------------- 28: continuity of a split stage
def test_the_later_parts_of_a_split_stage_carry_on_instead_of_repeating_its_action(monkeypatch):
    monkeypatch.setattr(packing_context, 'MAX_CLIP_SECONDS', 15.0)
    shot = {"index": 5, "location": "屋内", "segment_id": "seg_01", "shot_scale": "中景", "visual_prompt": "林凡站在屋内门边",
            "motion_prompt": "林凡推门走出去", "end_state": "林凡站在门外台阶上",
            "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "林凡", "text": "我们走吧。" * 12}] * 3}
    parts = packing_service.split_long_shot(shot)
    assert len(parts) == 3 and parts[0]["motion_prompt"] == "林凡推门走出去" and parts[0]["visual_prompt"] == "林凡站在屋内门边"
    for part in parts[1:]:
        assert "推门" not in part["motion_prompt"] and "林凡站在门外台阶上" in part["visual_prompt"]
    assert all(part["end_state"] == "林凡站在门外台阶上" for part in parts)


# ---------------------------------------------------------------- 29: a forced H3 rebuild that fails
def test_a_forced_rebuild_that_fails_is_reported_as_a_failure(tmp_path, monkeypatch):
    for name in ("QWEN38_LOCAL_BASE_URL", "QWEN38_LOCAL_MODEL", "QWEN38_LOCAL_API_KEY_VAR"):
        monkeypatch.setenv(name, "unused")
    import novel_manga.application.rendering.h3 as build_h3_prompts
    directory = episode(tmp_path)
    clip = {**CLIP, "prompt_h3": "old english"}
    clip["prompt_h3_of"] = h3_source_digest(clip["prompt"])
    plan_with(directory, [clip])

    def down(*args, **kwargs):
        raise TimeoutError("translation server down")
    monkeypatch.setattr(build_h3_prompts, "ask_json", down)
    monkeypatch.setattr(sys, "argv", ["build_h3_prompts.py", NOVEL, "--novel-dir", str(tmp_path / NOVEL), "--rebuild", "--workers", "1"])
    assert build_h3_prompts.main() == 1
    assert json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))["clips"][0]["prompt_h3"] == "old english"


# ---------------------------------------------------------------- 30: corrections after a re-plan
def test_corrections_follow_their_clip_through_a_new_plan(tmp_path):
    feedback = tmp_path / "review_feedback.json"
    feedback.write_text(json.dumps({"clip_01": "甲的修正", "clip_02": "乙穿红衣，不要出现甲"}, ensure_ascii=False), encoding="utf-8")
    old = {"clips": [{"clip_id": "clip_01", "prompt": "拍甲"}, {"clip_id": "clip_02", "prompt": "拍乙"}]}
    new = {"clips": [{"clip_id": "clip_01", "prompt": "新的开场"}, {"clip_id": "clip_02", "prompt": "拍甲"},
                     {"clip_id": "clip_03", "prompt": "乙换了一种拍法"}]}
    dropped = packing_flow.carry_corrections(old, new, feedback)
    assert json.loads(feedback.read_text(encoding="utf-8")) == {"clip_02": "甲的修正"}
    assert dropped == {"clip_02": "乙穿红衣，不要出现甲"} and list(tmp_path.glob("review_feedback.set-aside-*.json"))


# ---------------------------------------------------------------- repair batches draw no backlog of cards
def test_a_repair_batch_builds_only_the_cards_its_episode_references(tmp_path, monkeypatch):
    import novel_manga.application.assets.recurring as recurring_cards_thin
    directory = episode(tmp_path)
    plan_with(directory, [{**CLIP, "references": [{"role": "character", "asset_id": "character_001", "path": "x.jpeg"}]}])
    monkeypatch.setattr(recurring_cards_thin, "recurring_without_cards", lambda novel_dir: [("甲", "character_099", 2)])

    def prepared(no_recurring: bool) -> set:
        batch = object.__new__(production_flow.Batch)
        batch.args = types.SimpleNamespace(no_recurring_cards=no_recurring)
        batch.novel_dir, batch.novel_id, batch.rows = tmp_path / NOVEL, NOVEL, {1: {}}
        wanted: set = set()
        batch.cards = types.SimpleNamespace(want=wanted.update, wait=lambda ids: [{"asset_id": i, "status": "built"} for i in ids])
        batch.prepare_cards(1)
        return wanted
    assert prepared(False) == {"character_001", "character_099"}
    assert prepared(True) == {"character_001"}
