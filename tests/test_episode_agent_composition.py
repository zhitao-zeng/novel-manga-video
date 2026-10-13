import copy

import pytest

from novel_manga.application.agents.episode import contracts
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.story.dialogue import BOUND_SPEECH
from support.episode_agent import CATALOG, SOURCES, episode, shot
from support.episode_existing import prepared
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.repair import history


def adopt(draft, **options):
    return contracts.adopt(draft, catalog=CATALOG, source_segments=SOURCES, profile=options)


def structured(draft):
    for clip in draft['clips']:
        clip.pop('prompt_h3')
        clip['h3'] = {'shots': [
            'Close view of <Subject 2> lifting the cup. '
            + ' '.join(f'<DIALOGUE_{i}>' for i in range(1, len(draft['chapter_script']['shots'][n-1]['turns'])+1))
            + ' The hand settles on the table.' for n in clip['shot_indexes']],
            'soundscape': 'A steady breeze.'}
    return draft


def test_structured_shots_bind_exact_dialogue_once_at_authored_positions():
    turns = [{'speaker_name': '甲', 'text': '你来啦，', 'delivery_mode': 'visible_dialogue'},
             {'speaker_name': '甲', 'text': '真的是你！！', 'delivery_mode': 'visible_dialogue'}]
    draft = structured(episode(turns=turns))
    before = copy.deepcopy(draft)
    script, plan = adopt(draft)
    request = plan['clips'][0]['prompt_h3']
    spoken = list(BOUND_SPEECH.finditer(request))
    assert [s[4] for s in spoken] == ['你来啦,.', '真的是你!!']
    assert len(spoken) == 2 and all(s.groups()[:2] == ('2', '1') for s in spoken)
    assert request.index('lifting the cup') < spoken[0].start() < request.index('hand settles')
    assert request.count('timbre referenced from <Audio 1>') == 1
    assert '<Subject 3> is the setting in <Picture 2>' in request
    assert 'Planned duration: 15 seconds.' in request
    assert [{k: row[k] for k in turns[0]} for row in script['shots'][0]['turns']] == turns
    assert draft == before


def test_draft_check_reports_all_bad_visible_speakers_and_duration_without_adopting():
    draft = structured(episode())
    draft['chapter_script']['shots'][0]['in_frame'] = []
    draft['chapter_script']['shots'][3]['in_frame'] = []
    for clip in draft['clips']:
        clip['request_seconds'] = 5
    issues = contracts.collect_format_issues(draft, catalog=CATALOG, source_segments=SOURCES, profile={})
    assert [i.get('shot_indexes') for i in issues[:2]] == [[1], [4]]
    assert '40s' in issues[-1]['binding_or_timing_issue']
    with pytest.raises(ValueError, match='visible speaker'):
        adopt(draft)


def test_action_description_spelling_keeps_authored_ownership_and_text():
    draft = structured(episode())
    draft['chapter_script']['shots'][0]['actions'] = [
        {'actor': '甲', 'description': '甲抬手示意。', 'target': '乙'}]
    before = copy.deepcopy(draft)
    script, _ = adopt(draft)
    assert script['shots'][0]['actions'] == [{'actor': '甲', 'action': '甲抬手示意。', 'target': '乙'}]
    assert draft == before
    draft['chapter_script']['shots'][0]['actions'][0]['action'] = '甲放下手。'
    assert adopt(draft)[0]['shots'][0]['actions'][0]['action'] == '甲放下手。'


def test_structured_multiple_shots_derive_cuts_without_agent_writing_them():
    draft = episode()
    draft['chapter_script']['shots'].insert(1, shot(2))
    for n, row in enumerate(draft['chapter_script']['shots'], 1): row['index'] = n
    draft['chapter_script']['shots'][0]['duration_seconds'] = 6
    draft['chapter_script']['shots'][1]['duration_seconds'] = 9
    draft['clips'][0]['shot_indexes'] = [1, 2]
    for n, clip in enumerate(draft['clips'][1:], 3): clip['shot_indexes'] = [n]
    _, plan = adopt(structured(draft))
    request = plan['clips'][0]['prompt_h3']
    assert '[Shot 2] At 00:06.000, Planned duration: 9 seconds.' in request
    assert request.count('你来啦') == 2


