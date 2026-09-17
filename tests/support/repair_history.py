"""Shared repair history regression fixtures."""
import novel_manga.application.packing.service as packing_service
import novel_manga.repair.execution as repair_execution
import copy
import json
from pathlib import Path
import pytest
import novel_manga.util as utils
import novel_manga.application.repair.delivery as delivery
import novel_manga.application.repair.history as history
from novel_manga.llm import client as model_client
import novel_manga.review.policy as review_policy
import novel_manga.review.storage as review_storage
import novel_manga.application.review.evidence as review_evidence
from novel_manga.application.review.store import current_takes
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.production.runs import REVIEW_POLICY, episode_status


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def episode(tmp_path):
    directory = tmp_path / "nov" / "nov_1"
    directory.mkdir(parents=True)
    video = directory / "work/clips/clip_01/attempt_01/clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"original take")
    write(video.parent / "request.json", {"prompt": "original request"})
    write(video.parent / "asr.json", {"passed": True})
    plan = {"clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "original request", "request_seconds": 8, "references": []}]}
    take = review_storage.take_identity(video)
    review = {"policy": REVIEW_POLICY, "clips": {"clip_01": {"video": str(video), "take": take,
              "story_ok": False, "verify": {"verdict": "obvious", "same_person_twice": True,
                                            "evidence": "two copies of the lead", "instruction": "one lead"}}},
              "feedback": {"clip_01": "one lead"}}
    media = {"clip_plan_fingerprint": plan_fingerprint(plan), "review_feedback": {}, "failed_clips": [], "gate_failed_clips": [],
             "clips": [{"clip_id": "clip_01", "selected": {"video": str(video), "passed": True}}],
             "assembly": {"final_video": str(directory / "nov_1.mp4"), "thin_passed": True}}
    for name, value in (("clip_plan.json", plan), ("episode_review.json", review), ("thin_media_report.json", media)):
        write(directory / name, value)
    (directory / "nov_1.mp4").write_bytes(b"incumbent movie")
    return directory, plan, review, media


def candidate_episode(episode):
    directory, plan, review, media = episode
    history.begin_trial(directory, {"clip_01"}, "rewrite")
    out = delivery.assembly_directory(directory)
    candidate = out / "nov_1.mp4"
    candidate.write_bytes(b"new candidate")
    media["assembly"].update(final_video=str(candidate), pending_publish=True)
    write(directory / "thin_media_report.json", media)
    takes = current_takes(directory, plan, review)
    return directory, plan, review, media, takes

