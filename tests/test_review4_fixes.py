"""Regressions from the 2026-09-12 review of branch fix/review-0911: paid submissions sent twice, cache keys that
missed their own takes, English (H3) prompts that carried Chinese the model read out, split parts that lost their
cast, the render-run count, pool waits, and verdicts reused for the wrong take."""
from __future__ import annotations
import packing_context_thin as packing_context
import packing_service_thin as packing_service
from dataclasses import replace
import production_render_thin as production_render

from render_context_support import uninitialized_runner

import json
import os
import re
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import packing_context_thin as packer  # noqa: E402
import build_h3_prompts as h3prompts  # noqa: E402
import render_flow_thin as rc  # noqa: E402
import split_long_stages as tool  # noqa: E402
import production_flow_thin as production_flow  # noqa: E402
import novel_manga.models as review_models
import novel_manga.review.contracts as review_contracts
import novel_manga.review.policy as review_policy
import novel_manga.review.storage as review_storage
import review_episode_thin as review_episode
import review_evidence_thin as review_evidence
import review_judges_thin as review_judges  # noqa: E402
from novel_manga.providers import phanrouter  # noqa: E402
from novel_manga.providers.base import ImageResult  # noqa: E402
from novel_manga.providers.h3_pool import PoolUnavailable  # noqa: E402
from novel_manga.providers.phanrouter import PhanRouterMediaProvider, SubmissionUncertain  # noqa: E402
from thin_profile import h3_prompt_outdated, h3_source_digest  # noqa: E402
from thin_runs import count_run, render_runs  # noqa: E402

NOVEL = "nov"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(phanrouter.time, "sleep", lambda _: None)
    monkeypatch.delenv("NOVEL_RESUBMIT_UNCONFIRMED", raising=False)
    for name in ("MAX_CLIP_SECONDS", "SOFT_CUT_SECONDS", "MAX_STAGES"):  # split_long_stages sets these per plan
        monkeypatch.setattr(packer, name, getattr(packer, name))


def provider_with(handler) -> PhanRouterMediaProvider:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = types.SimpleNamespace(video_model="sd2.5", image_model="gpt-image-2", phanrouter_base_url="https://cloud.test/phanrouter",
                                              request_timeout=30.0, poll_timeout=5.0, inline_reference_images=True)
    provider.video_headers, provider.image_headers = {}, {}
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def episode(tmp_path: Path) -> Path:
    directory = tmp_path / NOVEL / f"{NOVEL}_1"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def long_stage(turns: list[str], index: int = 5, **extra) -> dict:
    stage = {"index": index, "origin_index": index, "location": "大殿", "segment_id": "seg_01", "shot_scale": "中景",
             "visual_prompt": "林凡站在大殿中央", "motion_prompt": "林凡边说边踱步", "end_state": "林凡停下",
             "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "林凡", "text": text} for text in turns]}
    stage.update(extra)
    return stage


# ---------------------------------------------------------------- what a failed submission means (1, 15)
@pytest.mark.parametrize("status", [500, 502, 504, 524])
def test_a_gateway_error_after_the_request_went_through_is_held_not_sent_again(tmp_path, status):
    posts = []

    def handle(request):
        posts.append(1)
        return httpx.Response(status, text="upstream gone")
    provider = provider_with(handle)
    frame = ImageResult(path=tmp_path / "frame.jpeg", public_url="https://media.test/frame.jpeg")
    for _ in range(2):
        with pytest.raises(SubmissionUncertain):
            provider.create_video("女孩回头。", frame, tmp_path / "clip.mp4", duration=8)
    assert len(posts) == 1  # the runner's throttle backoff used to send a 502 again, up to seven times
    assert json.loads((tmp_path / "clip.mp4.task.json").read_text(encoding="utf-8"))["submit_uncertain_at"]


def test_a_refusal_before_any_task_is_asked_again_and_ends_as_a_runtime_error(tmp_path):
    posts = []

    def handle(request):
        posts.append(1)
        return httpx.Response(503, text="busy")
    provider = provider_with(handle)
    with pytest.raises(RuntimeError, match="HTTP 503") as caught:
        provider.create_image("一张角色卡", tmp_path / "card.jpeg")
    assert len(posts) == 3 and not isinstance(caught.value, httpx.HTTPStatusError)  # the card loops catch RuntimeError
    assert not (tmp_path / "card.jpeg.task.json").exists()


# ---------------------------------------------------------------- a hiccup while polling keeps the image task (4)
def test_a_hiccup_while_polling_an_image_keeps_its_task(tmp_path):
    posts, polls = [], []

    def handle(request):
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, json={"task_id": "img-1"})
        if request.url.path.endswith("/v3/images/generations/img-1"):
            polls.append(1)
            if len(polls) == 1:
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://media.test/card.jpeg"}})
        if request.url == httpx.URL("https://media.test/card.jpeg"):
            return httpx.Response(200, content=b"jpeg")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")
    provider = provider_with(handle)
    output = tmp_path / "card.jpeg"
    with pytest.raises(httpx.HTTPStatusError):
        provider.create_image("一张角色卡", output)
    assert json.loads((tmp_path / "card.jpeg.task.json").read_text(encoding="utf-8"))["task_id"] == "img-1"
    provider.create_image("一张角色卡", output)  # the next build picks the same task up again
    assert len(posts) == 1 and output.read_bytes() == b"jpeg"


