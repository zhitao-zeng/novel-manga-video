import packing_context_thin as packing_context
import packing_service_thin as packing_service
import repair_context_thin as repair_context
import repair_judges_thin as repair_judges
import production_render_thin as production_render

from render_context_support import uninitialized_runner
"""Split recovery must preserve dialogue order; asset checks must stay episode-local."""
import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import packing_context_thin as packer
import render_flow_thin as renderer
import repair_split_ranges as ranges
from novel_manga.models.bible import Character, StoryBible
from novel_manga.config import Settings

with patch.dict(os.environ):
    import repair_flow_thin as repair


@pytest.fixture
def split_episode(tmp_path, monkeypatch):
    for key in ("MAX_CLIP_SECONDS", "MAX_STAGES", "SOFT_CUT_SECONDS", "TWO_VIEW_CAST_LIMIT"):
        monkeypatch.setattr(packer, key, getattr(packer, key))
    episode = tmp_path / "book" / "book_1"
    episode.mkdir(parents=True)
    bible = StoryBible(novel_title="测试", genre="generic", visual_style="2d", palette="蓝", style_fingerprint="test",
                       characters=[Character(name="林凡", appearance="黑发", wardrobe="白衣")], locations=["大厅：木桌"])
    (episode.parent / "story_bible.json").write_text(bible.model_dump_json())
    shot = {"index": 1, "origin_index": 9, "segment_id": "seg_1", "location": "大厅", "characters": ["林凡"],
            "visual_prompt": "林凡站在窗边", "motion_prompt": "林凡说话", "end_state": "林凡停下",
            "shot_scale": "中景", "camera": "固定中景", "light": "窗外日光",
            "turns": [{"speaker_name": "林凡", "delivery_mode": "visible_dialogue", "text": char * 48}
                      for char in "甲乙丙"]}
    script = {"shots": [shot]}
    plan = {"policy": "thin-15s", "limits": {"max_clip_seconds": 15, "max_stages": 3},
            "totals": {"profile": {"tier": "fast", "frame": "16:9", "style": "2d"}}}
    ctx = packing_context.context_for_plan(episode, episode.parent / "story_bible.json", plan)
    plan["clips"] = [packing_service.clip_entry(c, f"clip_{i:02d}", ctx)
                     for i, c in enumerate(packing_service.pack(packing_service.prepared_shots(copy.deepcopy(script), episode), settings=ctx["compiler_options"]), 1)]
    return episode, script, plan


@pytest.mark.parametrize("metadata", [False, True])
def test_targeted_legacy_repair_keeps_sibling_context(split_episode, metadata):
    episode, script, plan = split_episode
    for clip in plan["clips"]:
        clip.pop("shot_parts")
    if metadata:
        plan["split_long_stages"] = {"split": {"clip_01": [c["clip_id"] for c in plan["clips"]]}}
    # A missing unrelated source stage must not prevent the selected repair.
    unrelated = {"kind": "video", "clip_id": "clip_04", "shot_indexes": [99]}
    plan["clips"].append(unrelated)
    result, changed = repair.rebuild_clips(episode, episode.parent / "story_bible.json", script, plan, {"clip_02"})
    assert changed == ["clip_02"]
    assert result["clips"][1]["spoken_text"] == "乙" * 48
    assert result["clips"][1]["shot_parts"] == [{"index": 1, "part": [2, 3]}]
    assert result["clips"][0] == plan["clips"][0] and result["clips"][2:] == plan["clips"][2:]


def test_no_op_rebuild_preserves_english_prompt_and_does_not_request_a_retake(split_episode):
    episode, script, plan = split_episode
    for c in plan["clips"]:
        c.update(prompt_h3="existing English", prompt_h3_of="existing source", prompt_h3_skip=True)
    updated, changed = repair.rebuild_clips(episode, episode.parent / "story_bible.json", script, plan, {"clip_02"})
    assert not changed and updated == plan