def test_clip_wide_dialogue_numbers_are_normalized_without_changing_speaker_or_timing():
    draft=episode()
    draft['chapter_script']['shots'].insert(1,shot(2,turns=[{
        'speaker_name':'乙','text':'你也来了。','delivery_mode':'visible_dialogue'}]))
    draft['chapter_script']['shots'][1]['characters']=['甲','乙']
    draft['chapter_script']['shots'][1]['in_frame']=['甲','乙']
    for n,row in enumerate(draft['chapter_script']['shots'],1):row['index']=n
    draft['chapter_script']['shots'][0]['duration_seconds']=6
    draft['chapter_script']['shots'][1]['duration_seconds']=9
    draft['clips'][0]['shot_indexes']=[1,2]
    for n,clip in enumerate(draft['clips'][1:],3):clip['shot_indexes']=[n]
    structured(draft)
    draft['clips'][0]['h3']['shots'][1]=draft['clips'][0]['h3']['shots'][1].replace('<DIALOGUE_1>','<DIALOGUE_2>')
    before=copy.deepcopy(draft)
    _,plan=adopt(draft);request=plan['clips'][0]['prompt_h3']
    assert request.index('[Shot 1]')<request.index('你来啦')<request.index('[Shot 2]')<request.index('你也来了')
    assert '[Shot 2] At 00:06.000, Planned duration: 9 seconds.'in request
    assert '(S1)'in request and '(S2)'in request
    assert '<DIALOGUE_'not in request and draft==before


@pytest.mark.parametrize('bodies',[
    ['A view. <DIALOGUE_1>','Another view. <DIALOGUE_3>'],
    ['A view. <DIALOGUE_1> <DIALOGUE_2>','Another view.'],
    ['A view. <DIALOGUE_1>','Another view. <DIALOGUE_1> <DIALOGUE_2>']])
def test_global_numbering_cannot_hide_missing_extra_or_misplaced_dialogue(bodies):
    from novel_manga.application.agents.episode.requests import compose_request
    clip={'stage_count':2,'shot_indexes':[1,2],'dialogue_bindings':[
        {'stage':1,'speaker_name':'甲','delivery_mode':'visible_dialogue','text':'甲的台词'},
        {'stage':2,'speaker_name':'乙','delivery_mode':'visible_dialogue','text':'乙的台词'}]}
    with pytest.raises(ValueError,match='dialogue slots'):
        compose_request(clip,{'shots':bodies})


@pytest.mark.parametrize('body', ['Empty hall.', '<DIALOGUE_1> <DIALOGUE_1>',
    '<DIALOGUE_2>', '<DIALOGUE_1> <d>[Chinese] 偷加台词。</d>'])
def test_missing_duplicate_or_invented_speech_is_rejected(body):
    draft = structured(episode())
    draft['clips'][0]['h3']['shots'] = [body]
    with pytest.raises(ValueError, match='dialogue|speech'):
        adopt(draft)


def test_structured_offscreen_and_postmix_keep_delivery_boundaries():
    draft = structured(episode(turns=[{'speaker_name': '甲', 'text': '你来啦。',
                                      'delivery_mode': 'offscreen_dialogue'}]))
    _, plan = adopt(draft)
    assert 'says in an off-screen voiceover <d>' in plan['clips'][0]['prompt_h3']
    for row, clip in zip(draft['chapter_script']['shots'], draft['clips']):
        row['turns'][0]['inner_monologue'] = True
        clip['h3']['shots'] = ['A close view of <Subject 2> silently lifting the cup.']
    _, plan = adopt(draft, inner_voice_delivery='postmix')
    request = plan['clips'][0]['prompt_h3']
    assert '<d>' not in request and '<Audio ' not in request
    assert plan['clips'][0]['inner_voice']['text'] == '你来啦。'


@pytest.mark.parametrize('sound', ['Wind. <Subject 2> (S1) says <d>[Chinese] 偷加台词。</d>',
                                 'Wind. <DIALOGUE_1>'])
def test_soundscape_cannot_add_or_duplicate_native_speech(sound):
    draft = structured(episode())
    draft['clips'][0]['h3']['soundscape'] = sound
    with pytest.raises(ValueError, match='soundscape.*speech|soundscape.*dialogue'):
        adopt(draft)


def test_prepared_script_accepts_structured_directions_without_changing_plan(tmp_path, monkeypatch):
    ctx, old, prompts = prepared(tmp_path, monkeypatch)
    prompts['clip_01'] = {'shots': ['<Subject 2> lifts the cup. <DIALOGUE_1> The cup settles.']}
    errors, script, plan = EpisodeTools(ctx).drafts.validate_requests({'prompts': prompts})
    assert errors == []
    first = plan['clips'][0]
    assert first['generation_seed'] == old['clips'][0]['generation_seed']
    assert first['references'] == old['clips'][0]['references']
    assert first['dialogue_bindings'] == old['clips'][0]['dialogue_bindings']
    assert first['shot_timing'] == old['clips'][0]['shot_timing']
    assert first['prompt_h3'].count('你来啦') == 1
    assert plan['clips'][1]['prompt_h3'] == prompts['clip_02']


