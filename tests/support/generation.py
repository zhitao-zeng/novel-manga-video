"""Shared generation regression fixtures."""
from __future__ import annotations
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
from novel_manga.media import cache, generation
from novel_manga.providers import phanrouter_tasks
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service
from dataclasses import replace
import novel_manga.application.production.render as production_render
from support.render_context import uninitialized_runner
import json
import os
import re
import sys
import time
import types
from pathlib import Path
import httpx
import pytest
ROOT = Path(__file__).resolve().parents[2]
import novel_manga.application.packing.context as packer
import novel_manga.application.rendering.h3 as h3prompts
import novel_manga.application.rendering.flow as rc
import novel_manga.application.packing.split as tool
import novel_manga.application.production.flow as production_flow
import novel_manga.review.contracts as review_contracts
import novel_manga.review.policy as review_policy
import novel_manga.review.storage as review_storage
import novel_manga.application.review.episode as review_episode
import novel_manga.application.review.evidence as review_evidence
import novel_manga.application.review.judges as review_judges
from novel_manga.providers import phanrouter  # noqa: E402
from novel_manga.providers.base import ImageResult  # noqa: E402
from novel_manga.providers.h3_pool import PoolUnavailable  # noqa: E402
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.application.profiles import h3_prompt_outdated, h3_source_digest
from novel_manga.application.production.runs import count_run, render_runs
NOVEL = "nov"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(phanrouter_tasks.time, "sleep", lambda _: None)
    monkeypatch.delenv("NOVEL_RESUBMIT_UNCONFIRMED", raising=False)
    for name in ("MAX_CLIP_SECONDS", "SOFT_CUT_SECONDS", "MAX_STAGES"):  # split_long_stages sets these per plan
        monkeypatch.setattr(packer, name, getattr(packer, name))


def provider_with(handler) -> PhanRouterMediaProvider:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.video_ratio, provider.video_resolution = "9:16", "720p"
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


def analysis(video: Path, passed: bool) -> dict:
    return {"passed": passed, "cer": 0.0 if passed else 1.0, "max_volume_db": -3.0, "issues": [] if passed else ["missing"], "video": str(video)}


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
    monkeypatch.setattr(review_episode, "review_models_StoryBible", types.SimpleNamespace(model_validate_json=lambda text: types.SimpleNamespace(characters=[])))
    monkeypatch.setattr(review_judges, "judge_clip", lambda *args: dict(verdict))
    monkeypatch.setattr(review_policy, "compose_feedback", lambda *args, **kwargs: "修正")
    review_episode.review_episode(directory)
    return json.loads((directory / "episode_review.json").read_text(encoding="utf-8"))

