import novel_manga.repair.execution as repair_execution
import copy
import json
from pathlib import Path

import pytest

import novel_manga.util as utils
import repair_delivery_thin as delivery
import repair_history as history
from novel_manga import model_client
import novel_manga.review.policy as review_policy
import novel_manga.review.storage as review_storage
import review_evidence_thin as review_evidence
from review_store_thin import current_takes
from thin_profile import plan_fingerprint
from thin_runs import REVIEW_POLICY, episode_status


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


def test_trial_keeps_old_media_and_request_before_the_live_take_is_replaced(episode):
    directory, plan, review, media = episode
    history.begin_trial(directory, {"clip_01"}, "instruction", after_notes={"clip_01": "new framing"})
    trial = history.load(directory)["trials"][0]
    old = Path(trial["before"]["clip_01"]["archive"]["video"])
    source = Path(media["clips"][0]["selected"]["video"])
    source.write_bytes(b"replacement")
    source.unlink()  # the renderer's later prune cannot erase the archived candidate
    assert old.read_bytes() == b"original take"
    assert utils.read_json(old.parent / "request.json")["prompt"] == "original request"
    assert trial["before"]["clip_01"]["plan"] == plan["clips"][0]
    assert (directory / "nov_1.mp4").read_bytes() == b"incumbent movie"


def test_duplicate_reviews_are_not_two_failed_takes_and_fine_breaks_the_streak(episode):
    directory, plan, review, _ = episode
    history.begin_trial(directory, {"clip_01"}, "rewrite")
    takes = current_takes(directory, plan, review)
    history.observe(directory, review, takes)
    assert len(history.load(directory)["observations"]["clip_01"]) == 1
    assert not history.repeated_errors(directory, "clip_01")
    fresh = copy.deepcopy(review)
    fresh["clips"]["clip_01"]["take"] = [22, 222]
    history.observe(directory, fresh, {"clip_01": {"video": fresh["clips"]["clip_01"]["video"], "take": [22, 222]}})
    assert history.repeated_errors(directory, "clip_01") == ["same_person_twice"]
    fresh["clips"]["clip_01"]["verify"] = {"verdict": "fine"}
    history.observe(directory, fresh, {"clip_01": {"video": fresh["clips"]["clip_01"]["video"], "take": [22, 222]}})
    assert not history.repeated_errors(directory, "clip_01")


def test_stale_verdict_is_not_presented_as_current_failure_history(episode):
    directory, plan, review, _ = episode
    data = {"observations": {}}
    takes = {"clip_01": {"video": review["clips"]["clip_01"]["video"], "take": [99, 999]}}
    assert not history.add_observations(data, review, takes)
    assert not data["observations"]


def test_render_record_counts_new_material_and_ignores_other_requests(episode):
    directory, _, _, media = episode
    history.begin_trial(directory, {"clip_01"}, "rewrite", changes={"clip_01": "narrow frame"})
    assert "narrow frame" not in history.history_context(directory, "clip_01")  # not yet tried
    media["clips"][0]["attempts"] = [{"duration": 8, "generated_this_run": True}, {"duration": 8, "generated_this_run": False}]
    history.record_render(directory, media)
    row = history.load(directory)["trials"][0]["renders"][0]["clips"]["clip_01"]
    assert row["completed_generated_seconds"] == 8
    assert "narrow frame" in history.history_context(directory, "clip_01")
    history.record_render(directory, media)
    assert len(history.load(directory)["trials"][0]["renders"]) == 1
    media["review_feedback"] = {"clip_01": "unrelated later correction"}
    history.record_render(directory, media)
    assert len(history.load(directory)["trials"][0]["renders"]) == 1


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


