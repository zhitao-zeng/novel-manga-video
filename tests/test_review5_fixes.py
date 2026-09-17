"""Regressions for the September 14 review: preserve cuts/tier and enforce current review semantics."""
from __future__ import annotations
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service
import novel_manga.application.repair.judges as repair_judges
import novel_manga.application.production.conductor_state as conductor_state
import novel_manga.application.production.conductor_workers as conductor_workers

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import novel_manga.application.packing.context as packer
import novel_manga.application.planning.cast_completion as completion
import novel_manga.application.production.conductor_flow as conductor_flow
import novel_manga.application.production.conductor_workers as conductor_workers
import novel_manga.application.production.delivery as delivery_gate_thin
import novel_manga.planning.cast as pc_cast
import novel_manga.application.planning.context as planner_context
from novel_manga.planning.context import PlannerContext  # noqa: E402
import novel_manga.application.review.retier as retier_reviews
import novel_manga.application.dashboard.config as dashboard_config
import novel_manga.application.dashboard.history as dashboard_history
import novel_manga.application.dashboard.inventory as dashboard_inventory
import novel_manga.application.production.flow as production_flow
import novel_manga.llm.client as model_client
import novel_manga.application.review.episode as review_episode
import novel_manga.application.review.judges as review_judges
from novel_manga.config import Settings  # noqa: E402
from novel_manga.models.bible import Character, StoryBible
from novel_manga.providers.base import ImageResult  # noqa: E402
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.production.runs import REVIEW_POLICY


@pytest.fixture(autouse=True)
def isolate_packer(monkeypatch):
    planner_ctx = PlannerContext.from_env()
    for key in ("MAX_CLIP_SECONDS", "MAX_STAGES", "SOFT_CUT_SECONDS", "TWO_VIEW_CAST_LIMIT"):
        monkeypatch.setattr(packer, key, getattr(packer, key))
    for key in ("ENTITY_FORMS", "ENTITY_TIERS", "ENTITY_GENERIC", "_FORMS_INDEX"):
        monkeypatch.setattr(planner_ctx, key.lower().lstrip('_'), {})


def write(path: Path, data):
    previous = path.stat().st_mtime if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    stamp = max(time.time(), previous + 1)
    os.utime(path, (stamp, stamp))


def book(tmp_path: Path):
    novel = tmp_path / "nov"
    episode = novel / "nov_1"
    episode.mkdir(parents=True)
    bible = StoryBible(novel_title="测试", genre="generic", visual_style="2d", palette="p", style_fingerprint="s",
                       characters=[Character(name=n, appearance="黑发青年", wardrobe="长袍", role="配角") for n in ("林凡", "苏清")],
                       locations=["大殿：石柱与窗户"])
    write(novel / "story_bible.json", bible.model_dump())
    write(novel / "profile.json", {"frame": "16:9"})  # like wuyue/xinghai: no tier in the novel profile
    return novel, episode


def packed_episode(tmp_path: Path):
    novel, episode = book(tmp_path)
    # origin_index deliberately differs from index: the plan stores the prepared shot's index.
    shot = {"index": 1, "origin_index": 9, "segment_id": "seg_1", "location": "大殿", "characters": ["林凡"],
            "visual_prompt": "林凡站在窗边，苏清站在他身旁", "motion_prompt": "林凡说话，苏清点头", "end_state": "林凡停下",
            "shot_scale": "中景", "camera": "固定中景", "light": "窗外日光",
            "turns": [{"speaker_name": "林凡", "delivery_mode": "visible_dialogue", "text": text}
                      for text in ("甲" * 48, "乙" * 48, "丙" * 48)]}
    old = {"shots": [shot]}
    new = copy.deepcopy(old)
    new["shots"][0]["characters"] = ["林凡", "苏清"]
    plan = {"policy": "thin-15s", "limits": {"max_clip_seconds": 15, "max_stages": 3},
            "totals": {"profile": {"tier": "fast", "frame": "16:9", "style": "2d"}}}
    ctx = packing_context.context_for_plan(episode, novel / "story_bible.json", plan)
    plan["clips"] = [packing_service.clip_entry(c, f"clip_{i:02d}", ctx)
                     for i, c in enumerate(ClipCompiler(ctx['compiler_options'] or compiler_options()).pack(packing_service.prepared_shots(copy.deepcopy(old), episode)), 1)]
    return novel, episode, old, new, plan


