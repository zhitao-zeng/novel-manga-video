"""Actual authored plans reach image admission before any simulated video request."""
import copy
from types import SimpleNamespace

import pytest
from PIL import Image

from novel_manga.application.agents.episode import contracts
from novel_manga.application.identity.phases import POLICY as PHASE_POLICY
from novel_manga.application.packing.service import clip_entry
from novel_manga.application.preparation import readiness, wearer_cards
from novel_manga.models.bible import Character, Prop, StoryBible
from novel_manga.util import atomic_write_json, read_json
from support.episode_agent import CATALOG, SOURCES, episode
from support.render_context import uninitialized_runner


def authored(tmp_path, *, first_wears=None, second_wears=None, independent=False, count=2):
    draft = episode(turns=[], silent=True, voice=False)
    draft['chapter_script']['shots'] = draft['chapter_script']['shots'][:count]
    draft['clips'] = draft['clips'][:count]
    draft['chapter_script']['shots'][0]['wears'] = (
        {'甲': '黑色披风'} if first_wears is None else first_wears)
    if second_wears is not None:
        draft['chapter_script']['shots'][1].update(wears=second_wears, in_frame=['乙'])
    catalog = copy.deepcopy(CATALOG)
    if independent:
        draft['chapter_script']['shots'][0].update(props=['银甲'], independent_props=['银甲'])
        catalog.append({'id': 'armor', 'role': 'prop', 'name': '银甲', 'path': 'cards/armor.jpeg'})
        draft['clips'][0]['reference_ids'].append('armor')
        draft['clips'][0]['prompt_h3'] = draft['clips'][0]['prompt_h3'].replace(
            '\n\nsummary:', '\n<Subject 4> is the physical object shown in <Picture 4>.\n\nsummary:')
    script, plan = contracts.adopt(draft, catalog=catalog, source_segments=SOURCES,
                                   profile={}, target_seconds=(15 * count, 15 * count))
    directory = tmp_path / 'book/book_1'
    directory.mkdir(parents=True)
    for ref in catalog:
        if ref['role'] == 'voice':
            continue
        image = directory.parent / ref['path']
        image.parent.mkdir(exist_ok=True)
        Image.new('RGB', (32, 32), 'black').save(image)
    atomic_write_json(directory / 'chapter_script.json', script)
    atomic_write_json(directory / 'clip_plan.json', plan)
    atomic_write_json(directory.parent / 'story_bible.json', {
        'props': [{'name': '黑色披风', 'appearance': '黑色布披风'}]})
    return directory, script, plan


def runner_for(directory, script, plan, monkeypatch):
    runner = uninitialized_runner()
    runner.context.episode_dir, runner.context.novel_dir = directory, directory.parent
    runner.context.work = directory / 'work'
    runner.context.clip_plan, runner.context.script = plan, script
    runner.context.settings = SimpleNamespace(local_h3_base_url='')
    runner.context.options.max_attempts, runner.context.options.workers = 1, 1
    generated = []
    monkeypatch.setattr(runner, 'build_assets', lambda **kwargs: None)

    def generate(clip, attempt):
        generated.append(clip['clip_id'])
        return directory / (clip['clip_id'] + '.mp4')

    monkeypatch.setattr(runner, 'generate_clip', generate)
    monkeypatch.setattr(runner, 'analyse_clip', lambda clip, video: {
        'passed': True, 'video': str(video), 'cer': 0, 'max_volume_db': -5, 'issues': []})
    monkeypatch.setattr(runner, 'assemble', lambda results: {'thin_passed': True})
    monkeypatch.setattr('novel_manga.application.repair.history.record_render', lambda *args: None)
    return runner, generated


def character(clip, name='甲'):
    return next(ref for ref in clip['references'] if ref['role'] == 'character' and ref['name'] == name)


@pytest.mark.parametrize('verdict', ['matches', 'different'])
def test_source_adoption_checks_actual_image_and_blocks_only_its_wearer(tmp_path, monkeypatch, verdict):
    directory, script, plan = authored(tmp_path)
    assert plan['clips'][0]['prompt'] == ''
    assert not any(ref['role'] == 'prop' for ref in plan['clips'][0]['references'])
    seen = []

    def reply(parts, schema, **kwargs):
        seen.append(parts)
        return {'wearing': verdict, 'evidence': '当前图中的披风检查结果'}

    monkeypatch.setattr(wearer_cards, 'ask_json', reply)
    runner, generated = runner_for(directory, script, plan, monkeypatch)
    result = runner.run()
    assert len(seen) == 1
    assert any(part.get('type') == 'image_url' for part in seen[0])
    assert '人物：甲' in seen[0][0]['text']
    expected_blocked = [] if verdict == 'matches' else ['clip_01']
    assert list(result['blocked_clips']) == expected_blocked
    assert generated == (['clip_01', 'clip_02'] if verdict == 'matches' else ['clip_02'])
    assert result['clips'][1]['selected']['passed'] is True
    assert bool(readiness.render_risks(plan['clips'][0], directory.parent)[0]) == (verdict != 'matches')
    assert list(read_json(directory / readiness.REPORT)['blocked_clips']) == expected_blocked


