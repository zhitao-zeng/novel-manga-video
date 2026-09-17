"""Regressions from the 2026-09-11 review of the Seedance lines and the packer (issues 16-21), and the
cache-only rebuild that puts the dropped title cards back into finished episodes."""
from __future__ import annotations
import packing_context_thin as packing_context
import packing_service_thin as packing_service
import conductor_dispatch_thin as conductor_dispatch
import conductor_workers_thin as conductor_workers
import production_render_thin as production_render

from render_context_support import uninitialized_runner

import base64
import io
import json
import sys
import threading
import time
import types
from pathlib import Path

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from novel_manga.story.compilation import ClipCompiler  # noqa: E402
import conductor_flow_thin as conductor_flow  # noqa: E402
import planner_context_thin as planner_context  # noqa: E402
import render_flow_thin as rc  # noqa: E402
import production_flow_thin as production_flow  # noqa: E402
from novel_manga.config import DEFAULT_FONT_PATH  # noqa: E402
from novel_manga.providers.base import ImageResult  # noqa: E402
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from thin_profile import plan_fingerprint  # noqa: E402
from thin_runs import render_runs  # noqa: E402

NOVEL = "nov"


# ---------------------------------------------------------------- 16: reference pictures keep their frame
def provider_with(handler=None) -> PhanRouterMediaProvider:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.video_ratio, provider.video_resolution = "9:16", "720p"
    provider.settings = types.SimpleNamespace(video_model="sd2.5", phanrouter_base_url="https://cloud.test/phanrouter",
                                              request_timeout=30.0, poll_timeout=5.0, inline_reference_images=True)
    provider.video_headers = {"Authorization": "Bearer runtime-secret"}
    provider.image_headers = {}
    provider.client = httpx.Client(transport=httpx.MockTransport(handler or (lambda request: httpx.Response(500))))
    return provider


@pytest.mark.parametrize("size, sent", [((2048, 1152), (1280, 720)), ((1152, 2048), (720, 1280))])
def test_a_reference_card_goes_out_whole(tmp_path, size, sent):
    card = tmp_path / "card.jpeg"
    Image.new("RGB", size, (40, 90, 160)).save(card)
    url = provider_with()._restore_image_url(ImageResult(path=card, public_url=None))
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert image.size == sent  # a 16:9 scene card used to be cut to its middle 9:16 third


# ---------------------------------------------------------------- 19: no second paid task for a lost answer
def submit_clip(provider: PhanRouterMediaProvider, tmp_path: Path):
    frame = ImageResult(path=tmp_path / "frame.jpeg", public_url="https://media.test/frame.jpeg")
    return provider.create_video("女孩听见脚步声后回头。", frame, tmp_path / "clip.mp4", duration=8)


