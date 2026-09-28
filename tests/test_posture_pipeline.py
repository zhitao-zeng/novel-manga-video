"""Posture reads reach packing/H3 and repair uses a candidate ledger before publishing it."""
import copy
import json

import pytest

from novel_manga.application.packing import posture, service, context
from novel_manga.application.repair import flow, publication
from novel_manga.application.rendering import h3
from novel_manga.repair.proposal import RepairProposal
from support.split_episode import split_episode
from support.posture import answer


@pytest.fixture
def episode(tmp_path, monkeypatch):
    directory, original, plan = split_episode.__wrapped__(tmp_path, monkeypatch)
    base = original['shots'][0]
    shots = [{**copy.deepcopy(base), 'index': i, 'origin_index': i, 'clip_hint': f'clip_{i:02d}',
              'in_frame': ['林凡'], 'turns': [copy.deepcopy(base['turns'][0])],
              'visual_prompt': picture, 'motion_prompt': '林凡讲述经过', 'end_state': ''}
             for i, picture in enumerate(['林凡坐在窗边椅子上', '林凡举起一根手指',
                                          '次日林凡站在窗边', '林凡看向门口'], 1)]
    script = {'shots': shots}
    (directory / 'chapter_script.json').write_text(json.dumps(script))
    (directory / 'segments.json').write_text('[]')
    responses = [answer(1, '坐', name='林凡', boundary='reset', entry='reset', where='窗边椅子上'),
                 answer(2, name='林凡'), answer(3, '站', name='林凡', boundary='reset', entry='reset'),
                 answer(4, name='林凡')]
    states = posture.fill(directory, script=script, ask=lambda p, s: {'stages': responses})
    ctx = context.context_for_plan(directory, directory.parent / 'story_bible.json', plan)
    packed, _ = service.compile_plan(script, ctx)
    assert len(packed['clips']) == 4
    for clip in packed['clips']:
        clip['prompt_h3'] = 'existing cached English ' + clip['clip_id']
    (directory / 'clip_plan.json').write_text(json.dumps(packed))
    return directory, script, packed, states, responses


def test_compiled_request_contains_only_confirmed_inherited_pose_and_reaches_h3(episode, monkeypatch):
    directory, script, plan, states, _ = episode
    assert '人物姿态' not in plan['clips'][0]['prompt']
    assert '人物姿态：林凡坐着（窗边椅子上）' in plan['clips'][1]['prompt']
    assert '人物姿态' not in plan['clips'][2]['prompt']
    assert '人物姿态：林凡站着' in plan['clips'][3]['prompt']
    before = copy.deepcopy(script)
    other, _ = service.compile_plan(script, context.context_for_plan(directory, directory.parent / 'story_bible.json', plan))
    assert [c['prompt'] for c in other['clips']] == [c['prompt'] for c in plan['clips']]
    assert script == before
    captured = []
    def translate(parts, schema, **kwargs):
        captured.append(parts[0]['text'])
        if 'manners' in schema.get('properties', {}):
            return {'manners': ['The line is delivered naturally.']}
        return {'shots': ['A young man remains seated on a chair by the window and raises a finger.']}
    monkeypatch.setattr(h3, 'ask_json', translate)
    clip = copy.deepcopy(other['clips'][1])
    assert h3.convert(clip, tries=1)
    assert any('坐着（窗边椅子上）' in p for p in captured)  # names have become Subject tags
    assert 'remains seated' in clip['prompt_h3']


def test_noop_repair_keeps_requests_cache_and_never_asks_again(episode, monkeypatch):
    directory, script, plan, states, _ = episode
    monkeypatch.setattr(posture, 'ask_json', lambda *a, **k: pytest.fail('unchanged ledger must be reused'))
    after, changed = flow.rebuild_clips(directory, directory.parent / 'story_bible.json', script, plan, {'clip_01'})
    assert changed == [] and after == plan
    assert json.loads((directory / posture.FILE).read_text()) == states


def test_repair_updates_dependent_clip_but_not_next_scene_or_saved_ledger(episode, monkeypatch):
    directory, script, plan, states, responses = episode
    script = copy.deepcopy(script)
    script['shots'][0]['visual_prompt'] = '林凡站在窗边'
    responses = copy.deepcopy(responses)
    responses[0]['people'][0]['start'] = {'posture': '站', 'where': '窗边', 'quote': '站在窗边'}
    monkeypatch.setattr(posture, 'ask_json', lambda *a, **k: {'stages': responses})
    report = {}
    after, changed = flow.rebuild_clips(directory, directory.parent / 'story_bible.json', script, plan,
                                      {'clip_01'}, repack_report=report)
    assert changed == ['clip_01', 'clip_02']
    assert '人物姿态：林凡站着（窗边）' in after['clips'][1]['prompt']
    assert after['clips'][2:] == plan['clips'][2:]
    assert 'prompt_h3' not in after['clips'][1]
    assert json.loads((directory / posture.FILE).read_text()) == states  # proposal only
    payload = {'script': script, 'plan': after, 'notes': {}, 'changes': {},
               'posture_states': report['_posture_states']}
    candidate = RepairProposal.from_result({'changed': changed, 'proposal': payload})
    publication.write_artifacts(directory, candidate)
    assert json.loads((directory / posture.FILE).read_text()) == candidate.posture_states
    assert not (directory / 'repair_history/history.json').exists()  # writing is not a video attempt


def test_ambiguous_repair_candidate_never_overwrites_plan_or_ledger(episode, monkeypatch):
    directory, script, plan, states, responses = episode
    before = {p.name: p.read_bytes() for p in directory.glob('*.json')}
    script = copy.deepcopy(script)
    script['shots'][0]['visual_prompt'] = '林凡看向窗外'
    responses = copy.deepcopy(responses)
    responses[0]['people'][0]['start'] = {'posture': '未写明', 'where': '', 'quote': ''}
    responses[1]['boundary'] = 'unknown'
    monkeypatch.setattr(posture, 'ask_json', lambda *a, **k: {'stages': responses})
    with pytest.raises(ValueError, match='姿态交接需修正'):
        flow.rebuild_clips(directory, directory.parent / 'story_bible.json', script, plan, {'clip_01'})
    assert {p.name: p.read_bytes() for p in directory.glob('*.json')} == before