def test_reference_paths_override_checks_the_new_image_despite_unchanged_phase_id(tmp_path, monkeypatch):
    directory = tmp_path / 'book/book_1'
    directory.mkdir(parents=True)
    book = directory.parent
    phase = {'from': 1, 'to': 1, 'asset_id': 'character_001-p1', 'wears': '黑色披风'}
    atomic_write_json(book / 'series_assets/phases.json', {
        'policy': PHASE_POLICY, 'characters': {'甲': [phase]}})
    for name, color in [('correct.jpeg', 'black'), ('replacement.jpeg', 'white')]:
        Image.new('RGB', (32, 32), color).save(book / name)
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d', palette='蓝',
                       style_fingerprint='test', characters=[Character(name='甲', appearance='黑发', wardrobe='便装')],
                       locations=['房间：木门'], props=[Prop(name='黑色披风', category='服装', wearable=True)])
    shot = {'index': 1, 'characters': ['甲'], 'in_frame': ['甲'], 'wears': {'甲': '黑色披风'},
            'location': '房间', 'turns': [], 'visual_prompt': '甲站在门边', 'motion_prompt': '甲抬眼',
            'end_state': '甲望向门口', 'shot_scale': '中景', 'camera': '平视固定', 'light': '室内灯'}
    context = {'episode_dir': directory, 'bible': bible, 'location_map': {'房间': '房间：木门'},
               'overrides': {}, 'grammar': None, 'frame': None}
    raw = {'kind': 'video', 'location': '房间', 'seconds': 10, 'shots': [shot]}
    original = clip_entry(raw, 'clip_01', context, {'reference_paths': {'甲': 'correct.jpeg'}})
    replacement = clip_entry(raw, 'clip_01', context, {'reference_paths': {'甲': 'replacement.jpeg'}})
    assert character(original)['asset_id'] == character(replacement)['asset_id'] == phase['asset_id']
    answers = iter(['matches', 'different'])
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *args, **kwargs: {
        'wearing': next(answers), 'evidence': '核对当前图片'})
    wearer_cards.check_references(book, readiness.worn_reference_pairs({'clips': [original]}))
    assert readiness.render_risks(original, book)[0] == []
    wearer_cards.check_references(book, readiness.worn_reference_pairs({'clips': [replacement]}))
    block, _ = readiness.render_risks(replacement, book)
    assert block and '实际参考图与穿戴要求不同' in block[0]
    assert not wearer_cards.verified(book, character(replacement), '黑色披风')


def test_saved_source_plan_uses_its_existing_script_without_rewriting_the_plan(tmp_path, monkeypatch):
    directory, script, plan = authored(tmp_path)
    for clip in plan['clips']:
        clip.pop('wears_bindings')
    before = copy.deepcopy(plan)
    atomic_write_json(directory / 'clip_plan.json', plan)
    seen = []
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *args, **kwargs: seen.append(args) or {
        'wearing': 'different', 'evidence': '当前角色图缺少披风'})
    runner, generated = runner_for(directory, script, plan, monkeypatch)
    result = runner.run()
    assert len(seen) == 1 and generated == ['clip_02']
    assert list(result['blocked_clips']) == ['clip_01']
    assert plan == before and read_json(directory / 'clip_plan.json') == before


@pytest.mark.parametrize('bindings', [[], [{}], [{'甲': None}]])
def test_explicit_empty_removed_or_independent_states_do_not_revive_old_chinese(tmp_path, monkeypatch, bindings):
    directory, script, plan = authored(tmp_path, first_wears={}, independent=True)
    clip = plan['clips'][0]
    clip['prompt'] = '【阶段一】穿戴状态：甲穿着银甲；'
    clip['wears_bindings'] = [{'stage': 1, 'source_stage': 1, 'wears': wears, 'in_frame': ['甲']}
                             for wears in bindings]
    prop = next(ref for ref in clip['references'] if ref['role'] == 'prop')
    assert prop['independent'] is True and 'wearers' not in prop
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *args, **kwargs: pytest.fail('there is no worn target'))
    assert list(readiness.worn_reference_pairs(plan)) == []
    assert readiness.render_risks(clip, directory.parent)[0] == []
    runner, generated = runner_for(directory, script, plan, monkeypatch)
    assert runner.run()['blocked_clips'] == {}
    assert generated == ['clip_01', 'clip_02']