def test_structured_repair_preserves_existing_reference_modes_and_take_counts(tmp_path, monkeypatch):
    ctx, before, _ = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'feedback': {'clip_01': 'change hand grip'}})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [{
        'clip_id': 'clip_01', 'action': 'edit', 'reason': 'use a firm grip',
        'h3': {'shots': ['Close view of <Subject 2> firmly gripping the cup. <DIALOGUE_1> The hand stays on the table.'],
               'soundscape': 'Wind outside.'}}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is True and result['changed'] == ['clip_01']
    after = read_json(ctx.episode_dir/'clip_plan.json')
    clip = after['clips'][0]
    assert 'firmly gripping' in clip['prompt_h3']
    assert '<Subject 2>: partially_preserved' in clip['prompt_h3']
    assert clip['dialogue_bindings'] == before['clips'][0]['dialogue_bindings']
    assert clip['shot_timing'] == before['clips'][0]['shot_timing']
    assert clip['references'] == before['clips'][0]['references']
    assert after['clips'][1:] == before['clips'][1:]
    assert history.generation_counts(ctx.episode_dir) == {}


def test_structured_repair_cannot_add_speech_or_unknown_subject(tmp_path, monkeypatch):
    ctx, before, _ = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'feedback': {'clip_01': 'change hand grip'}})
    for body in ['<DIALOGUE_1> <d>[Chinese] 新增的话。</d>', '<Subject 77> lifts the cup. <DIALOGUE_1>']:
        atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [{
            'clip_id': 'clip_01', 'action': 'edit', 'reason': 'invalid edit', 'h3': {'shots': [body]}}]})
        result = EpisodeTools(ctx).dispatch('apply')
        assert not result['ok'] and not result['changed']
        assert result['blocked']['clip_01'] != "'prompt_h3'"
        assert read_json(ctx.episode_dir/'clip_plan.json') == before


@pytest.fixture
def missing_recompiled_request(tmp_path, monkeypatch):
    ctx, plan, prompts = prepared(tmp_path, monkeypatch)
    plan['clips'][0].pop('prompt_h3')
    plan['clips'][0].pop('prompt_h3_of')
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'blocked_clips': {'clip_01': ['H3 lane has no English prompt yet']}})
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {
        '*': {'limit': 2, 'reason': 'first take plus one retake'}})
    video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'old take before input recompile')
    history.save(ctx.episode_dir, {
        'trials': [], 'observations': {}, 'prior_generation_counts': {'clips': {'clip_01': 1}}})
    return ctx, plan, prompts


@pytest.mark.parametrize('structured_request', [True, False])
def test_repair_can_fill_missing_recompiled_request_with_current_plan_contract(
        missing_recompiled_request, structured_request):
    ctx, before, prompts = missing_recompiled_request
    grants = (ctx.episode_dir/'repair_budget_grants.json').read_bytes()
    script = (ctx.episode_dir/'chapter_script.json').read_bytes()
    decision = {'clip_id': 'clip_01', 'action': 'edit', 'reason': 'supply the missing English'}
    decision.update({'h3': {'shots': ['<Subject 2> holds the cup firmly. <DIALOGUE_1> The hand settles.']}}
                    if structured_request else {'prompt_h3': prompts['clip_01']})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [decision]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is True and result['changed'] == ['clip_01']
    after = read_json(ctx.episode_dir/'clip_plan.json')
    contracts.validate_existing_request(before['clips'][0], after['clips'][0]['prompt_h3'])
    for key in ('references', 'dialogue_bindings', 'shot_indexes', 'shot_timing', 'generation_seed'):
        assert after['clips'][0][key] == before['clips'][0][key]
    assert after['clips'][1:] == before['clips'][1:]
    assert history.generation_counts(ctx.episode_dir) == {'clip_01': 1}
    assert (ctx.episode_dir/'repair_budget_grants.json').read_bytes() == grants
    assert (ctx.episode_dir/'chapter_script.json').read_bytes() == script


@pytest.mark.parametrize('old,new', [
    ('你来啦。', '你回去。'), ('<Subject 2> (S1) says', '<Subject 1> (S1) says'),
    ('<Picture 2>', '<Picture 1>'), ('Planned duration: 15 seconds.', 'Planned duration: 14 seconds.')])