@pytest.mark.parametrize("metadata", ["recorded", "legacy", "split_tool"])
def test_cast_completion_preserves_each_split_parts_lines_and_fast_tier(tmp_path, metadata, monkeypatch):
    monkeypatch.setenv("NOVEL_TWO_VIEWS", "1")
    novel, episode, old, new, plan = packed_episode(tmp_path)
    for asset in ("character_001", "character_002"):
        sheet = novel / "series_assets/characters" / asset / "expressions.jpeg"
        sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet.write_bytes(b"existing expression sheet")
    if metadata != "recorded":
        for c in plan["clips"]:
            c.pop("shot_parts")
    if metadata == "split_tool":
        plan["split_long_stages"] = {"split": {"clip_01": [c["clip_id"] for c in plan["clips"]]}}
    merged, changed, why = completion.rebuild_in_place(episode, novel / "story_bible.json", old, new, plan)
    assert not why and len(changed) == 3
    assert [c["spoken_text"] for c in merged["clips"]] == [c["spoken_text"] for c in plan["clips"]]
    assert all(c["seconds_estimate"] <= c["request_seconds"] <= 15 for c in merged["clips"])
    assert [c["shot_parts"][0]["part"] for c in merged["clips"]] == [[1, 3], [2, 3], [3, 3]]
    assert all(c["cast"] == ["林凡", "苏清"] for c in merged["clips"])
    assert not any(ref["path"].endswith("expressions.jpeg") for c in merged["clips"] for ref in c["references"])


@pytest.mark.parametrize("tool_record", [True, False])
def test_already_reexpanded_parts_can_be_recovered_from_split_tool_record(tmp_path, tool_record):
    novel, episode, old, new, plan = packed_episode(tmp_path)
    wanted = [c["spoken_text"] for c in plan["clips"]]
    all_lines = [line for c in plan["clips"] for line in c["lines"]]
    if tool_record:
        plan["split_long_stages"] = {"split": {"clip_01": [c["clip_id"] for c in plan["clips"]]}}
    for c in plan["clips"]:
        c.pop("shot_parts")
        c.update(shot_indexes=[1, 1, 1], lines=all_lines, spoken_text="".join(wanted), cast=["林凡", "苏清"])
    merged, changed, why = completion.rebuild_in_place(episode, novel / "story_bible.json", new, new, plan)
    assert not why and len(changed) == 3
    assert [c["spoken_text"] for c in merged["clips"]] == wanted


def test_unrecoverable_cuts_are_left_unwritten(tmp_path, monkeypatch):
    novel, episode, old, new, plan = packed_episode(tmp_path)
    plan["clips"] = plan["clips"][:2]
    for c in plan["clips"]:
        c.pop("shot_parts")
    write(episode / "clip_plan.json", plan)
    write(episode / "chapter_script.json", old)
    before = (episode / "chapter_script.json").read_bytes(), (episode / "clip_plan.json").read_bytes()
    monkeypatch.setattr(sys, "argv", ["complete_cast_thin", "--novel-dir", str(novel), "--apply", "--mode", "15", "--rebuild-existing"])
    completion.main()
    assert before == ((episode / "chapter_script.json").read_bytes(), (episode / "clip_plan.json").read_bytes())


def test_context_switch_back_to_quality_restores_two_views(tmp_path):
    novel, episode, old, new, plan = packed_episode(tmp_path)
    fast = packing_context.context_for_plan(episode, novel / "story_bible.json", plan)["compiler_options"]
    assert fast.two_view_cast_limit == 0
    plan["totals"]["profile"]["tier"] = "quality"
    ctx = packing_context.context_for_plan(episode, novel / "story_bible.json", plan)
    assert ctx["profile"]["tier"] == "quality" and ctx["compiler_options"].two_view_cast_limit == 2
    assert fast.two_view_cast_limit == 0


def test_an_unrelated_old_uncut_stage_does_not_change_or_block_split_repair(tmp_path):
    novel, episode, old, new, plan = packed_episode(tmp_path)
    extra = copy.deepcopy(old["shots"][0])
    extra.update(index=2, origin_index=2)
    old["shots"].append(copy.deepcopy(extra))
    new["shots"].append(copy.deepcopy(extra))
    # Build the legacy entry as one full stage, without repacking it into new clips.
    ctx = packing_context.context_for_plan(episode, novel / "story_bible.json", plan)
    kept = packing_service.clip_entry({"kind": "video", "location": "大殿", "shots": [extra], "seconds": ClipCompiler(ctx['compiler_options'] or compiler_options()).shot_seconds(extra)}, "clip_04", ctx)
    kept.pop("shot_parts")
    plan["clips"].append(kept)
    merged, changed, why = completion.rebuild_in_place(episode, novel / "story_bible.json", old, new, plan)
    assert not why and merged["clips"][-1] == kept and "clip_04" not in changed