def test_later_location_change_recuts_only_affected_range(split_episode):
    from clip_readiness import plan_issues
    from repair_blocked_plan import turn_stream
    episode, script, plan = split_episode
    bible = json.loads((episode.parent / 'story_bible.json').read_text())
    bible['locations'].append('卧室：床')
    (episode.parent / 'story_bible.json').write_text(json.dumps(bible))
    first = script['shots'][0]
    first['turns'] = [{'speaker_name': '林凡', 'delivery_mode': 'visible_dialogue', 'text': '第一句。'}]
    second = {**copy.deepcopy(first), 'index': 2}
    second['turns'][0]['text'] = '第二句。'
    script['shots'] = [first, second]
    ctx = packing_context.context_for_plan(episode, episode.parent / 'story_bible.json', plan)
    packed = packing_service.pack(copy.deepcopy(script['shots']), settings=ctx['compiler_options'])
    assert len(packed) == 1
    plan['clips'] = [packing_service.clip_entry(packed[0], 'clip_01', ctx)]
    # Unrelated entries and cached translations must survive even if they
    # need an independent source-address repair later.
    unrelated = {**copy.deepcopy(plan['clips'][0]), 'clip_id': 'clip_09', 'shot_indexes': [99],
                 'shot_parts': [], 'prompt_h3': 'keep exactly'}
    plan['clips'].append(unrelated)
    second['location'] = '卧室'
    assert any(r.startswith('location:') for r in plan_issues(plan, script)['clip_01'])
    report = {}
    updated, changed = repair.rebuild_clips(episode, episode.parent / 'story_bible.json', script, plan,
                                           {'clip_01'}, repack_report=report)
    assert changed == ['clip_01', 'clip_10']
    assert updated['clips'][-1] == unrelated
    assert [c['location'] for c in updated['clips'][:-1]] == ['大厅', '卧室']
    assert [next(r['name'] for r in c['references'] if r['role'] == 'location')
            for c in updated['clips'][:-1]] == ['大厅', '卧室']
    assert report['groups'] == [{'old': ['clip_01'], 'new': ['clip_01', 'clip_10'], 'source_indexes': [1, 2]}]
    rebuilt = packing_service.shots_for_plan(updated, script['shots'], set(changed), settings=packing_context.context_for_plan(episode, episode.parent/'story_bible.json', updated)['compiler_options'])
    assert turn_stream(script['shots']) == turn_stream([s for cid in changed for s in rebuilt[cid]])
    assert not set(plan_issues(updated, script)) & set(changed)


def test_legacy_location_label_with_correct_rebound_reference_does_not_retake():
    from clip_readiness import location_issues
    shots = {1: {'location': '公安局旁边巷子'}}
    clip = {'location': '老街后巷', 'shot_indexes': [1],
            'references': [{'role': 'location', 'name': '公安局旁边巷子'}]}
    assert not location_issues(clip, shots)
    shots[1]['location'] = '卧室'
    assert location_issues(clip, shots)


def test_story_repair_addresses_plan_index_and_preserves_source_index(split_episode, monkeypatch):
    episode, script, plan = split_episode
    (episode / "chapter_script.json").write_text(json.dumps(script))
    (episode / "clip_plan.json").write_text(json.dumps(plan))
    (episode / "episode_review.json").write_text(json.dumps({"clips": {"clip_02": {"tier": "must_fix", "story_ok": False}}}))
    monkeypatch.setattr(repair_context, 'ledger_cast', lambda *a: {})
    monkeypatch.setattr('identity_flow_thin.resolve_chapter',lambda *a,**k:{'policy':'test','entities':{},'mentions':[]})
    def answer(content, schema, **kwargs):
        assert schema["properties"]["stages"]["items"]["properties"]["origin_index"]["enum"] == [1]
        return {"stages": [{"origin_index": 1, "in_frame": ["林凡"], "actions": [], "extras": ["持灯的侍者"], "event": "林凡转身说话"}]}
    monkeypatch.setattr(repair_judges, 'ask_json', answer)
    result = repair.repair_episode(episode.parent, 1, True)
    assert result["changed"] == ["clip_02"]
    assert json.loads((episode / "chapter_script.json").read_text())["shots"][0]["origin_index"] == 9
    actual = json.loads((episode / "clip_plan.json").read_text())["clips"]
    assert actual[1]["spoken_text"] == "乙" * 48
    assert actual[0] == plan["clips"][0] and actual[2] == plan["clips"][2]


