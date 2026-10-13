"""An input patch clears wearing; an omitted historical field still inherits."""
import copy
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode.input_candidate import prepare_candidate
from novel_manga.story.scene import resolve_scene, SceneContext
from novel_manga.util import atomic_write_json


def candidate(tmp_path, patch, *, alias=False, inherit_last=False, scope=None, return_case=False):
    ctx = SimpleNamespace(episode_dir=tmp_path/'book/book_1', novel_dir=tmp_path/'book',
                          run_dir=tmp_path, work_dir=tmp_path/'sandbox', input_mode='script')
    atomic_write_json(ctx.novel_dir/'story_bible.json', {'characters': [{'name': '甲'}, {'name': '乙'}]})
    script = {'shots': [
        {'index': 1, 'characters': ['甲', '乙'], 'wears': {'甲': '银甲', '乙': '披风'}, 'turns': []},
        {'index': 2, 'characters': ['甲', '乙'], 'in_frame': ['甲'], 'listeners': ['乙'],
         'props': ['空甲'], 'independent_props': ['空甲'], 'turns': []},
        {'index': 3, 'characters': ['甲'], 'wears': {'甲': '新甲'}, 'turns': []}]}
    if alias:
        atomic_write_json(ctx.novel_dir/'bible_aliases.json', {'小甲': '甲'})
        script['shots'][1].update(characters=['小甲', '乙'], in_frame=['小甲'])
    if inherit_last:
        script['shots'][2].pop('wears')
    plan = {'clips': [{'clip_id': f'clip_{i:02d}', 'kind': 'video', 'shot_indexes': [i], 'references': []}
                       for i in range(1, 4)]}
    row = {'clip_id': 'clip_02', 'action': 'input', 'reason': 'source-backed clear',
           'source_quote': '甲乙解下装备。', 'shots': [{'index': 2, **patch}]}
    if scope is not None:
        row['clip_ids'] = scope
    original = copy.deepcopy((row, script))
    prepared = prepare_candidate(ctx, row, plan, script, plan['clips'][1], {2}, scope, None)
    assert (row, script) == original
    return prepared.compilation if return_case else prepared.compilation.candidate_script


def test_alias_clear_uses_current_canonical_identity(tmp_path):
    script = candidate(tmp_path, {'wears': {}}, alias=True)
    assert script['shots'][1]['wears'] == {'甲': None, '乙': None}
    result = resolve_scene(script, SceneContext(aliases={'小甲': '甲'})).shots
    assert result[1]['wears']['甲'] is None


def test_declared_wearing_dependency_enters_compilation_targets(tmp_path):
    case = candidate(tmp_path, {'wears': {}}, inherit_last=True,
                     scope=['clip_02', 'clip_03'], return_case=True)
    assert case.targets == {'clip_02', 'clip_03'}


def test_undeclared_wearing_dependency_does_not_expand_targets(tmp_path):
    with pytest.raises(ValueError, match='wearing.*outside.*clip_03'):
        candidate(tmp_path, {'wears': {}}, inherit_last=True, return_case=True)


def test_incomplete_declared_scope_fails_before_input_receipt_or_model(tmp_path):
    with pytest.raises(ValueError, match='wearing.*outside.*clip_03'):
        candidate(tmp_path, {'wears': {}}, inherit_last=True, scope=['clip_02'], return_case=True)
    assert not list(tmp_path.rglob('clip_*-*.json'))


def test_declared_dependency_still_needs_original_video_allowance(tmp_path, monkeypatch):
    from novel_manga.application.repair import history
    monkeypatch.setattr(history, 'remaining_takes', lambda directory, cid, **kw: 0 if cid == 'clip_03' else 2)
    with pytest.raises(ValueError, match='remaining video budget: clip_03'):
        candidate(tmp_path, {'wears': {}}, inherit_last=True,
                  scope=['clip_02', 'clip_03'], return_case=True)


@pytest.mark.parametrize('clear', [{}, None])
def test_clear_patch_survives_inheritance_and_preserves_independent_props(tmp_path, clear):
    script = candidate(tmp_path, {'wears': clear})
    assert script['shots'][1]['wears'] == {'甲': None, '乙': None}
    resolved = resolve_scene(script, SceneContext()).shots
    assert resolved[0]['wears'] == {'甲': '银甲', '乙': '披风'}
    assert resolved[1]['wears'] == {'甲': None, '乙': None}
    assert resolved[1]['props'] == resolved[1]['independent_props'] == ['空甲']
    assert resolved[2]['wears']['甲'] == '新甲'


def test_omitted_patch_keeps_history_inheritance_and_named_clear_is_selective(tmp_path):
    omitted = resolve_scene(candidate(tmp_path, {'visual_prompt': '两人望向门口'}), SceneContext()).shots
    assert omitted[1]['wears'] == {'甲': '银甲', '乙': '披风'}
    selective = resolve_scene(candidate(tmp_path, {'wears': {'甲': None}}), SceneContext()).shots
    assert selective[1]['wears'] == {'甲': None, '乙': '披风'}


def test_wearing_rebuild_blocks_unrequested_inherited_changes(tmp_path, monkeypatch):
    from support.split_episode import split_episode
    from novel_manga.application.packing import context, service
    from novel_manga.application.repair.flow import rebuild_clips
    from novel_manga.util import read_json
    directory, original, old_plan = split_episode.__wrapped__(tmp_path, monkeypatch)
    base = original['shots'][0]
    script = {'shots': [{**copy.deepcopy(base), 'index': i, 'origin_index': i,
                        'turns': [], 'characters': ['林凡'], 'in_frame': ['林凡'],
                        **({'wears': {'林凡': '银甲'}} if i == 1 else {})} for i in (1, 2, 3)]}
    for shot in script['shots'][1:]:
        shot.pop('wears', None)
    atomic_write_json(directory/'chapter_script.json', script)
    ctx = context.context_for_plan(directory, directory.parent/'story_bible.json', old_plan)
    resolved = service.prepared_shots(script, directory, identity_data=ctx['identity_data'])
    entries = [service.clip_entry({'kind': 'video', 'location': s['location'], 'shots': [s], 'seconds': 5},
                                  f'clip_{i:02d}', ctx) for i, s in enumerate(resolved, 1)]
    plan = {**old_plan, 'clips': entries}
    revised = copy.deepcopy(script); revised['shots'][1]['wears'] = {'林凡': None}
    with pytest.raises(ValueError, match='wearing.*outside.*clip_03'):
        rebuild_clips(directory, directory.parent/'story_bible.json', revised, plan, {'clip_02'}, preserve_timing=True)
    result, changed = rebuild_clips(directory, directory.parent/'story_bible.json', revised, plan,
                                    {'clip_02', 'clip_03'}, preserve_timing=True)
    assert changed == ['clip_02', 'clip_03']
    assert result['clips'][0] == plan['clips'][0]
    assert all(c['wears_bindings'][0]['wears']['林凡'] is None for c in result['clips'][1:])
    assert read_json(directory/'chapter_script.json') == script