# ---------------------------------------------------------------- --resubmit-unconfirmed releases what was checked (5)
def test_resubmitting_releases_only_what_went_unconfirmed_before_the_run(monkeypatch):
    started = time.time()
    monkeypatch.setenv("NOVEL_RESUBMIT_UNCONFIRMED", f"{started:.0f}")
    assert not phanrouter.unconfirmed({"submit_uncertain_at": started - 3600})  # someone checked it: may go out again
    assert phanrouter.unconfirmed({"submit_uncertain_at": started + 5})  # went unconfirmed during this run: held
    monkeypatch.delenv("NOVEL_RESUBMIT_UNCONFIRMED")
    assert phanrouter.unconfirmed({"submit_uncertain_at": started - 3600})


# ---------------------------------------------------------------- thin_batch: retakes and held clips (2)
def batch_stub(tmp_path, monkeypatch, statuses, **args) -> production_flow.Batch:
    batch = object.__new__(production_flow.Batch)
    defaults = dict(rerender=False, cache_only=False, retake_failed=False, no_render=False, dry_run=False, workers=0, inflight=4,
                    tier=None, prescreen=False, moderation_repair=True, prune=False, resubmit_unconfirmed=False)
    defaults.update(args)
    batch.args = types.SimpleNamespace(**defaults)
    batch.novel_dir, batch.novel_id, batch.rows = tmp_path / NOVEL, NOVEL, {1: {}}
    batch.reviewing, batch.fast, batch.env, batch.notes, batch.commands = False, True, {}, {}, []
    sequence = iter(statuses)
    monkeypatch.setattr(batch, "render_status", lambda chapter: next(sequence))
    monkeypatch.setattr(batch, "run", lambda command, log_path: (batch.commands.append(command), (0, ""))[1])
    monkeypatch.setattr(batch, "prepare_cards", lambda chapter: None)
    monkeypatch.setattr(batch, "fill_result", lambda chapter: None)
    monkeypatch.setattr(batch, "moderation_blocked", lambda chapter: False)
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL", raising=False)
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    return batch