@pytest.mark.parametrize("problem", ["obvious", "unreviewed", "stale_take", "flash", "technical", "stale_plan", "speech_gate", "false_fine"])
def test_unapproved_candidate_never_replaces_the_incumbent(episode, problem):
    directory, plan, review, media, takes = candidate_episode(episode)
    row = review["clips"]["clip_01"]
    review["feedback"] = {}
    row.update(story_ok=True, verify={"verdict": "fine"})
    if problem == "obvious": row["verify"] = {"verdict": "obvious"}
    elif problem == "unreviewed": row.pop("verify")
    elif problem == "stale_take": row["take"] = [55, 555]
    elif problem == "flash": row["flash_pending"] = "confirm this"
    elif problem == "technical": media["assembly"]["thin_passed"] = False
    elif problem == "stale_plan": plan["clips"][0]["prompt"] = "changed request"
    elif problem == "speech_gate": media["gate_failed_clips"] = ["clip_01"]
    elif problem == "false_fine": row["verify"]["same_person_twice"] = True
    write(directory / "clip_plan.json", plan)
    write(directory / "thin_media_report.json", media)
    assert not delivery.publish_if_ready(directory, review, takes)
    assert (directory / "nov_1.mp4").read_bytes() == b"incumbent movie"
    assert Path(media["assembly"]["final_video"]).read_bytes() == b"new candidate"


def test_current_verified_candidate_is_published_and_previous_movie_is_kept(episode):
    directory, plan, review, media, takes = candidate_episode(episode)
    assert episode_status(directory, True) == "done"  # technically ready for review, not yet published
    assert delivery.publication_pending(directory)
    review["feedback"] = {}
    review["clips"]["clip_01"].update(story_ok=True, verify={"verdict": "fine"})
    assert delivery.publish_if_ready(directory, review, takes)
    assert (directory / "nov_1.mp4").read_bytes() == b"new candidate"
    assert (directory / history.HISTORY_DIR / "previous_final.mp4").read_bytes() == b"incumbent movie"
    assert not delivery.publication_pending(directory)
    assert not delivery.publish_if_ready(directory, review, takes)  # publishing is idempotent


def test_first_candidate_can_be_reviewed_before_a_canonical_movie_exists(episode):
    directory, plan, review, media, takes = candidate_episode(episode)
    (directory / "nov_1.mp4").unlink()
    assert episode_status(directory, True) == "done"
    review["feedback"] = {}
    review["clips"]["clip_01"].update(story_ok=True, verify={"verdict": "fine"})
    assert delivery.publish_if_ready(directory, review, takes)


def test_interrupted_publication_can_resume_without_losing_the_old_movie(episode, monkeypatch):
    directory, plan, review, media, takes = candidate_episode(episode)
    review["feedback"] = {}
    review["clips"]["clip_01"].update(story_ok=True, verify={"verdict": "fine"})
    original_write = delivery.atomic_write_json
    def failed_report(path, value):
        if path.name == "thin_media_report.json":
            raise OSError("interrupted report write")
        original_write(path, value)
    monkeypatch.setattr(delivery, "atomic_write_json", failed_report)
    with pytest.raises(OSError):
        delivery.publish_if_ready(directory, review, takes)
    assert Path(media["assembly"]["final_video"]).is_file()
    assert (directory / history.HISTORY_DIR / "previous_final.mp4").read_bytes() == b"incumbent movie"
    monkeypatch.setattr(delivery, "atomic_write_json", original_write)
    assert delivery.publish_if_ready(directory, review, takes)
    assert (directory / history.HISTORY_DIR / "previous_final.mp4").read_bytes() == b"incumbent movie"


def test_cause_advice_is_preserved_without_overriding_the_visual_verdict():
    result = review_policy.verify_to_verdict({"verdict": "obvious", "same_person_twice": True, "people": [],
                                        "evidence": "two leads", "instruction": "one lead",
                                        "repair_advice": {"layer": "generation", "evidence": "request says one", "next_change": "close shot"}})
    assert result["story_ok"] is False
    assert result["verify"]["repair_advice"]["layer"] == "generation"
    assert result["verify"]["instruction"] == "one lead"


