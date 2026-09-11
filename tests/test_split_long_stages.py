"""split_long_stages.py: an old plan's clamped clips become the parts the packer now cuts them into, and nothing
else about the episode changes - the other clips keep their entries and their rendered videos."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_clips_thin as rc  # noqa: E402
import split_long_stages as tool  # noqa: E402
import thin_batch  # noqa: E402
from thin_profile import plan_fingerprint  # noqa: E402


def long_stage(turns: list[str], index: int = 5) -> dict:
    return {"index": index, "origin_index": index, "location": "大殿", "segment_id": "seg_01", "shot_scale": "中景",
            "visual_prompt": "林凡站在大殿中央", "motion_prompt": "林凡边说边踱步", "end_state": "林凡停下",
            "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "林凡", "text": text} for text in turns]}


PLAN = {"clips": [
    {"clip_id": "clip_01", "kind": "video", "shot_indexes": [3], "seconds_estimate": 12.0, "request_seconds": 12, "prompt": "first"},
    {"clip_id": "clip_02", "kind": "video", "shot_indexes": [5], "seconds_estimate": 40.0, "request_seconds": 15, "prompt": "clamped"},
    {"clip_id": "clip_03", "kind": "title_card", "shot_indexes": [7], "seconds_estimate": 3.0, "request_seconds": 3, "text": "三天后"},
    {"clip_id": "clip_04", "kind": "video", "shot_indexes": [9], "seconds_estimate": 6.0, "request_seconds": 6, "prompt": "last"}]}


def test_only_the_clamped_clip_is_replaced_and_the_others_are_renumbered(monkeypatch):
    monkeypatch.setattr(tool.packer, "MAX_CLIP_SECONDS", 15.0)
    monkeypatch.setattr(tool.packer, "MAX_STAGES", 3)
    built = []

    def build(raw, new_id, old_id):
        built.append((new_id, old_id, [t["text"] for t in raw["shots"][0]["turns"]]))
        return {"clip_id": new_id, "kind": "video", "prompt": f"part of {old_id}"}
    clips, moved, split = tool.resplit(PLAN, {5: long_stage(["我们走吧。" * 12] * 3)}, build)
    assert [c["clip_id"] for c in clips] == ["clip_01", "clip_02", "clip_03", "clip_04", "clip_05", "clip_06"]
    assert split == {"clip_02": ["clip_02", "clip_03", "clip_04"]}
    assert moved == {"clip_01": "clip_01", "clip_03": "clip_05", "clip_04": "clip_06"}
    assert clips[0] == PLAN["clips"][0] and clips[4] == {**PLAN["clips"][2], "clip_id": "clip_05"}
    assert [b[2] for b in built] == [["我们走吧。" * 12]] * 3  # every line once, in order


def test_a_plan_without_long_stages_is_left_alone():
    clips, moved, split = tool.resplit({"clips": [PLAN["clips"][0]]}, {}, lambda *a: None)
    assert clips == [PLAN["clips"][0]] and not split


def test_rendered_clips_and_corrections_follow_their_new_ids(tmp_path):
    episode = tmp_path / "nov" / "nov_1"
    for name in ("clip_01", "clip_02", "clip_03", "clip_04", "clip_09"):
        attempt = episode / "work" / "clips" / name / "attempt_01"
        attempt.mkdir(parents=True)
        (attempt / "clip.mp4").write_text(name, encoding="utf-8")
    moved = {"clip_01": "clip_01", "clip_03": "clip_05", "clip_04": "clip_06"}
    split = {"clip_02": ["clip_02", "clip_03", "clip_04"]}
    tool.rename_clip_dirs(episode, moved, split)
    clips = episode / "work" / "clips"
    assert (clips / "clip_05" / "attempt_01" / "clip.mp4").read_text(encoding="utf-8") == "clip_03"
    assert (clips / "clip_06" / "attempt_01" / "clip.mp4").read_text(encoding="utf-8") == "clip_04"
    assert not (clips / "clip_02").exists() and not (clips / "clip_09").exists()
    assert {p.parent.parent.name for p in (episode / "work" / "clips_before_split").glob("*/*/attempt_01/clip.mp4")} == {"clip_02", "clip_09"}
    feedback = episode / "review_feedback.json"
    feedback.write_text(json.dumps({"clip_02": "修正A", "clip_04": "修正B", "clip_09": "旧"}, ensure_ascii=False), encoding="utf-8")
    assert tool.remap_json(feedback, moved, split, to_parts=False) == {"clip_02": "修正A", "clip_09": "旧"}
    assert json.loads(feedback.read_text(encoding="utf-8")) == {"clip_06": "修正B"}
    overrides = episode / "clip_overrides.json"
    overrides.write_text(json.dumps({"clip_02": {"cast": ["林凡"]}}, ensure_ascii=False), encoding="utf-8")
    tool.remap_json(overrides, moved, split, to_parts=True)
    assert set(json.loads(overrides.read_text(encoding="utf-8"))) == {"clip_02", "clip_03", "clip_04"}


def test_a_moved_take_s_asr_record_names_its_new_place(tmp_path):
    episode = tmp_path / "nov" / "nov_1"
    for name in ("clip_02", "clip_03"):
        attempt = episode / "work" / "clips" / name / "attempt_01"
        attempt.mkdir(parents=True)
        (attempt / "clip.mp4").write_text(name, encoding="utf-8")
        (attempt / "asr.json").write_text(json.dumps({"clip_id": name, "video": str(attempt / "clip.mp4"), "passed": True}), encoding="utf-8")
    tool.rename_clip_dirs(episode, {"clip_03": "clip_05"}, {"clip_02": ["clip_02", "clip_03", "clip_04"]})
    moved = episode / "work" / "clips" / "clip_05" / "attempt_01"
    record = json.loads((moved / "asr.json").read_text(encoding="utf-8"))
    assert record == {"clip_id": "clip_05", "video": str(moved / "clip.mp4"), "passed": True}


# ---------------------------------------------------------------- the two runner changes that go with it
def runner(tmp_path, cache_only=False) -> rc.ThinMediaRunner:
    r = object.__new__(rc.ThinMediaRunner)
    r.novel_dir, r.work = tmp_path / "nov", tmp_path / "nov" / "nov_1" / "work"
    r.settings = types.SimpleNamespace(local_h3_base_url=None)
    r.feedback, r.prescreen, r.cache_only = {}, False, cache_only
    return r


def test_cache_only_leaves_a_clip_from_another_request_where_it_is(tmp_path):
    r = runner(tmp_path, cache_only=True)
    attempt = r.work / "clips" / "clip_01" / "attempt_01"
    attempt.mkdir(parents=True)
    (attempt / "clip.mp4").write_bytes(b"old take")
    (attempt / "request.json").write_text(json.dumps({"prompt": "an older wording", "references": [], "duration": 10}), encoding="utf-8")
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡走进大殿。", "request_seconds": 10, "references": []}
    try:
        r.generate_clip(clip, 1)
    except rc.CacheMiss:
        pass
    assert (attempt / "clip.mp4").read_bytes() == b"old take" and not (attempt / "clip.stale.mp4").exists()


def test_a_cached_asr_record_gives_the_take_beside_it_not_the_path_inside(tmp_path, monkeypatch):
    # 2026-09-11: after the split renamed clip_03 to clip_04, clip_04's asr.json still named clip_03/attempt_01/clip.mp4
    # - by then the split stage's first part - and 39 星海 finals were put together from neighbouring clips' takes.
    r = runner(tmp_path)
    attempt = r.work / "clips" / "clip_04" / "attempt_01"
    attempt.mkdir(parents=True)
    for name in ("clip.mp4", "native.wav"):
        (attempt / name).write_bytes(b"x")
    old = r.work / "clips" / "clip_03" / "attempt_01" / "clip.mp4"
    (attempt / "asr.json").write_text(json.dumps({"clip_id": "clip_03", "video": str(old), "passed": True}), encoding="utf-8")
    monkeypatch.setattr(rc, "media_duration", lambda path: 10.0)
    result = r.analyse_clip({"clip_id": "clip_04", "spoken_text": "我们走吧。"}, attempt / "clip.mp4")
    assert result == {"clip_id": "clip_04", "video": str(attempt / "clip.mp4"), "passed": True}


def test_fresh_takes_past_the_cache_are_free_lanes_or_asked_for():
    local, paid = types.SimpleNamespace(local_h3_base_url="pool"), types.SimpleNamespace(local_h3_base_url=None)
    assert rc.takes_past_cache(local, False, False) and not rc.takes_past_cache(paid, False, False)
    assert rc.takes_past_cache(paid, True, False) and not rc.takes_past_cache(local, True, True)


def test_thin_batch_retake_failed_takes_a_paid_final_back(tmp_path, monkeypatch):
    directory = tmp_path / "nov" / "nov_1"
    directory.mkdir(parents=True)
    plan = {"policy": "thin-clip-plan-v9-15s", "clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "p", "references": []}]}
    (directory / "clip_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "thin_media_report.json").write_text(json.dumps({"clip_plan_fingerprint": plan_fingerprint(plan), "review_feedback": {},
                                                                  "failed_clips": [], "gate_failed_clips": ["clip_01"], "assembly": {"thin_passed": True}}), encoding="utf-8")
    (directory / "nov_1.mp4").write_bytes(b"mp4")
    monkeypatch.delenv("NOVEL_LOCAL_H3_URL", raising=False)
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)

    def batch(retake: bool):
        b = object.__new__(thin_batch.Batch)
        b.args = types.SimpleNamespace(rerender=False, cache_only=False, retake_failed=retake, no_render=False, dry_run=False, workers=0,
                                       inflight=4, tier=None, prescreen=False, moderation_repair=True, prune=False)
        b.novel_dir, b.novel_id, b.rows = tmp_path / "nov", "nov", {1: {}}
        b.reviewing, b.fast, b.env, b.notes, b.commands = False, True, {}, {}, []
        monkeypatch.setattr(b, "run", lambda command, log_path: (b.commands.append(command), (0, ""))[1])
        monkeypatch.setattr(b, "prepare_cards", lambda chapter: None)
        monkeypatch.setattr(b, "fill_result", lambda chapter: None)
        b.render(1)
        return b
    assert not batch(False).commands
    assert "--retake-failed" in batch(True).commands[0]


def test_a_clip_the_output_filter_passed_with_the_compliance_line_is_a_cache_hit(tmp_path):
    r = runner(tmp_path)

    class Provider:
        def create_video(self, *args, **kwargs):
            raise AssertionError("paid for a clip the cache holds")
    r.provider = Provider()
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段1】林凡擦去嘴角的血。", "request_seconds": 10, "references": []}
    attempt = r.work / "clips" / "clip_01" / "attempt_01"
    attempt.mkdir(parents=True)
    (attempt / "clip.mp4").write_bytes(b"take that passed the filter")
    (attempt / "request.json").write_text(json.dumps({"prompt": clip["prompt"] + rc.COMPLIANCE_SUFFIX, "references": [],
                                                      "reference_sha256": [], "duration": 10}, ensure_ascii=False), encoding="utf-8")
    assert rc.soften_prompt(clip["prompt"]) != clip["prompt"] + rc.COMPLIANCE_SUFFIX  # softening would have changed it
    assert r.generate_clip(clip, 1).read_bytes() == b"take that passed the filter"


def test_parts_are_packed_again_for_the_fast_tier_with_their_ids(tmp_path, monkeypatch):
    episode = tmp_path / "nov" / "nov_1"
    episode.mkdir(parents=True)
    (episode / "chapter_script.json").write_text(json.dumps({"shots": []}), encoding="utf-8")
    plan = {"limits": {"max_clip_seconds": 15, "max_stages": 3}, "clips": [
        {"clip_id": "clip_01", "kind": "video", "shot_indexes": [3], "prompt": "kept"},
        {"clip_id": "clip_02", "kind": "video", "shot_indexes": [5], "prompt": "part 1, quality", "references": ["expressions.jpeg"]},
        {"clip_id": "clip_03", "kind": "video", "shot_indexes": [5], "prompt": "part 2, quality", "references": ["expressions.jpeg"]}],
        "split_long_stages": {"split": {"clip_02": ["clip_02", "clip_03"]}}}
    (episode / "clip_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    tiers = []
    monkeypatch.setattr(tool.packer, "load_context", lambda episode_dir, bible, tier=None: tiers.append(tier) or {"overrides": {}})
    monkeypatch.setattr(tool.packer, "prepared_shots", lambda script, episode_dir: [long_stage(["我们走吧。" * 12] * 2)])
    monkeypatch.setattr(tool.packer, "clip_entry", lambda raw, clip_id, ctx, override=None: {"clip_id": clip_id, "kind": "video", "prompt": f"{clip_id}, fast", "references": []})
    monkeypatch.setattr(tool.packer, "plan_totals", lambda clips, shots, ctx: {})
    assert tool.rebuild_parts(episode, "fast", apply=True) == {"rebuilt": 2}
    written = json.loads((episode / "clip_plan.json").read_text(encoding="utf-8"))
    assert tiers == ["fast"] and [c["prompt"] for c in written["clips"]] == ["kept", "clip_02, fast", "clip_03, fast"]
    assert written["split_long_stages"]["tier"] == "fast"