def paid_episode(tmp_path: Path) -> Path:
    directory = episode(tmp_path)
    plan = {"policy": "thin-clip-plan-v9-15s", "clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "p", "references": [],
             "seconds_estimate": 10, "request_seconds": 10}]}
    (directory / "clip_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "thin_media_report.json").write_text(json.dumps({"gate_failed_clips": ["clip_01"]}), encoding="utf-8")
    return directory


def test_retake_failed_goes_only_to_the_first_call_of_the_approved_retake(tmp_path, monkeypatch):
    paid_episode(tmp_path)
    retake = batch_stub(tmp_path, monkeypatch, ["done_with_warnings", "clips_failed", "clips_failed"], retake_failed=True)
    production_render.render(retake, 1)
    assert ["--retake-failed" in command for command in retake.commands] == [True, False]
    stale = batch_stub(tmp_path, monkeypatch, ["stale", "clips_failed", "clips_failed"], retake_failed=True)
    production_render.render(stale, 1)
    assert len(stale.commands) == 2 and not any("--retake-failed" in command for command in stale.commands)


def test_a_held_submission_can_be_released_past_the_run_limit(tmp_path, monkeypatch):
    directory = paid_episode(tmp_path)
    for _ in range(3):
        count_run(directory)
    record = directory / "work" / "clips" / "clip_01" / "attempt_01" / "clip.mp4.task.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"request_sha256": "x", "submit_uncertain_at": time.time() - 60}), encoding="utf-8")
    plain = batch_stub(tmp_path, monkeypatch, ["clips_failed"])
    production_render.render(plain, 1)
    assert not plain.commands and plain.rows[1]["render"].startswith("gave up")
    released = batch_stub(tmp_path, monkeypatch, ["clips_failed", "done", "done"], resubmit_unconfirmed=True)
    production_render.render(released, 1)
    assert len(released.commands) == 1


# ---------------------------------------------------------------- the runner (3, 6, 8, 10, 14)
def runner(tmp_path: Path, local: str | None = None, **fields) -> rc.ThinMediaRunner:
    r = uninitialized_runner()
    r.context.novel_dir = tmp_path / NOVEL
    r.context.novel_dir.mkdir(parents=True, exist_ok=True)
    r.context.settings = types.SimpleNamespace(local_h3_base_url=local)
    r.context.feedback, r.context.max_attempts, r.context.free_retries = {}, 2, bool(local)
    r.context.work = r.context.novel_dir / f"{NOVEL}_1" / "work"
    r.context.prescreen, r.context.inflight, r.context.moderation_repair, r.context.cache_only = False, 0, True, False
    for key, value in fields.items():
        setattr(r.context, key, value)
    return r


def saved(prompt: str) -> dict:
    return {"prompt": prompt, "references": [], "reference_sha256": [], "duration": 10}


def test_every_wording_a_run_can_choose_is_the_same_clip(tmp_path):
    seedance = runner(tmp_path)
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡擦去嘴角的血。", "request_seconds": 10, "references": []}
    base, soft, compliance = clip["prompt"], rc.soften_prompt(clip["prompt"]), rc.COMPLIANCE_SUFFIX
    for prompt in (base, base + rc.RETRY_SUFFIX, base + rc.RETRY_SUFFIX + compliance, base + compliance,
                   soft, soft + rc.RETRY_SUFFIX, soft + rc.RETRY_SUFFIX + compliance, soft + compliance):
        assert seedance.request_matches(clip, saved(prompt), (), []), prompt[-40:]  # softened + compliance was paid again
    assert not seedance.request_matches(clip, saved("【阶段1】另一段戏。"), (), [])
    h3 = runner(tmp_path, local="pool")
    english = {**clip, "prompt_h3": "subject_definitions:\nNone.\n\ndetailed_description:\n[Shot 1] A man wipes his mouth."}
    assert h3.request_matches(english, saved(english["prompt_h3"] + rc.RETRY_SUFFIX_H3 + " This is take 3."), (), [])
    for prompt in (rc.soften_prompt(english["prompt_h3"]), english["prompt_h3"] + rc.RETRY_SUFFIX, english["prompt_h3"] + compliance):
        assert not h3.request_matches(english, saved(prompt), (), [])  # Chinese H3 reads out: not this clip


def test_prescreen_leaves_an_english_prompt_alone(tmp_path):
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡拔刀。", "prompt_h3": "english", "request_seconds": 10, "references": []}
    assert not runner(tmp_path, local="pool", prescreen=True).prescreens(clip)
    assert runner(tmp_path, prescreen=True).prescreens(clip)


def analysis(video: Path, passed: bool) -> dict:
    return {"passed": passed, "cer": 0.0 if passed else 1.0, "max_volume_db": -3.0, "issues": [] if passed else ["missing"], "video": str(video)}


def test_a_run_without_fresh_takes_looks_past_take_two_at_the_takes_already_made(tmp_path, monkeypatch):
    r = runner(tmp_path, cache_only=True)

    def generate(clip, attempt):
        clip["_generated"] = False
        return tmp_path / f"take{attempt}.mp4"
    monkeypatch.setattr(r, "generate_clip", generate)
    monkeypatch.setattr(r, "analyse_clip", lambda clip, video: analysis(video, video.name == "take3.mp4"))
    monkeypatch.setattr(r, "cached_take", lambda clip, attempt: attempt == 3)
    result = r.process_clip({"clip_id": "clip_01", "kind": "video", "prompt": "p", "request_seconds": 10, "references": []})
    assert [row["passed"] for row in result["attempts"]] == [False, False, True] and result["selected"]["passed"]


def test_a_further_take_that_fails_leaves_the_clip_with_the_takes_it_has(tmp_path, monkeypatch):
    free = runner(tmp_path, local="pool")

    def generate(clip, attempt):
        if attempt >= 3:
            raise PoolUnavailable("no H3 pool instance has a free slot")
        clip["_generated"] = False
        return tmp_path / f"take{attempt}.mp4"
    monkeypatch.setattr(free, "generate_clip", generate)
    monkeypatch.setattr(free, "analyse_clip", lambda clip, video: analysis(video, False))
    result = free.process_clip({"clip_id": "clip_01", "kind": "video", "prompt": "p", "request_seconds": 10, "references": []})
    assert "error" not in result and result["selected"]["video"].endswith("take2.mp4") and "PoolUnavailable" in result["retake_error"]
    rebuild = runner(tmp_path, cache_only=True)

    def missing(clip, attempt):
        if attempt == 2:
            raise rc.CacheMiss("clip_01 attempt 2: not in the cache")
        clip["_generated"] = False
        return tmp_path / "take1.mp4"
    monkeypatch.setattr(rebuild, "generate_clip", missing)
    monkeypatch.setattr(rebuild, "analyse_clip", lambda clip, video: analysis(video, False))
    assert "error" in rebuild.process_clip({"clip_id": "clip_01", "kind": "video", "prompt": "p", "request_seconds": 10, "references": []})


def test_a_full_pool_is_waited_out_like_a_throttled_service():
    assert issubclass(PoolUnavailable, RuntimeError)  # generate_clip's submission loop catches RuntimeError
    assert rc.resubmittable(PoolUnavailable("no H3 pool instance has a free slot"))
    assert rc.resubmittable(RuntimeError("Seedance task submission returned HTTP 429: busy"))
    assert not rc.resubmittable(SubmissionUncertain("task submission unconfirmed (HTTP 502)"))
    assert not rc.resubmittable(RuntimeError("OutputVideoSensitiveContentDetected HTTP 429"))


# ---------------------------------------------------------------- a correction on an English prompt (9)
def test_a_correction_goes_into_the_english_prompt_in_english(tmp_path, monkeypatch):
    note = "林凡的衣服必须是蓝色长袍"
    prompt = "【阶段1】林凡推门走进大殿。【阶段2】林凡抬头看向王座。"
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": prompt, "request_seconds": 10, "references": [],
            "prompt_h3": "english", "prompt_h3_of": h3_source_digest(prompt)}
    assert "【导演修正】" not in runner(tmp_path, local="pool", feedback={"clip_01": note}).clip_base(clip)  # H3 read it out
    assert runner(tmp_path, feedback={"clip_01": note}).clip_base(clip).endswith(f"【导演修正】{note}")  # Seedance keeps it
    assert not h3_prompt_outdated(clip) and h3_prompt_outdated(clip, note)  # a new correction rebuilds the English prompt
    # The correction rides along as one more numbered line of the same ask (a separate ask had the model commenting
    # on the tag list instead of translating), so one answer carries the shots and, last, the note.
    answers = iter([{"shots": ["A man pushes the door open.", "He looks up at the throne.", "The man wears a blue robe."]}])
    monkeypatch.setattr(h3prompts, "ask_json", lambda *args, **kwargs: next(answers))
    fresh = {key: value for key, value in clip.items() if key not in ("prompt_h3", "prompt_h3_of")}
    assert h3prompts.convert(fresh, note=note)
    section = fresh["prompt_h3"].split("summary:\n")[1].split("\n\nretention_analysis")[0]
    assert section.endswith("The man wears a blue robe.") and not re.search("[一-鿿]", section)
    assert fresh["prompt_h3_of"] == h3_source_digest(prompt, note)