@pytest.mark.parametrize('legacy_address', [False, True])
def test_source_body_conflict_rolls_back_candidate_before_saving(split_episode, monkeypatch, legacy_address):
    episode, script, plan = split_episode
    if legacy_address:
        script['shots'][0].pop('index')
    quote = '林凡此时已经变成一只白狐。'
    for name, value in [('chapter_script.json', script), ('clip_plan.json', plan),
                        ('segments.json', [{'segment_id': 'seg_1', 'text': quote}])]:
        (episode / name).write_text(json.dumps(value))
    before = {name: (episode / name).read_bytes() for name in ['chapter_script.json', 'clip_plan.json']}
    context = {'entities': {'e1': '林凡'}, 'mentions': [], 'appearances': [
        {'entity_id': 'e1', 'source_quote': quote, 'description': '白狐'}]}
    monkeypatch.setattr('identity_flow_thin.resolve_chapter', lambda *a, **k: context)
    monkeypatch.setattr(repair_context, 'ledger_cast', lambda *a: {})
    def ask(*a, **k):
        if k['name'] == 'repair_source_appearance':
            assert k['name'] == 'repair_source_appearance'
            assert '"stage": 1' in a[0][0]['text']
            return {'issues': [{'stage': 1, 'source_quote': quote, 'candidate_quote': '人类男子', 'reason': '当前身体为白狐'}]}
        return {'stages': [{'origin_index': 1, 'in_frame': ['林凡'], 'actions': [], 'extras': [],
                            'event': '人类男子林凡转头'}]}
    monkeypatch.setattr(repair_judges, 'ask_json', ask)
    result = repair.repair_episode(episode.parent, 1, True, source_issues={'clip_02': '形态不符'})
    assert result['clips'] == 0 and 'source appearance conflict' in result['why']
    assert {name: (episode / name).read_bytes() for name in before} == before


def test_appearance_check_skips_unknown_and_rejects_invented_evidence(monkeypatch):
    shot = {'index': 1, 'characters': ['甲'], 'visual_prompt': '男子甲站在窗边。'}
    context = {'entities': {'e1': '甲'}, 'appearances': []}
    monkeypatch.setattr(repair_judges, 'ask_json', lambda *a, **k: pytest.fail('no source body evidence'))
    assert not repair_judges.source_appearance_check('甲看向窗外。', [shot], context)['checked']
    context['appearances'] = [{'entity_id': 'e1', 'source_quote': '甲看向窗外。', 'description': '旧抽取猜测为女性'}]
    def ask(content, *a, **k):
        assert '旧抽取猜测为女性' not in str(content)
        return {'issues': [{'stage': 1, 'source_quote': '甲是女子', 'candidate_quote': '男子', 'reason': '猜测'}]}
    monkeypatch.setattr(repair_judges, 'ask_json', ask)
    with pytest.raises(ValueError, match='unsupported evidence'):
        repair_judges.source_appearance_check('甲看向窗外。', [shot], context)


@pytest.mark.parametrize("repeated_index", [False, True])
def test_corrupted_ranges_recover_once_without_moving_other_clips(split_episode, repeated_index):
    episode, script, plan = split_episode
    expected = copy.deepcopy(plan)
    for clip in plan["clips"]:
        clip.pop("shot_parts")
        clip.update(shot_indexes=[1, 1, 1] if repeated_index else [1], seconds_estimate=39,
                    spoken_text="".join(c["spoken_text"] for c in expected["clips"]),
                    lines=[line for c in expected["clips"] for line in c["lines"]], prompt_h3="stale", prompt_h3_of="stale")
    kept = {"clip_id": "clip_04", "kind": "title_card", "shot_indexes": [2], "seconds_estimate": 3, "request_seconds": 3}
    plan["clips"].append(kept)
    path = episode / "clip_plan.json"
    path.write_text(json.dumps(plan))
    (episode / "chapter_script.json").write_text(json.dumps(script))
    before = path.read_bytes()
    result = ranges.repair_episode(episode, apply=True)
    assert result == {"changed": ["clip_01", "clip_02", "clip_03"], "skipped": {}}
    actual = json.loads(path.read_text())
    assert [c["spoken_text"] for c in actual["clips"][:3]] == [c["spoken_text"] for c in expected["clips"]]
    assert all(c["seconds_estimate"] <= c["request_seconds"] <= 15 and "prompt_h3" not in c for c in actual["clips"][:3])
    assert actual["clips"][-1] == kept
    assert (episode / "clip_plan.json.bak-split-ranges").read_bytes() == before
    assert ranges.repair_episode(episode, apply=True) == {"changed": [], "skipped": {}}


