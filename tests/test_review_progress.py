from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from novel_manga.application.dashboard.review_progress import viewer_progress
from novel_manga.application.profiles import plan_fingerprint


def write(path, data, stamp):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.utime(path, (stamp, stamp))


def fixture(tmp_path):
    novel = tmp_path / "nov"
    episode = novel / "nov_1"
    video = episode / "work/clips/clip_01/attempt_01/clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    stamp = time.time() - 1000
    os.utime(video, (stamp, stamp))
    plan = {"clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "a scene", "request_seconds": 15}]}
    write(episode / "clip_plan.json", plan, stamp)
    media = {"clip_plan_fingerprint": plan_fingerprint(plan), "assembly": {"thin_passed": True},
             "clips": [{"clip_id": "clip_01", "selected": {"video": str(video)}}]}
    write(episode / "thin_media_report.json", media, stamp + 10)
    (episode / "nov_1.mp4").write_bytes(b"final")
    write(novel / "second_review_final.json", [{"episode": "nov_1", "clip": "clip_01", "votes": 2,
                                               "kind": "身体结构错误", "saw": "旧视频多一只手"}], stamp + 20)
    return novel, episode, video, plan, media, stamp


def verdict(novel, stamp, *, repaired=False, score=0, broken=False, saw="画面正常"):
    base = novel / "second_review"
    look = base / "verify99/repaired/look" if repaired else base / "look/local"
    read = base / "verify99/repaired/read" if repaired else base / "read/local"
    row = {"episode": "nov_1", "clip": "clip_01", "wrong_out_of_100": score, "saw": saw, "kind": "身体结构错误"}
    write(look / "nov_1__clip_01.json", row, stamp)
    write(read / "nov_1__clip_01.json", {**row, "read": broken}, stamp + 1)


def test_repair_verdict_replaces_old_fault_without_rewriting_card_review(tmp_path):
    novel, episode, video, plan, media, stamp = fixture(tmp_path)
    verdict(novel, stamp + 30, score=90, broken=True, saw="多一只手")
    verdict(novel, stamp + 60, repaired=True)
    write(episode / "episode_review.json", {"clips": {"clip_01": {"severity": "fail"}}}, stamp + 70)
    result = viewer_progress(novel, False)
    assert result["baseline_confirmed"] == 1 and result["counts"] == {"clear": 1}
    assert result["repair_counts"] == {"clear": 1}
    assert json.loads((episode / "episode_review.json").read_text())["clips"]["clip_01"]["severity"] == "fail"
    os.utime(video, (stamp + 100, stamp + 100))
    assert viewer_progress(novel, False)["counts"] == {"needs_review": 1}


def test_review_of_attempt_one_cannot_clear_a_final_using_attempt_two(tmp_path):
    novel, episode, video, plan, media, stamp = fixture(tmp_path)
    verdict(novel, stamp + 60, repaired=True)
    second = video.parent.parent / "attempt_02/clip.mp4"
    second.parent.mkdir(); second.write_bytes(b"another take")
    media["clips"][0]["selected"]["video"] = str(second)
    write(episode / "thin_media_report.json", media, stamp + 80)
    result = viewer_progress(novel, False)
    assert result["counts"] == {"needs_review": 1} and "其他片段" in result["rows"][0]["reason"]


def test_unfinished_or_unmatched_read_pass_is_pending(tmp_path):
    novel, episode, video, plan, media, stamp = fixture(tmp_path)
    verdict(novel, stamp + 60)
    read = novel / "second_review/read/local/nov_1__clip_01.json"
    write(read, {"read": False, "saw": "对另一个画面的旧描述"}, stamp + 61)
    assert viewer_progress(novel, False)["counts"] == {"needs_review": 1}
    read.unlink()
    assert viewer_progress(novel, False)["counts"] == {"needs_review": 1}


def test_still_flagged_and_one_vote_are_not_cleared(tmp_path):
    novel, episode, video, plan, media, stamp = fixture(tmp_path)
    verdict(novel, stamp + 60, repaired=True, score=90, broken=True)
    assert viewer_progress(novel, False)["repair_counts"] == {"confirmed": 1}
    verdict(novel, stamp + 80, repaired=True, score=90, broken=False)
    assert viewer_progress(novel, False)["repair_counts"] == {"one_vote": 1}


def test_renumbered_intact_clip_keeps_review_but_split_clip_does_not(tmp_path):
    novel, episode, video, plan, media, stamp = fixture(tmp_path)
    verdict(novel, stamp + 60, repaired=True)
    old_directory = video.parent.parent
    old_directory.rename(old_directory.with_name("clip_02"))
    video = episode / "work/clips/clip_02/attempt_01/clip.mp4"
    plan["clips"][0]["clip_id"] = "clip_02"
    plan["split_long_stages"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp + 100)),
                                 "renamed": {"clip_01": "clip_02"}, "split": {}}
    media["clip_plan_fingerprint"] = plan_fingerprint(plan)
    media["clips"] = [{"clip_id": "clip_02", "selected": {"video": str(video)}}]
    write(episode / "clip_plan.json", plan, stamp + 100)
    write(episode / "thin_media_report.json", media, stamp + 110)
    result = viewer_progress(novel, False)
    assert result["counts"] == {"clear": 1} and result["rows"][0]["clip"] == "clip_02"
    plan["split_long_stages"]["split"] = {"clip_01": ["clip_01", "clip_02"]}
    write(episode / "clip_plan.json", plan, stamp + 120)
    assert viewer_progress(novel, False)["counts"] == {"needs_review": 1}


def test_absent_secondary_reviews_are_not_a_perfect_score(tmp_path):
    novel, episode, video, plan, media, stamp = fixture(tmp_path)
    assert viewer_progress(novel, False)["counts"] == {"needs_review": 1}
    (novel / "second_review_final.json").unlink()
    assert viewer_progress(novel, False) is None