# ---------------------------------------------------------------- split parts keep their cast (11)
def test_the_later_parts_of_a_split_stage_keep_the_characters_in_the_picture(monkeypatch):
    monkeypatch.setattr(packing_context, 'MAX_CLIP_SECONDS', 15.0)
    monkeypatch.setattr(packing_context, 'MAX_STAGES', 3)
    three = long_stage(["我们走吧。" * 12] * 3, characters=["林凡", "苏晴", "王长老"],
                       visual_prompt="林凡、苏晴和王长老围坐在桌边", motion_prompt="三人低声交谈")
    parts = packing_service.split_long_shot(three)
    assert len(parts) == 3
    for part in parts:
        raw = {"kind": "video", "location": "大殿", "shots": [part], "seconds": 10}
        assert packing_service.clip_cast(raw) == ["林凡", "苏晴", "王长老"]  # 苏晴 and 王长老 dropped to the background in parts 2-3
    two = packing_service.split_long_shot(long_stage(["我们走吧。" * 12] * 3, characters=["林凡", "苏晴"], visual_prompt="林凡和苏晴站在门口"))
    assert all("仍在画面中" not in part["visual_prompt"] for part in two)  # nobody would drop: the wording stays as it was


def test_rebuilding_parts_keeps_every_part_whose_pictures_stay_the_same(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    (directory / "chapter_script.json").write_text(json.dumps({"shots": []}), encoding="utf-8")
    plan = {"limits": {"max_clip_seconds": 15, "max_stages": 3}, "clips": [
        {"clip_id": "clip_01", "kind": "video", "shot_indexes": [5], "cast": ["林凡"], "references": ["a"],
         "prompt": "repaired wording", "prompt_h3": "english"},
        {"clip_id": "clip_02", "kind": "video", "shot_indexes": [5], "cast": ["林凡"], "references": ["a"], "prompt": "part 2"}],
        "split_long_stages": {"split": {"clip_01": ["clip_01", "clip_02"]}}}
    (directory / "clip_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tool.packing_context, "load_context", lambda episode_dir, bible, tier=None, **kwargs: {"overrides": {}, "compiler_options": replace(tool.packing_context.compiler_options(), **kwargs.get("limits", {}))})
    monkeypatch.setattr(tool.packing_service, "prepared_shots", lambda script, episode_dir, **kwargs: [long_stage(["我们走吧。" * 12] * 2)])
    rebuilt = {"clip_01": (["林凡"], ["a"]), "clip_02": (["林凡", "苏晴"], ["a", "b"])}
    monkeypatch.setattr(tool.packing_service, "clip_entry", lambda raw, clip_id, ctx, override=None: {
        "clip_id": clip_id, "kind": "video", "cast": rebuilt[clip_id][0], "references": rebuilt[clip_id][1], "prompt": f"{clip_id} rebuilt"})
    monkeypatch.setattr(tool.compilation, "plan_totals", lambda clips, shots, ctx: {})
    assert tool.rebuild_parts(directory, "fast", apply=True) == {"rebuilt": 1}
    written = {c["clip_id"]: c for c in json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))["clips"]}
    assert written["clip_01"]["prompt"] == "repaired wording" and written["clip_01"]["prompt_h3"] == "english"
    assert written["clip_02"]["prompt"] == "clip_02 rebuilt"


