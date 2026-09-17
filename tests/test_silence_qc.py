from __future__ import annotations

import array
import json
import math
import os
import sys
import wave
from pathlib import Path

import pytest

from novel_manga.qc import inspect_silence  # noqa: E402
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.production.runs import episode_status
from novel_manga.application.repair.recheck_silence import recorded_outro, recheck_episode


def audio(path: Path, parts: list[tuple[float, bool]]) -> Path:
    """Real synthetic PCM: tone and exact silent intervals, no model or ASR dependency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array.array("h")
    for seconds, sound in parts:
        samples.extend(int(6000 * math.sin(2 * math.pi * 440 * i / 8000)) if sound else 0 for i in range(round(seconds * 8000)))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    return path


def test_silent_outro_is_excluded_without_losing_the_story_tail(tmp_path):
    video = audio(tmp_path / "audio.wav", [(9.2, True), (4.8, False)])
    old = inspect_silence(video, 14)
    new = inspect_silence(video, 14, silent_outro_seconds=4)
    assert old["long_silence"]["passed"] is False
    assert new["long_silence"]["passed"] is True
    assert new["long_silence"]["detail"]["max_silence_seconds"] == pytest.approx(0.8, abs=.01)
    assert new["silence_ratio"]["detail"]["duration_seconds"] == 10
    assert new["silence_ratio"]["detail"]["ratio"] == pytest.approx(.08, abs=.001)


def test_long_silence_in_story_still_fails_even_when_touching_outro(tmp_path):
    video = audio(tmp_path / "audio.wav", [(5, True), (9, False)])
    result = inspect_silence(video, 14, silent_outro_seconds=4)
    assert result["long_silence"]["passed"] is False
    assert result["long_silence"]["detail"]["max_silence_seconds"] == pytest.approx(5, abs=.01)
    assert result["silence_ratio"]["passed"] is False


def test_story_silence_ratio_uses_story_duration_not_whole_video(tmp_path):
    video = audio(tmp_path / "audio.wav", [(3, False), (6, True), (5, False)])
    result = inspect_silence(video, 14, silent_outro_seconds=4)
    assert result["long_silence"]["passed"] is True  # longest story silence is just 3 seconds
    assert result["silence_ratio"]["passed"] is False
    assert result["silence_ratio"]["detail"]["ratio"] == pytest.approx(.4, abs=.001)


def episode(tmp_path, *, black=False, speech=False):
    directory = tmp_path / "nov" / "nov_1"
    video = audio(directory / "nov_1.mp4", [(9.2, True), (4.8, False)])
    plan = {"clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "scene"}]}
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    checks = {**inspect_silence(video, 14), "black_frames": {"passed": not black, "detail": {"max_black_seconds": 2 if black else 0}},
              "long_freeze": {"passed": False, "detail": {"max_freeze_seconds": 4}}, "audio_level": {"passed": True}}
    data = {"clip_plan_fingerprint": plan_fingerprint(plan), "review_feedback": {}, "failed_clips": [],
            "gate_failed_clips": ["clip_01"] if speech else [], "clips": [{"clip_id": "clip_01", "selected": {"passed": not speech}}],
            "assembly": {"duration": 14, "thin_passed": False, "silent_outro_seconds": 4,
                         "media_qc": {"passed": False, "checks": checks}}}
    (directory / "thin_media_report.json").write_text(json.dumps(data))
    (directory / "media_qc_report.json").write_text(json.dumps(data["assembly"]["media_qc"]))
    return directory, data


@pytest.mark.parametrize("black,speech,expected", [(False, False, "done"), (True, False, "done_with_warnings"), (False, True, "done_with_warnings")])
def test_recheck_updates_only_silence_and_preserves_video_and_other_verdicts(tmp_path, black, speech, expected):
    directory, old = episode(tmp_path, black=black, speech=speech)
    video = directory / "nov_1.mp4"
    before = video.read_bytes(), video.stat().st_mtime_ns
    archive = tmp_path / "backup"
    preview = recheck_episode(directory, apply=False, archive=archive)
    assert preview["after"] == expected and not archive.exists()
    assert episode_status(directory, True) == "done_with_warnings"
    result = recheck_episode(directory, apply=True, archive=archive)
    new = json.loads((directory / "thin_media_report.json").read_text())
    assert result["after"] == episode_status(directory, True) == expected
    assert (video.read_bytes(), video.stat().st_mtime_ns) == before
    assert new["clips"] == old["clips"] and new["gate_failed_clips"] == old["gate_failed_clips"]
    assert new["assembly"]["media_qc"]["checks"]["black_frames"] == old["assembly"]["media_qc"]["checks"]["black_frames"]
    assert json.loads((archive / "nov_1/thin_media_report.json").read_text()) == old
    assert not (directory / ".render.lock").exists()
    assert recheck_episode(directory, apply=True, archive=archive) is None


def test_running_render_is_left_alone(tmp_path):
    directory, old = episode(tmp_path)
    lock = directory / ".render.lock"
    lock.write_text(str(os.getpid()))
    assert recheck_episode(directory, apply=True, archive=tmp_path / "backup")["skipped"] == "render is running"
    assert lock.read_text() == str(os.getpid())
    assert json.loads((directory / "thin_media_report.json").read_text()) == old


def test_legacy_outro_requires_retained_evidence_not_current_settings(tmp_path):
    directory, old = episode(tmp_path)
    assembly = {k: v for k, v in old["assembly"].items() if k != "silent_outro_seconds"}
    card = audio(directory / "work/outro.mp4", [(4, False)])
    last = audio(directory / "work/join_01.mp4", [(4, False)])
    (directory / "render.log").write_text('{"outro_seconds": 4}')
    (directory / "work/join_list.txt").write_text(f"file '{directory}/work/join_00.mp4'\nfile '{last}'\n")
    final_time = (directory / "nov_1.mp4").stat().st_mtime
    for p in (card, last):
        os.utime(p, (final_time - 1, final_time - 1))
    assert recorded_outro(directory, assembly) == 4
    (directory / "render.log").write_text('{"outro_seconds": 0}')
    with pytest.raises(ValueError, match="no recorded"):
        recorded_outro(directory, assembly)