def test_missing_request_repair_still_rejects_protected_contract_changes(
        missing_recompiled_request, old, new):
    ctx, before, prompts = missing_recompiled_request
    assert old in prompts['clip_01']
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [{
        'clip_id': 'clip_01', 'action': 'edit', 'reason': 'invalid candidate',
        'prompt_h3': prompts['clip_01'].replace(old, new)}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is False and result['changed'] == []
    assert result['blocked']['clip_01'] != "'prompt_h3'"
    assert read_json(ctx.episode_dir/'clip_plan.json') == before
    assert history.generation_counts(ctx.episode_dir) == {'clip_01': 1}


def test_missing_request_retake_requires_an_edit_without_publishing(missing_recompiled_request):
    ctx, before, _ = missing_recompiled_request
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [{
        'clip_id': 'clip_01', 'action': 'retake', 'reason': 'missing English'}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is False and result['changed'] == []
    assert 'edit' in result['blocked']['clip_01']
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


def test_missing_request_edit_keeps_exhausted_physical_budget(missing_recompiled_request, monkeypatch):
    ctx, before, prompts = missing_recompiled_request
    monkeypatch.setattr(history, 'generation_counts', lambda directory: {'clip_01': 2})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [{
        'clip_id': 'clip_01', 'action': 'edit', 'reason': 'no quota', 'prompt_h3': prompts['clip_01']}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['changed'] == [] and result['blocked'] == {'clip_01': 'effective clip retry budget used'}
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


@pytest.mark.parametrize('section', ['summary', 'overall_soundscape'])
def test_missing_full_request_cannot_hide_extra_dialogue_outside_shots(missing_recompiled_request, section):
    ctx, before, prompts = missing_recompiled_request
    prompt = prompts['clip_01'].replace(section + ':',
        section + ':\n<Subject 2> (S1) says <d>[Chinese] 你回去。</d>')
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [{
        'clip_id': 'clip_01', 'action': 'edit', 'reason': 'invalid extra speech', 'prompt_h3': prompt}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is False and result['changed'] == []
    assert 'speech' in result['blocked']['clip_01']
    assert read_json(ctx.episode_dir/'clip_plan.json') == before
    assert history.generation_counts(ctx.episode_dir) == {'clip_01': 1}


def test_repair_can_complete_partial_duration_headers_from_unchanged_shot_timing():
    from novel_manga.application.agents.episode.requests import compose_repair
    draft = episode()
    draft['chapter_script']['shots'].insert(1, shot(2))
    for n, row in enumerate(draft['chapter_script']['shots'], 1):
        row['index'] = n
    draft['chapter_script']['shots'][0]['duration_seconds'] = 6
    draft['chapter_script']['shots'][1]['duration_seconds'] = 9
    draft['clips'][0]['shot_indexes'] = [1, 2]
    for n, clip in enumerate(draft['clips'][1:], 3):
        clip['shot_indexes'] = [n]
    _, plan = adopt(structured(draft))
    clip = plan['clips'][0]
    clip['prompt_h3'] = clip['prompt_h3'].replace('Planned duration: 9 seconds. ', '')
    before = copy.deepcopy(clip)
    prompt = compose_repair(clip, {'h3': {'shots': [
        '<Subject 2> firmly lifts the cup. <DIALOGUE_1>',
        '<Subject 2> places the cup on the table. <DIALOGUE_1>']}})
    contracts.validate_patch(clip, prompt)
    assert clip == before
    assert 'Planned duration: 6 seconds.' in prompt and 'Planned duration: 9 seconds.' in prompt
    with pytest.raises(ValueError, match='duration'):
        contracts.validate_patch(clip, prompt.replace('Planned duration: 9 seconds.', 'Planned duration: 8 seconds.'))


def test_repair_uses_bound_dialogue_when_old_compiler_lost_ellipsis():
    from novel_manga.application.agents.episode.requests import compose_repair
    _, plan = adopt(structured(episode(turns=[{
        'speaker_name': '甲', 'text': '你回去……', 'delivery_mode': 'visible_dialogue'}])))
    clip = plan['clips'][0]
    clip['prompt_h3'] = clip['prompt_h3'].replace('你回去…….', '你回去.')
    before = copy.deepcopy(clip)
    prompt = compose_repair(clip, {'h3': {'shots': ['<Subject 2> lowers the cup. <DIALOGUE_1>']}})
    assert '你回去…….' in prompt and '你回去…….' not in clip['prompt_h3']
    contracts.validate_patch(clip, prompt)
    assert clip == before
    with pytest.raises(ValueError, match='dialogue'):
        contracts.validate_patch(clip, prompt.replace('你回去…….', '你回去.'))
    with pytest.raises(ValueError, match='dialogue'):
        contracts.validate_patch(clip, prompt.replace('你回去…….', '他回去…….'))
    with pytest.raises(ValueError, match='duration'):
        contracts.validate_patch(clip, prompt.replace('Planned duration: 15 seconds.', 'Planned duration: 14 seconds.'))