def test_targeted_story_repair_rebuilds_only_the_selected_part(tmp_path, monkeypatch):
    # Importing the existing repair CLI selects a judge; keep that environment change inside this test.
    with patch.dict(os.environ, dict(os.environ), clear=True):
        import novel_manga.application.repair.flow as repair_flow_thin
    monkeypatch.setattr(repair_judges, 'ask_json', lambda *a, **k: pytest.fail("rebuilding must not call a model"))
    novel, episode, old, new, plan = packed_episode(tmp_path)
    merged, changed = repair_flow_thin.rebuild_clips(episode, novel / "story_bible.json", new, plan, {"clip_02"})
    assert changed == ["clip_02"]
    assert merged["clips"][0] == plan["clips"][0] and merged["clips"][2] == plan["clips"][2]
    assert merged["clips"][1]["spoken_text"] == "乙" * 48
    assert merged["clips"][1]["cast"] == ["林凡", "苏清"]


def test_index_recognizes_two_character_people_but_not_generic_nouns(tmp_path):
    planner_ctx = PlannerContext.from_env()
    write(tmp_path / "entity_index.json", {"characters": [
        {"name": "洛恩", "forms": {"洛恩": 10}, "generic": False},
        {"name": "娜芙", "forms": {"娜芙": 10}, "generic": False},
        {"name": "酒保", "forms": {"酒保": 10}, "generic": True},
        {"name": "神", "forms": {"神": 10}, "generic": True}]})
    assert planner_context.load_entity_index(tmp_path, ctx=planner_ctx)
    shot = {"visual_prompt": "娜芙站在洛恩身旁，酒保在门口，神在上方", "motion_prompt": ""}
    assert pc_cast.complete_characters(["洛恩"], shot, ["洛恩", "娜芙", "酒保", "神"], ctx=planner_ctx)[1] == ["娜芙"]
    assert not planner_context.load_entity_index(tmp_path / "missing", ctx=planner_ctx)
    assert not planner_ctx.entity_generic


def conductor(novel, tmp_path):
    return conductor_flow.Conductor({"novel_dir": str(novel), "tmp_dir": str(tmp_path / "conductor"), "keys": [],
        "ranges": [{"chapters": "1-1", "plan_mode": 15}], "qwen": {"urls": []},
        "planning": {"block_size": 1, "blocks_min": 1, "blocks_max": 1, "margin": 0}}, dry_run=True)


def test_old_review_is_pending_in_conductor_board_and_delivery(tmp_path, monkeypatch):
    novel, episode = book(tmp_path)
    plan = {"policy": "thin-15s", "clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "p"}]}
    write(episode / "clip_plan.json", plan)
    write(episode / "thin_media_report.json", {"clip_plan_fingerprint": plan_fingerprint(plan), "assembly": {"thin_passed": True}})
    (episode / "nov_1.mp4").write_bytes(b"video")
    review = {"policy": "thin-review-v1.16-volume", "clips": {"clip_01": {"severity": "pass"}}, "feedback": {}}
    path = episode / "episode_review.json"
    write(path, review)
    c = conductor(novel, tmp_path)
    assert conductor_state.chapter(c, 1)["unreviewed"]
    assert dashboard_inventory._episode_state(episode, False)["review"] == "pending"
    assert dashboard_history._parse_review(path) is None
    monkeypatch.setattr(sys, "argv", ["delivery_gate", "--novel-dir", str(novel), "--quiet"])
    delivery_gate_thin.main()
    assert not json.loads((novel / "delivery.json").read_text())["episodes"][0]["deliverable"]
    review["policy"] = REVIEW_POLICY
    write(path, review)
    assert not conductor_state.chapter(c, 1)["unreviewed"]
    assert dashboard_inventory._episode_state(episode, False)["review"] == "reviewed"
    delivery_gate_thin.main()
    assert json.loads((novel / "delivery.json").read_text())["episodes"][0]["deliverable"]


def test_board_ignores_delivery_aggregate_from_an_old_review_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_config, 'ROOT', tmp_path)
    path = tmp_path / "outputs/nov/delivery.json"
    write(path, {"review_policy": "thin-review-v1.16-volume", "deliverable": 99, "total": 99})
    assert dashboard_inventory._delivery("nov") is None
    write(path, {"review_policy": REVIEW_POLICY, "deliverable": 1, "total": 99})
    assert dashboard_inventory._delivery("nov")["deliverable"] == 1