# ---------------------------------------------------------------- the move is all or nothing (12)
def test_an_interrupted_move_puts_every_clip_back_under_its_old_id(tmp_path):
    directory = episode(tmp_path)
    clips = directory / "work" / "clips"
    for name in ("clip_01", "clip_02", "clip_03"):
        (clips / name / "attempt_01").mkdir(parents=True)
        (clips / name / "attempt_01" / "clip.mp4").write_text(name, encoding="utf-8")
    (clips / "clip_05").write_text("in the way", encoding="utf-8")  # moving clip_03 onto clip_05 fails part-way
    with pytest.raises(OSError):
        tool.rename_clip_dirs(directory, {"clip_01": "clip_01", "clip_03": "clip_05"}, {"clip_02": ["clip_02", "clip_03", "clip_04"]})
    assert sorted(p.name for p in clips.iterdir()) == ["clip_01", "clip_02", "clip_03", "clip_05"]
    assert (clips / "clip_03" / "attempt_01" / "clip.mp4").read_text(encoding="utf-8") == "clip_03"
    (clips / "clip_05").unlink()
    (clips / ".moving-clip_09").mkdir()
    with pytest.raises(RuntimeError, match="interrupted move"):
        tool.rename_clip_dirs(directory, {"clip_01": "clip_01"}, {})
    with pytest.raises(ValueError):
        (directory / "review_feedback.json").write_text("[]", encoding="utf-8")
        tool.remapped(directory / "review_feedback.json", {}, {}, to_parts=False)