def test_same_image_and_target_reuse_review_but_changed_image_or_prop_rechecks(tmp_path, monkeypatch):
    directory, _, plan = authored(tmp_path)
    book = directory.parent
    ref = character(plan['clips'][0])
    calls = []
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *args, **kwargs: calls.append(args) or {
        'wearing': 'matches', 'evidence': '当前图穿戴匹配'})
    duplicate_plan = {'clips': [plan['clips'][0], plan['clips'][0]]}
    for _ in range(2):
        wearer_cards.check_references(book, readiness.worn_reference_pairs(duplicate_plan))
    assert len(calls) == 1
    Image.new('RGB', (64, 64), 'white').save(book / ref['path'])
    assert not wearer_cards.verified(book, ref, '黑色披风')
    wearer_cards.check_references(book, readiness.worn_reference_pairs(duplicate_plan))
    assert len(calls) == 2
    atomic_write_json(book / 'story_bible.json', {
        'props': [{'name': '黑色披风', 'appearance': '蓝色丝绸披风'}]})
    assert not wearer_cards.verified(book, ref, '黑色披风')
    wearer_cards.check_references(book, readiness.worn_reference_pairs(duplicate_plan))
    assert len(calls) == 3
    wearer_cards.check_references(book, [(ref, '铁拳套')])
    assert len(calls) == 4 and wearer_cards.verified(book, ref, '铁拳套')


def test_check_error_keeps_only_affected_clip_blocked_and_recovers_without_changing_image(tmp_path, monkeypatch):
    directory, script, plan = authored(tmp_path, second_wears={'乙': '银甲'}, count=3)
    seen = []

    def reply(parts, schema, **kwargs):
        wearer = '甲' if '人物：甲' in parts[0]['text'] else '乙'
        seen.append(wearer)
        if wearer == '甲' and seen.count('甲') == 1:
            raise TimeoutError('simulated unavailable judge')
        return {'wearing': 'matches', 'evidence': '当前图片穿戴匹配'}

    monkeypatch.setattr(wearer_cards, 'ask_json', reply)
    runner, generated = runner_for(directory, script, plan, monkeypatch)
    failed = runner.run()
    assert list(failed['blocked_clips']) == ['clip_01']
    assert generated == ['clip_02', 'clip_03']
    assert seen == ['甲', '乙']
    assert wearer_cards.reviewed(directory.parent, character(plan['clips'][0]), '黑色披风') is None
    generated.clear()
    recovered = runner.run()
    assert recovered['blocked_clips'] == {} and recovered['status'] == 'assembled'
    assert generated == ['clip_01', 'clip_02', 'clip_03']
    assert seen == ['甲', '乙', '甲']


def test_missing_reference_blocks_before_vision_and_video_without_affecting_other_clip(tmp_path, monkeypatch):
    directory, script, plan = authored(tmp_path)
    character(plan['clips'][0])['path'] = 'cards/missing.jpeg'
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *args, **kwargs: pytest.fail('missing reference cannot be checked'))
    runner, generated = runner_for(directory, script, plan, monkeypatch)
    result = runner.run()
    assert generated == ['clip_02']
    assert list(result['blocked_clips']) == ['clip_01']
    assert any(reason.startswith('asset:') for reason in result['blocked_clips']['clip_01'])
    assert result['clips'][0]['attempts'] == []


def test_explicit_accepted_wearer_risk_keeps_the_existing_waiver_policy(tmp_path, monkeypatch):
    directory, script, plan = authored(tmp_path)
    atomic_write_json(directory.parent / readiness.ACCEPTED, {'wearer_card': ['甲']})
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *args, **kwargs: {
        'wearing': 'different', 'evidence': '图中没有指定披风'})
    runner, generated = runner_for(directory, script, plan, monkeypatch)
    result = runner.run()
    assert result['blocked_clips'] == {} and result['status'] == 'assembled'
    assert generated == ['clip_01', 'clip_02']
    assert not wearer_cards.verified(directory.parent, character(plan['clips'][0]), '黑色披风')
    assert readiness.render_risks(plan['clips'][0], directory.parent)[0]