def test_a_range_without_recoverable_siblings_is_not_guessed(split_episode):
    episode, script, plan = split_episode
    plan["clips"] = plan["clips"][:1]
    plan["clips"][0].pop("shot_parts")
    plan["clips"][0]["shot_indexes"] = [1, 1, 1]
    actual, changed, skipped = ranges.recover(episode, plan, script)
    assert actual == plan and not changed and "clip_01" in skipped


def test_blocked_repack_restores_full_source_once_and_preserves_other_requests(split_episode):
    import repair_blocked_plan as blocked
    from clip_readiness import plan_issues
    episode, script, plan = split_episode
    # The legacy plan lost siblings entirely; bounded old-cut recovery cannot fix it.
    original = copy.deepcopy(script['shots'][0])
    plan['clips'] = plan['clips'][:1]
    plan['clips'][0].update(shot_indexes=[1, 1, 1], seconds_estimate=39)
    plan['clips'][0].pop('shot_parts')
    next_shot = {**copy.deepcopy(original), 'index': 2, 'segment_id': 'seg_2',
                 'turns': [{'speaker_name': '林凡', 'delivery_mode': 'visible_dialogue', 'text': '保留这句。'}]}
    script['shots'].append(next_shot)
    ctx = packing_context.context_for_plan(episode, episode.parent / 'story_bible.json', plan)
    good = packing_service.clip_entry(packing_service.pack([copy.deepcopy(next_shot)], settings=ctx['compiler_options'])[0], 'clip_10', ctx)
    good.update(prompt_h3='existing request', prompt_h3_skip=True)
    plan['clips'].append(good)
    updated, report = blocked.repack(episode, plan, script)
    assert not plan_issues(updated, script)
    assert updated['clips'][-1] == good
    assert ''.join(c['spoken_text'] for c in updated['clips'][:-1]) == ''.join(t['text'] for t in original['turns'])
    assert [c['clip_id'] for c in updated['clips']] == ['clip_01', 'clip_11', 'clip_12', 'clip_10']
    assert report['changed'] == ['clip_01']
    assert blocked.repack(episode, updated, script)[1]['changed'] == []


def test_blocked_repack_includes_siblings_instead_of_duplicating_dialogue(split_episode):
    import repair_blocked_plan as blocked
    from clip_readiness import plan_issues
    episode, script, plan = split_episode
    plan['clips'][1]['shot_parts'][0]['part'] = [1, 3]
    updated, report = blocked.repack(episode, plan, script)
    assert not plan_issues(updated, script)
    assert [c['spoken_text'] for c in updated['clips']] == [x * 48 for x in '甲乙丙']
    assert len(report['changed']) == 3


def test_batch_restores_ranges_before_translating_h3_prompts(split_episode, monkeypatch):
    import production_flow_thin as production_flow
    episode, script, plan = split_episode
    for clip in plan["clips"]:
        clip.pop("shot_parts")
        clip.update(shot_indexes=[1, 1, 1], spoken_text="甲" * 48 + "乙" * 48 + "丙" * 48, prompt_h3="stale")
    (episode / "clip_plan.json").write_text(json.dumps(plan))
    (episode / "chapter_script.json").write_text(json.dumps(script))
    batch = production_flow.Batch.__new__(production_flow.Batch)
    batch.rows, batch.fast = {1: {}}, True
    batch.args = SimpleNamespace(no_render=False, rerender=False, dry_run=False, cache_only=False, retake_failed=False)
    batch.episode_dir = lambda chapter: episode
    batch.render_status = lambda chapter: "stale"
    monkeypatch.setenv("NOVEL_LOCAL_H3_URL", "pool")
    monkeypatch.delenv("NOVEL_CLIP_SECONDS_MAX", raising=False)
    def h3_ready(chapter):
        updated = json.loads((episode / "clip_plan.json").read_text())
        assert [c["spoken_text"] for c in updated["clips"]] == [x * 48 for x in "甲乙丙"]
        assert all("prompt_h3" not in c for c in updated["clips"])
        return False  # do not render until the replacement English prompts are ready
    batch.h3_ready = h3_ready
    production_render.render(batch, 1)
    assert batch.rows[1]["render"] == "skipped (H3 prompt not ready)"