# ---------------------------------------------------------------- the run count follows content (13)
def test_the_run_count_follows_what_the_plan_says_not_when_it_was_written(tmp_path):
    directory = episode(tmp_path)
    plan = {"policy": "thin-clip-plan-v9-15s", "clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "p", "references": []}]}
    path = directory / "clip_plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    for _ in range(3):
        count_run(directory)
    path.write_text(json.dumps({**plan, "totals": {"estimated_seconds": 12}}), encoding="utf-8")  # written again, same clips
    stamp = time.time() + 5
    os.utime(path, (stamp, stamp))
    assert render_runs(directory) == 3  # a given-up episode stays given up
    plan["clips"][0]["prompt"] = "another prompt"
    path.write_text(json.dumps(plan), encoding="utf-8")
    os.utime(path, (stamp + 5, stamp + 5))
    assert render_runs(directory) == 0


# ---------------------------------------------------------------- what the review asks to redo is written down
def review_with(tmp_path, monkeypatch, verdict) -> dict:
    directory = episode(tmp_path)
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡推门。", "request_seconds": 10, "references": [], "lines": []}
    (directory / "clip_plan.json").write_text(json.dumps({"policy": "thin-clip-plan-v9-15s", "clips": [clip]}, ensure_ascii=False), encoding="utf-8")
    video = directory / "work" / "clips" / "clip_01" / "attempt_01" / "clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    (directory / "thin_media_report.json").write_text(json.dumps({"clips": [
        {"clip_id": "clip_01", "selected": {"video": str(video), "hypothesis": ""}}]}), encoding="utf-8")
    (tmp_path / NOVEL / "story_bible.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(review_evidence, "load_review_rules", lambda novel_dir: None)
    monkeypatch.setattr(review_models, "StoryBible", types.SimpleNamespace(model_validate_json=lambda text: types.SimpleNamespace(characters=[])))
    monkeypatch.setattr(review_judges, "judge_clip", lambda *args: dict(verdict))
    monkeypatch.setattr(review_policy, "compose_feedback", lambda *args, **kwargs: "修正")
    review_episode.review_episode(directory)
    return json.loads((directory / "episode_review.json").read_text(encoding="utf-8"))


def test_the_review_writes_down_whether_a_failed_clip_must_be_redone(tmp_path, monkeypatch):
    broken = review_with(tmp_path, monkeypatch, {"severity": "fail", "visual_defects": True, "defect_issue": "手指粘连", "identity_ok": True})
    assert broken["clips"]["clip_01"]["tier"] == "must_fix" and broken["feedback"] == {"clip_01": "修正"}
    setting = review_with(tmp_path / "b", monkeypatch, {"severity": "fail", "visual_defects": False, "identity_ok": True,
                                                        "location_ok": False, "defect_issue": "背景是街道，设定是湖面"})
    assert setting["clips"]["clip_01"]["tier"] == "optional" and setting["feedback"] == {}  # differs from the setting, not a redo
    fine = review_with(tmp_path / "c", monkeypatch, {"severity": "pass", "identity_ok": True})
    assert "tier" not in fine["clips"]["clip_01"]


# ---------------------------------------------------------------- a verdict belongs to one take (7)
def test_a_verdict_is_reused_only_for_the_very_same_take(tmp_path, monkeypatch):
    directory = episode(tmp_path)
    videos, clips = {}, []
    for clip_id in ("clip_01", "clip_02"):
        clips.append({"clip_id": clip_id, "kind": "video", "prompt": "【阶段1】林凡推门。", "request_seconds": 10, "references": [], "lines": []})
        video = directory / "work" / "clips" / clip_id / "attempt_01" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"mp4")
        videos[clip_id] = video
    (directory / "clip_plan.json").write_text(json.dumps({"policy": "thin-clip-plan-v9-15s", "clips": clips}, ensure_ascii=False), encoding="utf-8")
    (directory / "thin_media_report.json").write_text(json.dumps({"clips": [
        {"clip_id": clip_id, "selected": {"video": str(video), "hypothesis": ""}} for clip_id, video in videos.items()]}), encoding="utf-8")
    (directory / "episode_review.json").write_text(json.dumps({"policy": review_contracts.POLICY, "clips": {
        "clip_01": {"video": str(videos["clip_01"]), "take": review_storage.take_identity(videos["clip_01"]), "severity": "pass"},
        "clip_02": {"video": str(videos["clip_02"]), "severity": "review_error", "error": "ReadTimeout"}}}), encoding="utf-8")
    # split_long_stages moved another clip's take to this path: the same path, an older mtime, another file
    videos["clip_01"].unlink()
    videos["clip_01"].write_bytes(b"another clip's take")
    os.utime(videos["clip_01"], (time.time() - 86400, time.time() - 86400))
    (tmp_path / NOVEL / "story_bible.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(review_evidence, "load_review_rules", lambda novel_dir: None)
    monkeypatch.setattr(review_models, "StoryBible", types.SimpleNamespace(model_validate_json=lambda text: types.SimpleNamespace(characters=[])))
    judged = []
    monkeypatch.setattr(review_judges, "judge_clip", lambda clip, video, *rest: judged.append(clip["clip_id"]) or {"severity": "pass"})
    review_episode.review_episode(directory)
    assert judged == ["clip_01", "clip_02"]