def test_a_submission_whose_answer_was_lost_is_not_sent_again(tmp_path, monkeypatch):
    posts = []

    def handle(request):
        if request.method == "POST":
            posts.append(1)
            raise httpx.ReadTimeout("answer lost", request=request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setattr("novel_manga.providers.phanrouter_tasks.time.sleep", lambda _: None)
    provider = provider_with(handle)
    with pytest.raises(SubmissionUncertain):
        submit_clip(provider, tmp_path)
    assert len(posts) == 1
    record = json.loads((tmp_path / "clip.mp4.task.json").read_text(encoding="utf-8"))
    assert record["submit_uncertain_at"] and "task_id" not in record
    with pytest.raises(SubmissionUncertain, match="not sent again"):
        submit_clip(provider, tmp_path)  # a later call waits instead of creating a second task
    assert len(posts) == 1


def test_a_submission_that_never_connected_is_sent_again(tmp_path, monkeypatch):
    posts = []

    def handle(request):
        if request.method == "POST":
            posts.append(1)
            if len(posts) == 1:
                raise httpx.ConnectError("no route to host", request=request)
            return httpx.Response(200, json={"task_id": "cgt-1"})
        if request.url.path.endswith("/tasks/cgt-1"):
            return httpx.Response(200, json={"code": "success", "data": {
                "task_id": "cgt-1", "status": "succeeded", "model": "sd2.5", "url": "https://media.test/result.mp4",
                "output_format": "mp4", "error": None}})
        if request.url == httpx.URL("https://media.test/result.mp4"):
            return httpx.Response(200, content=b"seedance-mp4")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setattr("novel_manga.providers.phanrouter_tasks.time.sleep", lambda _: None)
    submit_clip(provider_with(handle), tmp_path)
    assert len(posts) == 2 and (tmp_path / "clip.mp4").read_bytes() == b"seedance-mp4"


# ---------------------------------------------------------------- 17: a stage too long for one clip
def long_stage(turns: list[str]) -> dict:
    return {"index": 5, "origin_index": 5, "location": "大殿", "segment_id": "seg_01", "shot_scale": "中景",
            "visual_prompt": "林凡站在大殿中央", "motion_prompt": "林凡边说边踱步", "end_state": "林凡停下",
            "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "林凡", "text": text} for text in turns]}


def test_a_stage_too_long_for_one_clip_is_split_between_its_lines(monkeypatch):
    monkeypatch.setattr(packing_context, 'MAX_CLIP_SECONDS', 15.0)
    monkeypatch.setattr(packing_context, 'MAX_STAGES', 3)
    shot = long_stage(["我们走吧。" * 12] * 6)
    assert packing_service.shot_seconds(shot) > 60
    compiler = ClipCompiler(packing_context.compiler_options())
    clips = compiler.pack([shot])
    assert len(clips) == 6 and all(clip["seconds"] <= 15.0 for clip in clips)
    spoken = [turn["text"] for clip in clips for stage in clip["shots"] for turn in stage["turns"]]
    assert spoken == [turn["text"] for turn in shot["turns"]]  # every line, in order, none clamped away
    assert any(d["kind"] == "split_stage" for d in compiler.decisions)
    assert clips[1]["shots"][0]["visual_prompt"] == "承接上一段结束时的画面：林凡停下"


def test_a_line_longer_than_a_clip_is_cut_at_sentence_ends(monkeypatch):
    monkeypatch.setattr(packing_context, 'MAX_CLIP_SECONDS', 15.0)
    monkeypatch.setattr(packing_context, 'MAX_STAGES', 3)
    text = "这一句话有十个字符吗。" * 20
    clips = packing_service.pack([long_stage([text])])
    pieces = [turn["text"] for clip in clips for stage in clip["shots"] for turn in stage["turns"]]
    assert "".join(pieces) == text and all(piece.endswith("。") for piece in pieces)
    assert len(pieces) > 1 and all(clip["seconds"] <= 15.0 for clip in clips)


# ---------------------------------------------------------------- 18: title cards
def title_runner(tmp_path: Path, plan: dict, monkeypatch) -> rc.ThinMediaRunner:
    r = uninitialized_runner()
    r.context.work, r.context.clip_plan = tmp_path / "work", plan
    r.context.settings = types.SimpleNamespace(width=1280, height=720, fps=25, font_path=DEFAULT_FONT_PATH)
    r.context.renderer = types.SimpleNamespace(mux_visual_group=lambda video, wav, out: (out, 5.0),
                                       _silent_card_segment=lambda image, out, seconds: out)
    monkeypatch.setattr("novel_manga.media.postprocess.chat_segments", lambda *args: [])
    monkeypatch.setattr("novel_manga.media.subtitles.subtitle_events", lambda *args: [])
    monkeypatch.setattr("novel_manga.media.postprocess.frame", lambda *args: None)  # no backdrop: the card is drawn on a dark ground
    return r


def test_the_plans_title_cards_are_cut_in_where_the_plan_puts_them(tmp_path, monkeypatch):
    if not Path(DEFAULT_FONT_PATH).is_file():
        pytest.skip("no CJK font on this machine")
    plan = {"clips": [{"clip_id": "clip_01", "kind": "video"},
                      {"clip_id": "clip_02", "kind": "title_card", "text": "二十年后", "request_seconds": 3},
                      {"clip_id": "clip_03", "kind": "video"}]}
    r = title_runner(tmp_path, plan, monkeypatch)
    results = [{"clip_id": c, "selected": {"video": str(tmp_path / f"{c}.mp4")}} for c in ("clip_01", "clip_03")]
    segments = r.story_segments(results)
    assert [(s["unit_id"], s["role"]) for s in segments] == [("clip_01", "dialogue"), ("clip_02", "title"), ("clip_03", "dialogue")]
    assert segments[1]["duration"] == 3.0 and segments[1]["subtitle_events"] == []
    with Image.open(tmp_path / "work" / "titles" / "clip_02.jpeg") as image:
        assert image.size == (1280, 720)


# ---------------------------------------------------------------- the cache-only rebuild
def test_cache_only_never_generates_a_clip(tmp_path):
    r = uninitialized_runner()
    r.context.novel_dir, r.context.work = tmp_path / NOVEL, tmp_path / NOVEL / "work"
    r.context.settings = types.SimpleNamespace(local_h3_base_url=None)
    r.context.feedback, r.context.prescreen, r.context.cache_only = {}, False, True

    class Provider:
        def create_video(self, *args, **kwargs):
            raise AssertionError("cache-only generated a clip")
    r.context.provider = Provider()
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡走进大殿。", "request_seconds": 10, "references": []}
    with pytest.raises(rc.CacheMiss):
        r.generate_clip(clip, 1)


def test_thin_batch_cache_only_rebuilds_without_cards_or_a_run(tmp_path, monkeypatch):
    directory = tmp_path / NOVEL / f"{NOVEL}_1"
    directory.mkdir(parents=True)
    plan = {"policy": "thin-clip-plan-v9-15s", "clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "p", "references": []}]}
    (directory / "clip_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "thin_media_report.json").write_text(json.dumps({"clip_plan_fingerprint": plan_fingerprint(plan), "review_feedback": {},
                                                                  "failed_clips": [], "gate_failed_clips": [], "assembly": {"thin_passed": True}}), encoding="utf-8")
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL", raising=False)
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    batch = object.__new__(production_flow.Batch)
    batch.args = types.SimpleNamespace(rerender=True, cache_only=True, no_render=False, dry_run=False, workers=0, inflight=4, tier=None,
                                       prescreen=False, moderation_repair=True, prune=False, retake_failed=False)
    batch.novel_dir, batch.novel_id, batch.rows = tmp_path / NOVEL, NOVEL, {1: {}}
    batch.reviewing, batch.fast, batch.env, batch.notes = False, True, {}, {}
    commands, cards = [], []
    monkeypatch.setattr(batch, "run", lambda command, log_path: (commands.append(command), (0, ""))[1])
    monkeypatch.setattr(batch, "prepare_cards", lambda chapter: cards.append(chapter))
    monkeypatch.setattr(batch, "fill_result", lambda chapter: None)
    production_render.render(batch, 1)
    assert len(commands) == 1 and "--cache-only" in commands[0]
    assert not cards and render_runs(directory) == 0


# ---------------------------------------------------------------- 20: appearances of a re-planned chapter
def test_a_replanned_chapter_replaces_its_appearances(tmp_path):
    novel = tmp_path / NOVEL
    novel.mkdir()
    planner_context.record_cast(novel, 5, ["林凡", "苏清"], ["大殿"])
    planner_context.record_cast(novel, 6, ["苏清"], ["山门"])
    planner_context.record_cast(novel, 5, ["林凡"], ["山门"])  # chapter 5 re-written without 苏清 and the hall
    index = json.loads((novel / "cast_index.json").read_text(encoding="utf-8"))
    assert index["characters"] == {"林凡": [5], "苏清": [6]}
    assert index["locations"] == {"山门": [5, 6]}


# ---------------------------------------------------------------- 21: --plan-parallel
def test_the_plan_stage_plans_chapters_side_by_side_and_checkpoints_in_order(monkeypatch):
    batch = object.__new__(production_flow.Batch)
    batch.args = types.SimpleNamespace(plan_parallel=3)
    running, peak, order, lock = [0], [0], [], threading.Lock()

    def plan(chapter):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
    batch.grow, batch.plan = (lambda chapter: None), plan
    import production_reports_thin
    monkeypatch.setattr(production_reports_thin, "volume_checkpoint", lambda batch, chapter, chapters: order.append(chapter))
    batch.plan_stage([1, 2, 3, 4, 5, 6])
    assert peak[0] == 3 and order == [1, 2, 3, 4, 5, 6]


def test_the_conductor_fills_a_servers_slots_with_blocks_not_parallel_chapters(tmp_path, monkeypatch):
    (tmp_path / NOVEL).mkdir()
    (tmp_path / NOVEL / "bible_growth.json").write_text(json.dumps({"99": {}}), encoding="utf-8")
    config = {"novel_dir": str(tmp_path / NOVEL), "tmp_dir": str(tmp_path / "conductor"), "keys": [],
              "ranges": [{"chapters": "1-6", "plan_mode": 15}], "qwen": {"urls": []},
              "planning": {"block_size": 3, "blocks_min": 1, "blocks_max": 2, "margin": 0,
                           "models": [{"model": "m", "base": "http://127.0.0.1:9/v1", "slots": 2}]}}
    c = conductor_flow.Conductor(config, dry_run=False)
    commands = []
    monkeypatch.setattr(conductor_workers, "spawn", lambda conductor, name, command, extra=None: commands.append(command))
    conductor_dispatch.tick_planning(c, congested=False)
    assert len(commands) == 2 and all("--plan-parallel" not in command for command in commands)