def test_managed_verifier_adds_advice_without_old_failure_labels(episode, monkeypatch):
    from verify_clips_thin import Verifier
    directory, plan, review, media = episode
    history.begin_trial(directory, {"clip_01"}, "rewrite")
    v = Verifier.__new__(Verifier)
    v.novel, v.frames, v.prefix, v.judge_tag = directory.parent, directory / "frames", "nov", "test"
    v.model_settings = model_client.JsonEndpoint.from_env()
    v.repair_advice = True
    monkeypatch.setattr(v, "prompt_for", lambda *a: ([], "current frame questions"))
    monkeypatch.setattr(review_evidence, "clip_frames", lambda *a: [directory / "frame.jpeg"])
    monkeypatch.setattr(model_client, "image_part", lambda *a: {"type": "text", "text": "test frame"})
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(parts[-1]["text"])
        assert "repair_advice" in schema["required"]
        assert kwargs["max_tokens"] == 1200
        return {"verdict": "fine", "people": [], "repair_advice": {"layer": "none", "evidence": "", "next_change": ""}}
    monkeypatch.setattr(model_client, "ask_json", ask)
    result = v.verify((1, "clip_01", "", "managed"))
    assert result["verdict"] == "fine" and len(calls) == 1
    assert "two copies of the lead" not in calls[0]
    assert "已发生的修复记录" not in calls[0] and "error" not in result


def test_explicit_repair_participants_keep_their_cards_when_only_one_person_speaks():
    import repair_flow_thin as repair
    import build_clip_plan_thin as packer
    names = ["塞西娅", "莱恩·格雷", "贝纳妮丝"]
    shot = {"characters": names[:], "visual_prompt": "三人被污泥雨包围", "motion_prompt": "暖光笼罩三人", "end_state": "三人受到保护",
            "turns": [{"speaker_name": "塞西娅", "delivery_mode": "visible_dialogue", "text": "地脉恩赐！"}]}
    fix = {"in_frame": names, "actions": [{"actor": "塞西娅", "action": "施法", "target": ""}], "extras": [], "event": "暖光保护三人"}
    repair_execution.apply_stage(shot, fix, names)
    assert shot["in_frame"] == names
    # The other two may be listeners, but are still explicit participants with
    # distinct identities; missing their cards produced two identical men.
    assert packer.clip_cast({"shots": [shot]}) == names


def test_positive_visual_flags_still_count_when_the_model_labels_the_verdict_fine(episode):
    directory, _, _, _ = episode
    history.save(directory, {"trials": [], "observations": {"clip_01": [
        {"video": "first", "take": [1, 1], "verdict": "fine", "errors": ["actor_missing"], "evidence": "missing actor"},
        {"video": "second", "take": [2, 2], "verdict": "obvious", "errors": ["actor_missing"], "evidence": "missing actor"}]}})
    assert history.repeated_errors(directory, "clip_01") == ["actor_missing"]
    assert "结论：obvious" in history.history_context(directory, "clip_01")


def test_actor_tags_are_resolved_before_visual_translation(monkeypatch):
    import build_h3_prompts as h3
    clip = {"clip_id": "clip_01", "kind": "video", "request_seconds": 10,
            "prompt": "【阶段1】林凡推门走进大殿。【阶段2】林凡抬头看向王座。",
            "references": [{"role": "character", "name": "林凡", "path": "card.jpeg"}]}
    seen = []
    def ask(parts, *args, **kwargs):
        seen.append(parts[0]["text"].split("\n1. ", 1)[1])
        return {"shots": ["<Subject 1> opens the door.", "<Subject 1> looks up."]}
    monkeypatch.setattr(h3, "ask_json", ask)
    assert h3.convert(clip)
    assert "<Subject 1>推门" in seen[0] and "林凡" not in seen[0]


def test_name_prefixes_and_ambiguous_abbreviations_do_not_swap_actors():
    from build_h3_prompts import tag_names
    assert tag_names("林凡青看着林凡", "林凡 = <Subject 1>\n林凡青 = <Subject 2>") == "<Subject 2>看着<Subject 1>"
    naming = "莱恩·格雷 = <Subject 1>\n莱恩·史密斯 = <Subject 2>"
    assert tag_names("莱恩·格雷看着莱恩·史密斯，莱恩开口", naming) == "<Subject 1>看着<Subject 2>，莱恩开口"