def test_build_assets_checks_only_used_images_and_rebuilds_a_bad_used_card(tmp_path, monkeypatch):
    root = tmp_path / "series_assets"
    selected = root / "characters/character_001/turnaround.jpeg"
    unused = root / "characters/character_999/turnaround.jpeg"
    old_sheet = selected.with_name("expressions.jpeg")
    backup = unused.with_name("turnaround.photoreal.jpeg")
    for path in (selected, unused, old_sheet, backup):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"broken")
    runner = uninitialized_runner()
    runner.context.novel_dir, runner.context.fast = tmp_path, True
    runner.context.settings, runner.context.provider, runner.context.bible = Settings(), object(), object()
    runner.context.profile = {"style": "2d", "frame": "16:9"}
    runner.context.frame_spec = {"text": "横屏"}
    ref = {"role": "character", "asset_id": "character_001", "path": str(selected.relative_to(tmp_path))}
    runner.context.clip_plan = {"clips": [{"references": [ref, ref]}]}
    # Deliberately contains stale secondary and unrelated records; neither is needed by this episode.
    manifest = SimpleNamespace(characters=[SimpleNamespace(primary_image=str(p.relative_to(tmp_path)), secondary_image=None)
                                            for p in (selected, unused, old_sheet)], locations=[])
    def build(factory, root, bible, characters, locations, expressions):
        assert characters == {"character_001"} and not locations and expressions is False
        assert not selected.exists()
        Image.new("RGB", (2048, 1024), "white").save(selected)
        return manifest
    monkeypatch.setattr(renderer.FramedAssetFactory, "build_selected", build)
    waits, opened = [], []
    monkeypatch.setattr('novel_manga.media.asset_repair.wait_for_inflight_redraws', lambda paths: waits.extend(paths))
    original_open = Image.open
    def observe(path, *args, **kwargs):
        opened.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Image, "open", observe)
    assert runner.build_assets() is manifest
    assert waits == [selected] and opened == [selected]
    assert all(p.read_bytes() == b"broken" for p in (unused, old_sheet, backup))


def test_missing_image_keeps_its_pending_task(tmp_path):
    path = tmp_path / "turnaround.jpeg"
    task = path.with_suffix(".jpeg.task.json")
    task.write_text("pending")
    assert renderer.ThinMediaRunner.purge_unreadable(tmp_path, paths=[path]) == []
    assert task.read_text() == "pending"


def test_quality_phase_card_does_not_require_an_unreferenced_expression_sheet(tmp_path, monkeypatch):
    asset = "character_001_old"
    card = tmp_path / "series_assets/characters" / asset / "turnaround.jpeg"
    card.parent.mkdir(parents=True)
    Image.new("RGB", (2048, 1024), "white").save(card)
    runner = uninitialized_runner()
    runner.context.novel_dir, runner.context.fast = tmp_path, False
    runner.context.settings, runner.context.provider = Settings(), object()
    runner.context.bible = StoryBible(novel_title="测试", genre="generic", visual_style="2d", palette="蓝", style_fingerprint="test",
                             characters=[Character(name="林凡", appearance="黑发", wardrobe="白衣")], locations=[])
    runner.context.profile, runner.context.frame_spec = {"style": "2d", "frame": "16:9"}, {"text": "横屏"}
    runner.context.clip_plan = {"clips": [{"references": [{"role": "character", "asset_id": asset,
                                                  "path": str(card.relative_to(tmp_path))}]}]}
    monkeypatch.setattr(renderer.FramedAssetFactory, "ensure_card", lambda *a, **kw: pytest.fail("phase card already exists"))
    runner.build_assets()
    assert card.exists() and not card.with_name("expressions.jpeg").exists()