def test_retierring_restores_a_wrong_actor_hidden_by_scripted_exemption(tmp_path, monkeypatch):
    novel, episode = book(tmp_path)
    verdict = {"severity": "fail", "tier": "optional", "story_ok": False, "story_kind": "动作落在错误的人物身上",
               "story_issue": "原文是林凡递信，画面是苏清递信", "scripted": {"evidence": "林凡递信", "note": ""}}
    write(episode / "episode_review.json", {"policy": REVIEW_POLICY, "clips": {"clip_01": verdict}, "feedback": {}})
    monkeypatch.setattr(model_client, "ask_json", lambda *a, **k: pytest.fail("retiering must not call a model"))
    monkeypatch.setattr(sys, "argv", ["retier_reviews", "--novel-dir", str(novel), "--apply"])
    retier_reviews.main()
    review = json.loads((episode / "episode_review.json").read_text())
    assert review["clips"]["clip_01"]["tier"] == "must_fix"
    assert "原文是林凡递信" in review["feedback"]["clip_01"]


def test_new_story_failure_is_never_sent_for_a_scripted_oddity_exemption(tmp_path, monkeypatch):
    novel, episode = book(tmp_path)
    video = episode / "work/clips/clip_01/attempt_01/clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"take")
    write(episode / "clip_plan.json", {"clips": [{"clip_id": "clip_01", "kind": "video", "prompt": "p"}]})
    write(episode / "thin_media_report.json", {"clips": [{"clip_id": "clip_01", "selected": {"video": str(video)}}]})
    monkeypatch.setattr(review_judges, "judge_clip", lambda *a, **k: {
        "severity": "fail", "story_ok": False, "story_kind": "动作落在错误的人物身上", "story_issue": "林凡的动作由苏清执行"})
    monkeypatch.setattr(review_judges, "script_check", lambda *a, **k: pytest.fail("a wrong actor cannot be exempted"))
    report = review_episode.review_episode(episode)
    assert report["clips"]["clip_01"]["tier"] == "must_fix" and report["feedback"]["clip_01"]


def test_pick_more_than_available_terminates_and_selects_each_episode_once(tmp_path):
    novel, episode = book(tmp_path)
    write(episode / "episode_review.json", {"feedback": {"clip_01": "人物重复出现，多余的手"}})
    result = subprocess.run([sys.executable, "scripts/apply_review_feedback.py", "--novel-dir", str(novel),
                             "--must-fix", "--pick", "10", "--apply"], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert (novel / "repair_targets.txt").read_text() == "1"
    assert len(json.loads((episode / "review_feedback.json").read_text())) == 1


def test_asset_opt_in_survives_conductor_batch_and_provider(tmp_path, monkeypatch):
    novel, episode = book(tmp_path)
    for key, value in {"PHANROUTER_REFERENCE_ASSETS": "1", "PHANROUTER_INLINE_REFERENCE_IMAGES": "1",
                       "PHANROUTER_ASSET_GROUP_ID": "test-group", "PHANROUTER_API_KEY": "test-key"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PHANROUTER_ASSET_PUBLIC_BASE", raising=False)
    captured = {}
    monkeypatch.setattr(conductor_workers.subprocess, "Popen", lambda *a, **k: (captured.update(k), SimpleNamespace(pid=123))[1])
    c = conductor(novel, tmp_path)
    c.dry = False
    conductor_workers.spawn(c, "worker", ["test-worker"])
    assert captured["env"]["PHANROUTER_INLINE_REFERENCE_IMAGES"] == "0"
    args = SimpleNamespace(novel_dir=str(novel), source=str(tmp_path / "novel.txt"), title=None, notes_json=None,
                           resubmit_unconfirmed=False, unattended=False, review_only=False, tier="fast", card_parallel=1)
    batch = production_flow.Batch(args)
    assert batch.env["PHANROUTER_INLINE_REFERENCE_IMAGES"] == "0"
    monkeypatch.setenv("PHANROUTER_INLINE_REFERENCE_IMAGES", batch.env["PHANROUTER_INLINE_REFERENCE_IMAGES"])
    settings = Settings.from_env(provider="phanrouter", admission_mode="preview")
    provider = PhanRouterMediaProvider(settings)
    provider.client.close()
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"Result": {"Id": "asset-test"}})
    provider.client = httpx.Client(transport=httpx.MockTransport(handle))
    card = episode / "card.jpeg"
    Image.new("RGB", (8, 8), "white").save(card)
    assert provider._restore_image_url(ImageResult(path=card, public_url="https://cdn.test/card.jpeg")) == "asset://asset-test"
    assert len(calls) == 1 and calls[0].endswith("/open/CreateAsset")
    provider.client.close()
    batch.cards.shutdown()
