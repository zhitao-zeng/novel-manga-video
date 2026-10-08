from novel_manga.application.agents.episode import input_contracts
"""Source input repair routing; generation is simulated, budgets/publication are real."""
import copy
from pathlib import Path

import pytest

from novel_manga.application.agents.episode import input_repair
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.repair import history
from novel_manga.application.repair.flow import rebuild_clips as real_rebuild_clips
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


@pytest.fixture
def setup(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    ctx.asset_snapshot_dir = ctx.run_dir.parent/'assets-snapshot'
    shared = ctx.asset_snapshot_dir/'series_assets'
    (shared/'characters').mkdir(parents=True)
    atomic_write_json(shared/'phases.json', {'policy': 'phase-cards-v1', 'characters': {}})
    (ctx.novel_dir/'series_assets').symlink_to(shared, target_is_directory=True)
    for root in [ctx.novel_dir, ctx.source_novel_dir]:
        (root/'cards').mkdir(exist_ok=True)
        (root/'voices').mkdir(exist_ok=True)
        for name in ['a.jpeg', 'b.jpeg', 'room.jpeg']:
            (root/'cards'/name).write_bytes(b'image')
        (root/'voices/a.wav').write_bytes(b'audio')
    atomic_write_json(ctx.novel_dir/'story_bible.json', {'characters': [{'name': '乙'}, {'name': '甲'}]})
    (ctx.run_dir/'source.txt').write_text('甲坐在宿舍，穿着黑色披风。甲说：你来啦。')
    monkeypatch.setattr(input_repair, 'source_check', lambda *a, **kw: {'consistent': True, 'problems': []})
    def translated(clip, **kwargs):
        from novel_manga.application.profiles import h3_stamp
        clip['prompt_h3_of'] = h3_stamp(clip, '')
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert', translated)
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda *a, **k: {'consistent': True, 'problems': []})
    monkeypatch.setattr('novel_manga.application.preparation.wearer_cards.check_references', lambda *a: None)

    def rebuild(directory, bible_path, script, before, targets, *, repack_report=None, preserve_timing=False, source_checked_turns=None):
        assert preserve_timing
        after = copy.deepcopy(before)
        ledger = read_json(ctx.novel_dir/'series_assets/phases.json', {})
        phases = ledger.get('characters', {})
        changed = []
        for clip in after['clips']:
            if clip['clip_id'] not in targets:
                continue
            clip['prompt'] = '修正后的地点与外貌'
            if phases:
                for ref in clip['references']:
                    if ref['name'] in phases:
                        asset = phases[ref['name']][0]['asset_id']
                        ref.update(asset_id=asset, path=f'series_assets/characters/{asset}/turnaround.jpeg')
            place = script['shots'][clip['shot_indexes'][0]-1]['location']
            location_phase = (ledger.get('locations', {}).get(place) or [None])[0]
            if location_phase:
                clip['location'] = place
                for ref in clip['references']:
                    if ref['role'] == 'location':
                        asset = location_phase['asset_id']
                        ref.update(name=place, asset_id=asset, path=f'series_assets/locations/{asset}/establishing.jpeg')
            changed.append(clip['clip_id'])
        return after, changed

    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips', rebuild)
    return ctx, plan, shared


def decision(**extra):
    return {'clip_id': 'clip_01', 'action': 'input', 'reason': '原文是宿舍，当前场景错误',
            'source_quote': '甲坐在宿舍', 'shots': [{'index': 1, 'location': '宿舍'}], **extra}


def test_check_and_apply_reject_resume_with_new_changes_before_spending_input(setup):
    ctx,_,_=setup
    saved=decision(shots=[{'index':1,'location':'宿舍'},{'index':999,'location':'旧方案'}])
    path=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path,{'clip_id':'clip_01','status':'incomplete','step':'compile','decision':saved})
    before=path.read_bytes()
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision(resume=True)]})
    checked=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert not checked['valid'] and 'resume' in checked['argument_errors']['clip_01']
    with pytest.raises(input_repair.RepairArgumentError,match='resume'):
        input_repair.repair(ctx,decision(resume=True))
    assert path.read_bytes()==before


def test_input_check_uses_live_shot_scope_without_model_or_receipt(setup,monkeypatch):
    ctx,_,_=setup
    monkeypatch.setattr(input_repair,'source_check',lambda *a,**k:pytest.fail('check called a model'))
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision(shots=[{'index':999,'location':'宿舍'}])]})
    result=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert not result['valid'] and 'allowed indexes' in result['argument_errors']['clip_01']
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision()]})
    assert EpisodeTools(ctx).check({'file':'output/repair.json'})['valid']
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))


def test_check_sees_spent_revision_without_changing_its_saved_candidate(setup):
    ctx,_,_=setup
    path=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path,{'clip_id':'clip_01','status':'incomplete','step':'compile','revision_count':1,
        'decision':decision(shots=[{'index':1,'light':'old'}])})
    before=path.read_bytes()
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision()]})
    result=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert not result['valid'] and 'revision allowance' in result['argument_errors']['clip_01']
    assert path.read_bytes()==before


def test_compile_error_names_changed_dependency_instead_of_claiming_a_recut(setup,monkeypatch):
    ctx,plan,_=setup
    def rebuild(*a,**kw):
        after=copy.deepcopy(plan);after['clips'][1]['prompt']='dependent posture'
        return after,['clip_02']
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips',rebuild)
    with pytest.raises(ValueError,match='clip_02.*prompt'):
        input_repair.repair(ctx,decision())
    record=read_json(next((ctx.episode_dir/'work/input_repairs').glob('*.json')))
    assert record['error_details']['kind']=='dependency_scope'
    assert record['error_details']['extra_clips']==['clip_02']


def test_verified_posture_dependency_is_repaired_in_same_transaction(setup,monkeypatch):
    ctx,plan,_=setup
    def rebuild(*a,**kw):
        after=copy.deepcopy(plan)
        after['clips'][1]['prompt']='dependent posture'
        kw['repack_report']['_posture_dependent_clips']=['clip_02']
        return after,['clip_02']
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips',rebuild)
    result=input_repair.repair(ctx,decision())
    assert result['changed']==['clip_02']
    record=read_json(next((ctx.episode_dir/'work/input_repairs').glob('*.json')))
    assert record['dependent_clips']==['clip_02']


@pytest.mark.parametrize('filmed', [False, True])
def test_automatic_dependency_keeps_video_budget_and_first_pass_boundary(setup,monkeypatch,filmed):
    ctx,plan,_=setup
    before=(ctx.episode_dir/'clip_plan.json').read_bytes()
    if filmed:
        monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY','1')
        p=ctx.episode_dir/'work/clips/clip_02/attempt_01/clip.mp4'
        p.parent.mkdir(parents=True);p.write_bytes(b'keep')
    else:
        atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'clip_02':{'limit':0,'reason':'spent'}})
    def rebuild(*a,**kw):
        after=copy.deepcopy(plan);after['clips'][1]['prompt']='dependent posture'
        kw['repack_report']['_posture_dependent_clips']=['clip_02']
        return after,['clip_02']
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips',rebuild)
    with pytest.raises(ValueError,match='clip_02'):
        input_repair.repair(ctx,decision())
    assert (ctx.episode_dir/'clip_plan.json').read_bytes()==before


def test_first_pass_preflight_rejects_a_filmed_dependent_input_before_any_work(setup,monkeypatch):
    ctx,_,_=setup
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY','1')
    video=ctx.episode_dir/'work/clips/clip_02/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True);video.write_bytes(b'existing')
    row=decision(clip_ids=['clip_01','clip_02'],shots=[{'index':1,'light':'晚间'},{'index':2,'light':'晚间'}])
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row]})
    checked=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert not checked['valid'] and 'clip_02' in checked['argument_errors']['clip_01']
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    with pytest.raises(input_repair.RepairArgumentError,match='clip_02'):
        input_repair.repair(ctx,row)


def test_missing_source_is_reported_by_check_without_a_tool_traceback(setup):
    ctx,_,_=setup
    (ctx.run_dir/'source.txt').unlink()
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision()]})
    result=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert not result['valid'] and 'source.txt' in result['argument_errors']['clip_01']


def test_old_source_rejection_resumes_saved_candidate_without_new_allowance(setup, monkeypatch):
    ctx, plan, _ = setup
    path = ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path, {'status': 'rejected', 'step': 'source', 'clip_id': 'clip_01',
        'decision': decision(), 'error': 'UnsupportedInput: 所有修改均有原文依据',
        'error_kind': 'source_rejected', 'revision_count': 1, 'resume_count': 1})
    before = input_repair.remaining_input_attempts(ctx, ['clip_01'])
    resumed = input_repair.repair_status(ctx)['clip_01']['resume_action']
    result = input_repair.repair(ctx, resumed)
    saved = read_json(path)
    assert result['changed'] == ['clip_01']
    assert saved['status'] == 'prepared' and saved['source_review_policy'] >= 2
    assert saved['previous_source_review']['error'].endswith('所有修改均有原文依据')
    assert saved['resume_count'] == saved['revision_count'] == 1
    assert input_repair.remaining_input_attempts(ctx, ['clip_01']) == before
    assert history.generation_counts(ctx.episode_dir) == {}


def test_confirmed_rejection_cannot_reopen_again(setup, monkeypatch):
    ctx, _, _ = setup
    path = ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path, {'status': 'rejected', 'step': 'source', 'clip_id': 'clip_01',
        'decision': decision(), 'revision_count': 1, 'resume_count': 1})
    calls = []
    def reject(*args, **kwargs):
        calls.append(1)
        error = input_repair.UnsupportedInput('confirmed source contradiction')
        error.review = {'consistent': False, 'problems': ['confirmed contradiction']}
        raise error
    monkeypatch.setattr(input_repair, 'source_check', reject)
    with pytest.raises(input_repair.UnsupportedInput):
        input_repair.repair(ctx, {'clip_id': 'clip_01', 'action': 'input', 'resume': True})
    saved = read_json(path)
    assert saved['source_review']['consistent'] is False
    assert 'resume_action' not in input_repair.repair_status(ctx)['clip_01']
    with pytest.raises(ValueError):
        input_repair.repair(ctx, {'clip_id': 'clip_01', 'action': 'input', 'resume': True})
    assert calls == [1]


def test_reused_input_is_explicit_in_apply_result(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [decision()]})
    first = EpisodeTools(ctx).apply({})
    assert first['changed'] == ['clip_01']
    second = EpisodeTools(ctx).apply({})
    assert second['input_reused'] == ['clip_01'] and second['changed'] == []


def test_duplicate_shared_scope_reuse_does_not_claim_a_new_repair(setup, monkeypatch):
    from novel_manga.application.agents.episode.flow import _made_progress, _progress
    ctx, _, _ = setup
    monkeypatch.setattr(input_repair, 'repair', lambda *a: {
        'changed': [], 'input_repaired': ['clip_01'], 'reused': True})
    first = decision(clip_ids=['clip_01', 'clip_02'])
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [first, {**first, 'clip_id': 'clip_02'}]})
    before = _progress(ctx, {})
    result = EpisodeTools(ctx).apply({})
    assert set(result['input_reused']) == {'clip_01', 'clip_02'}
    assert not _made_progress(ctx, before, {}, {'job': {
        'id': 'job-1', 'status': 'completed', 'action': 'apply', 'result': result}})


def test_host_continues_old_source_rejection_before_opening_agent(setup):
    from novel_manga.application.agents.episode.flow import _pending_input_continuations
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json', {
        'status': 'rejected', 'step': 'source', 'clip_id': 'clip_01', 'decision': decision()})
    assert _pending_input_continuations(ctx)
    saved = read_json(ctx.work_dir/'output/resume-inputs.json')
    assert len(saved['decisions']) == 1 and saved['decisions'][0]['resume'] is True


def test_source_recovery_reservation_survives_interrupted_judge(setup, monkeypatch):
    from novel_manga.application.agents.episode.flow import _pending_input_continuations
    ctx, _, _ = setup
    path = ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path, {'status': 'rejected', 'step': 'source', 'clip_id': 'clip_01',
        'decision': decision(), 'resume_count': 0, 'revision_count': 1})
    def interrupted(*args, **kwargs):
        reserved = read_json(path)
        assert reserved['source_review_policy'] >= 2 and reserved['previous_source_review']
        raise TimeoutError('temporary outage')
    monkeypatch.setattr(input_repair, 'source_check', interrupted)
    with pytest.raises(TimeoutError):
        input_repair.repair(ctx, {'clip_id': 'clip_01', 'action': 'input', 'resume': True})
    saved = read_json(path)
    assert saved['status'] == 'incomplete' and saved['resume_count'] == 0
    assert not input_repair.source_review_recoverable(saved)
    assert _pending_input_continuations(ctx)
    monkeypatch.setattr(input_repair, 'source_check', lambda *a, **kw: {'consistent': True, 'problems': []})
    input_repair.repair(ctx, {'clip_id': 'clip_01', 'action': 'input', 'resume': True})
    saved = read_json(path)
    assert saved['status'] == 'prepared' and saved['resume_count'] == saved['revision_count'] == 1


@pytest.mark.parametrize('resume', [True, False])
def test_old_source_candidate_cannot_replace_a_newer_prepared_input(setup, monkeypatch, resume):
    ctx, _, _ = setup
    folder = ctx.episode_dir/'work/input_repairs'
    atomic_write_json(folder/'clip_01-1.json', {'status': 'rejected', 'step': 'source',
        'clip_id': 'clip_01', 'decision': decision()})
    atomic_write_json(folder/'clip_01-2.json', {'status': 'prepared', 'step': 'complete',
        'clip_id': 'clip_01', 'decision': decision(shots=[{'index': 1, 'location': '房间'}])})
    before = (ctx.episode_dir/'clip_plan.json').read_bytes()
    monkeypatch.setattr(input_repair, 'source_check', lambda *a, **kw: pytest.fail('obsolete input reached judge'))
    row = {'clip_id': 'clip_01', 'action': 'input', 'resume': True} if resume else decision()
    with pytest.raises(ValueError):
        input_repair.repair(ctx, row)
    assert (ctx.episode_dir/'clip_plan.json').read_bytes() == before
    assert read_json(folder/'clip_01-1.json')['status'] == 'rejected'


@pytest.mark.parametrize('status,resumes,expected', [('checking', 1, True), ('incomplete', 0, True), ('incomplete', 1, False)])
def test_reserved_source_recovery_uses_only_its_existing_continuation(setup, status, resumes, expected):
    from novel_manga.application.agents.episode.flow import _pending_input_continuations
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json', {
        'status': status, 'step': 'source', 'clip_id': 'clip_01', 'decision': decision(),
        'source_review_policy': 2, 'previous_source_review': {'policy': 1}, 'resume_count': resumes})
    assert _pending_input_continuations(ctx) is expected


def test_existing_actor_conversion_retains_a_resumable_canonical_decision(setup,monkeypatch):
    ctx,_,_=setup
    def unavailable(*args,**kwargs):raise TimeoutError('temporary source checker interruption')
    monkeypatch.setattr(input_repair,'source_check',unavailable)
    row=decision(characters={'甲':{'appearance':'source appearance','wardrobe':'black cloak'}})
    with pytest.raises(TimeoutError):input_repair.repair(ctx,row)
    record=read_json(next((ctx.episode_dir/'work/input_repairs').glob('*.json')))
    parsed=input_contracts.normalize_arguments(record['decision'])
    assert 'characters' not in parsed and '甲' in parsed['phases']['characters']


def test_adopted_input_accepts_source_cast_and_missing_character_requests(setup):
    row = decision(shots=[{'index': 1, 'in_frame': ['甲'], 'extras': ['访客'], 'listeners': [],
                          'sfx': 'Wind outside.'}],
                   characters={'甲': {'appearance': '成年男子', 'wardrobe': '黑色披风'}})
    assert input_contracts.normalize_arguments(row) == row


@pytest.mark.parametrize('field,value', [('in_frame', '甲'), ('extras', [None]), ('listeners', [''])])
def test_cast_argument_errors_precede_models_and_receipts(setup, field, value):
    with pytest.raises(input_repair.RepairArgumentError, match=field):
        input_contracts.normalize_arguments(decision(shots=[{'index': 1, field: value}]))


def test_explicit_speaker_and_action_correction_preserves_the_words(setup, monkeypatch):
    from novel_manga.application.repair import flow as repair_flow
    ctx, before, _ = setup
    base_rebuild = repair_flow.rebuild_clips
    def rebuild(*args, **kwargs):
        script = args[2]
        assert script['shots'][0]['turns'][0]['speaker_name'] == '乙'
        after, changed = base_rebuild(*args, **kwargs)
        for line in after['clips'][0]['lines']:
            line['speaker_name'] = '乙'
        return after, changed
    monkeypatch.setattr(repair_flow, 'rebuild_clips', rebuild)
    result = input_repair.repair(ctx, decision(clip_ids=['clip_01'], shots=[{
        'index': 1, 'in_frame': ['乙'], 'speaker_map': {'甲': '乙'},
        'actions': [{'actor': '乙', 'action': '坐在宿舍', 'target': ''}],
        'event': '乙坐在宿舍说话', 'sfx': 'Wind outside.'}]))
    assert result['changed'] == ['clip_01']
    after = read_json(ctx.episode_dir/'clip_plan.json')
    assert [r['text'] for r in after['clips'][0]['lines']] == [r['text'] for r in before['clips'][0]['lines']]
    assert after['clips'][1:] == before['clips'][1:]


def test_unrelated_blocked_shots_do_not_veto_local_source_correction(setup,monkeypatch):
    ctx,plan,_=setup
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{'blocked_clips':{
        c['clip_id']:'different known issues' for c in plan['clips']}})
    seen=[]
    def check(source,quote,patches,phases,locations=None,scoped_shots=None,**kwargs):
        seen.extend(s['index'] for s in scoped_shots)
        return {'consistent':True,'problems':[]}
    monkeypatch.setattr(input_repair,'source_check',check)
    input_repair.repair(ctx,decision())
    assert seen==[1]


def test_dialogue_correction_argument_retains_inner_voice_and_new_words(setup):
    row=decision(shots=[{'index':1,'turns':[{'speaker_name':'甲',
        'delivery_mode':'offscreen_dialogue','text':'我得先查清缘由。','inner_monologue':True}]}])
    assert input_contracts.normalize_arguments(row)==row


@pytest.mark.parametrize('correct_owner',[False,True])
def test_source_checked_dialogue_revision_runs_real_compilation_and_preserves_other_clips(setup,monkeypatch,correct_owner):
    from novel_manga.application.agents.episode.requests import compose_request
    from novel_manga.application.profiles import h3_stamp
    from novel_manga.story.voice_delivery import visual_request
    ctx,before,shared=setup
    atomic_write_json(ctx.novel_dir/'story_bible.json',{'novel_title':'Book','genre':'fantasy',
        'visual_style':'3d','palette':'natural','style_fingerprint':'fixture','locations':['房间'],
        'characters':[{'name':n,'appearance':'adult','wardrobe':'robe'} for n in ('乙','甲')]})
    for rel in ('characters/character_001/turnaround.jpeg','characters/character_002/turnaround.jpeg',
                'locations/location_001/establishing.jpeg'):
        path=shared/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'image')
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips',real_rebuild_clips)
    def translate(clip,**kwargs):
        view=visual_request(clip)
        bodies=[]
        for stage in range(1,len(clip['shot_indexes'])+1):
            lines=[x for x in view['dialogue_bindings'] if x['stage']==stage]
            bodies.append('The people face each other. '+ ' '.join(f'<DIALOGUE_{i}>' for i in range(1,len(lines)+1)))
        clip['prompt_h3']=compose_request(clip,{'shots':bodies,'soundscape':'Quiet room tone.'})
        clip['prompt_h3_of']=h3_stamp(clip,'')
        return True
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',translate)
    new_turn={'speaker_name':'甲','delivery_mode':'offscreen_dialogue','text':'我得先查清缘由。','inner_monologue':True}
    retained=[]
    if correct_owner:
        from novel_manga.application.identity.store import chapter_inputs
        from novel_manga.story.source_identity import POLICY
        source='甲坐在宿舍，甲说：你来啦。乙说：你来啦。'
        (ctx.run_dir/'source.txt').write_text(source)
        atomic_write_json(ctx.episode_dir/'segments.json',[{'segment_id':'seg_1','text':source}])
        atomic_write_json(ctx.episode_dir/'identity_context.json',{'policy':POLICY,
            'inputs':chapter_inputs(ctx.episode_dir),'entities':{'e1':'甲','e2':'乙'},
            'mentions':[{'form':name,'kind':'proper','entity_id':eid,'presence':'on_stage'}
                for eid,name in [('e1','甲'),('e2','乙')]],'relations':[]})
        fact={'stage':1,'turn':1,'speaker':'甲','source_quote':'甲说：你来啦。',
              'source_speaker_phrase':'甲','relation':'verbatim','adapted_text':'你来啦。'}
        retained=[{**fact,'stage':2}]
        atomic_write_json(ctx.episode_dir/'source_speaker_contract.json',[fact,*retained])
        new_turn={'speaker_name':'乙','delivery_mode':'offscreen_dialogue','text':'你来啦。'}
    result=input_repair.repair(ctx,decision(shots=[{'index':1,'turns':[new_turn]}]))
    after=read_json(ctx.episode_dir/'clip_plan.json')
    assert result['changed']==['clip_01']
    assert after['clips'][0]['lines'][0]['text'].rstrip('。.')==new_turn['text'].rstrip('。.')
    assert after['clips'][0]['lines'][0]['speaker_name']==new_turn['speaker_name']
    assert bool(after['clips'][0]['lines'][0].get('inner_monologue'))==bool(new_turn.get('inner_monologue'))
    if correct_owner:
        assert read_json(ctx.episode_dir/'source_speaker_contract.json')==retained
    assert after['clips'][0]['shot_timing']==before['clips'][0]['shot_timing']
    assert after['clips'][1:]==before['clips'][1:]
    assert history.generation_counts(ctx.episode_dir)=={}


def test_cardless_offscreen_owner_correction_reaches_real_packer(setup, monkeypatch):
    ctx, plan, _=setup
    bible={'novel_title':'Book','genre':'fantasy','visual_style':'3d','palette':'natural',
        'style_fingerprint':'fixture','locations':['房间'], 'characters':[
            {'name':n,'appearance':'adult','wardrobe':'robe'} for n in ('乙','甲')]}
    atomic_write_json(ctx.novel_dir/'story_bible.json',bible)
    script=read_json(ctx.episode_dir/'chapter_script.json')
    turn=script['shots'][0]['turns'][0]
    turn.update(speaker_name='无名商人',delivery_mode='offscreen_dialogue')
    script['shots'][0]['characters']=['甲']
    for field in ('lines','dialogue_bindings'):
        for line in plan['clips'][0].get(field,[]):
            line.update(speaker_name='无名商人',delivery_mode='offscreen_dialogue')
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    class PackingVerified(Exception):
        pass
    def rebuild(*args,**kwargs):
        candidate,changed=real_rebuild_clips(*args,**kwargs)
        clip=candidate['clips'][0]
        assert clip['lines'][0]['speaker_name']=='酒吧老板'
        assert clip['lines'][0]['delivery_mode']=='offscreen_dialogue'
        assert not any(r.get('name')=='酒吧老板' for r in clip['references'])
        assert clip['shot_timing']==plan['clips'][0]['shot_timing']
        assert candidate['clips'][1:]==plan['clips'][1:]
        raise PackingVerified  # Stop before translation, whose input is now verified.
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips',rebuild)
    with pytest.raises(PackingVerified):
        input_repair.repair(ctx, decision(clip_ids=['clip_01'],shots=[
            {'index':1,'speaker_map':{'无名商人':'酒吧老板'}}]))


def test_false_source_cast_correction_cannot_register_assets(setup, monkeypatch):
    from novel_manga.application.agents.episode import draft_assets
    ctx, before, _ = setup
    original = (ctx.novel_dir/'story_bible.json').read_bytes()
    def reject(*args, **kwargs):
        raise input_repair.UnsupportedInput('cast correction contradicts source')
    monkeypatch.setattr(input_repair, 'source_check', reject)
    monkeypatch.setattr(draft_assets, '_asset', lambda *a: pytest.fail('unverified cast drew assets'))
    with pytest.raises(input_repair.UnsupportedInput, match='contradicts source'):
        input_repair.repair(ctx, decision(clip_ids=['clip_01'],
            shots=[{'index': 1, 'in_frame': ['甲','丙']}],
            characters={'丙': {'appearance': '成年男子', 'wardrobe': '灰袍'}}))
    assert (ctx.novel_dir/'story_bible.json').read_bytes() == original
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


def test_cast_patch_recomputes_membership_and_passes_scoped_source_evidence(setup, monkeypatch):
    ctx, before, _ = setup
    seen = {}
    def source_check(source, quote, shots, phases, locations=None, scoped_shots=None, **kwargs):
        seen['shots'] = scoped_shots
        return {'consistent': True, 'problems': []}
    monkeypatch.setattr(input_repair, 'source_check', source_check)
    result = input_repair.repair(ctx, decision(clip_ids=['clip_01'],
        shots=[{'index': 1, 'in_frame': ['甲'], 'listeners': [], 'extras': ['三名守卫']}]))
    assert result['changed'] == ['clip_01']
    script = read_json(ctx.episode_dir/'chapter_script.json')
    assert script['shots'][0]['characters'] == ['甲']
    assert script['shots'][0]['extras'] == ['三名守卫']
    assert seen['shots'][0]['in_frame'] == ['甲']
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][1:] == before['clips'][1:]
    assert history.generation_counts(ctx.episode_dir) == {}


@pytest.mark.parametrize('translation_fails', [False, True])
def test_adopted_missing_actor_registration_is_local_and_rolls_back_failed_candidate(setup, monkeypatch, translation_fails):
    from novel_manga.application.agents.episode import draft_assets
    ctx, before, shared = setup
    (ctx.work_dir/'input').mkdir(exist_ok=True)
    (ctx.run_dir/'source.txt').write_text('甲坐在宿舍，丙站在门口。')
    original_bible = (ctx.novel_dir/'story_bible.json').read_bytes()
    original_entities = (ctx.novel_dir/'entity/entities.json').read_bytes() if (ctx.novel_dir/'entity/entities.json').exists() else None
    calls = []
    def build(context, folder, role, name, design):
        calls.append(name)
        path = folder/'image.jpeg'; path.write_bytes(b'new card'); return path
    monkeypatch.setattr(draft_assets, 'build', build)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: {'consistent': True, 'problems': []})
    if translation_fails:
        def reject(clip, **kwargs):
            kwargs['problems'].append('translation rejected')
        monkeypatch.setattr('novel_manga.application.rendering.h3.convert', reject)
    row = decision(clip_ids=['clip_01'],
        shots=[{'index': 1, 'in_frame': ['甲', '丙'], 'listeners': []}],
        characters={'丙': {'appearance': '成年男子', 'wardrobe': '灰袍'}})
    if translation_fails:
        with pytest.raises(ValueError, match='translation rejected'):
            input_repair.repair(ctx, row)
        assert (ctx.novel_dir/'story_bible.json').read_bytes() == original_bible
        assert read_json(ctx.episode_dir/'clip_plan.json') == before
        entities = ctx.novel_dir/'entity/entities.json'
        assert (entities.read_bytes() if entities.exists() else None) == original_entities
    else:
        result = input_repair.repair(ctx, row)
        assert result['changed'] == ['clip_01']
        assert read_json(ctx.novel_dir/'story_bible.json')['characters'][-1]['name'] == '丙'
        assert (ctx.novel_dir/'series_assets/characters/character_003/turnaround.jpeg').is_file()
    assert calls == ['丙']
    assert not (shared/'characters/character_003').exists()
    assert history.generation_counts(ctx.episode_dir) == {}


def test_existing_actor_missing_card_can_resume_after_translation_failure(setup, monkeypatch):
    from novel_manga.application.agents.episode import draft_assets
    from novel_manga.application.rendering import h3
    ctx, _, _ = setup
    (ctx.work_dir/'input').mkdir(exist_ok=True)
    bible = read_json(ctx.novel_dir/'story_bible.json')
    bible['characters'].append({'name': '丙', 'appearance':'成年男子', 'wardrobe':'灰袍'})
    atomic_write_json(ctx.novel_dir/'story_bible.json', bible)
    (ctx.run_dir/'source.txt').write_text('甲坐在宿舍，丙站在门口。')
    calls = []
    def build(context, directory, role, name, design):
        calls.append(name); p = directory/'image.jpeg'; p.write_bytes(b'accepted card'); return p
    monkeypatch.setattr(draft_assets, 'build', build)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: {'consistent': True, 'problems': []})
    good = h3.convert
    monkeypatch.setattr(h3, 'convert', lambda *a, **kw: kw['problems'].append('temporary translation failure'))
    row = decision(clip_ids=['clip_01'], shots=[{'index':1, 'in_frame':['甲','丙']}],
                   characters={'丙':{'appearance':'成年男子', 'wardrobe':'灰袍'}})
    with pytest.raises(ValueError, match='temporary translation failure'):
        input_repair.repair(ctx, row)
    assert (ctx.novel_dir/'series_assets/characters/character_003/turnaround.jpeg').is_file()
    monkeypatch.setattr(h3, 'convert', good)
    result = input_repair.repair(ctx, {'clip_id':'clip_01', 'action':'input', 'resume':True})
    assert result['input_repaired'] == ['clip_01'] and calls == ['丙']
    assert input_repair.repair_status(ctx)['clip_01']['status'] == 'prepared'


def test_published_speaker_map_resumes_refresh_without_reapplying_old_owner(setup, monkeypatch):
    from novel_manga.application.repair import flow as repair_flow
    ctx, _, _ = setup
    rebuild = repair_flow.rebuild_clips
    def remapped(*args, **kwargs):
        after, changed = rebuild(*args, **kwargs)
        for line in after['clips'][0]['lines']:line['speaker_name'] = '乙'
        return after, changed
    monkeypatch.setattr(repair_flow, 'rebuild_clips', remapped)
    refresh = input_repair.refresh_inputs
    monkeypatch.setattr(input_repair, 'refresh_inputs', lambda *a: (_ for _ in ()).throw(ConnectionError('refresh interrupted')))
    row = decision(clip_ids=['clip_01'], shots=[{'index':1, 'in_frame':['乙'], 'speaker_map':{'甲':'乙'}}])
    with pytest.raises(ConnectionError, match='refresh interrupted'):
        input_repair.repair(ctx, row)
    assert read_json(ctx.episode_dir/'chapter_script.json')['shots'][0]['turns'][0]['speaker_name'] == '乙'
    monkeypatch.setattr(input_repair, 'refresh_inputs', refresh)
    monkeypatch.setattr(repair_flow, 'rebuild_clips', lambda *a, **kw: pytest.fail('published repair recompiled'))
    result = input_repair.repair(ctx, {'clip_id':'clip_01', 'action':'input', 'resume':True})
    assert result['changed'] == ['clip_01']
    assert input_repair.repair_status(ctx)['clip_01']['status'] == 'prepared'


def test_host_compile_exception_is_reported_without_spending_input_candidate(setup, monkeypatch):
    ctx, before, _ = setup
    def broken(*args, **kwargs):
        raise KeyError('unregistered binding')
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips', broken)
    with pytest.raises(KeyError):
        input_repair.repair(ctx, decision())
    status = input_repair.repair_status(ctx)['clip_01']
    assert status['status'] == 'tool_error'
    assert status['error_kind'] == 'tool_error'
    assert status['remaining_attempts'] == 2
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


def test_status_reports_conditional_take_without_activating_generation(setup):
    ctx, _, _ = setup
    path = ctx.episode_dir/'repair_budget_grants.json'
    atomic_write_json(path, {'*': {'limit': 2, 'reason': 'old allowance'},
        'clip_01': {'conditional_extra': {'limit': 3, 'reason': 'reviewed extra take',
                    'authorization': 'user-approved', 'prepared': False}}})
    history.save(ctx.episode_dir, {'prior_generation_counts': {'clips': {'clip_01': 2}}, 'trials': []})
    before = path.read_bytes()
    status = EpisodeTools(ctx).status({})
    assert status['remaining_takes']['clip_01'] == 1
    assert status['conditional_extra_takes']['clip_01']['prepared'] is False
    assert history.generation_limit(ctx.episode_dir, 'clip_01') == 2
    assert path.read_bytes() == before


def test_current_status_exposes_actual_adopted_input_operations(setup):
    ctx, _, _ = setup
    status = EpisodeTools(ctx).status({})
    capabilities = status['repair_capabilities']
    assert capabilities['adopted_input']['characters'] == 'missing source actor cards'
    assert {'in_frame', 'speaker_map', 'sfx'} <= set(capabilities['adopted_input']['shot_fields'])
    assert capabilities['source_review_required'] is True


def test_pending_extra_explicitly_permits_apply_before_it_permits_generation(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'feedback': {'clip_01': 'missing speech'}})
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*': {'limit':2, 'input_limit':1, 'reason':'old default'},
        'clip_01': {'conditional_extra': {'limit':3, 'input_limit':2, 'authorization':'approved', 'reason':'reviewed retry', 'prepared':False}}})
    history.save(ctx.episode_dir, {'trials':[], 'prior_generation_counts':{'clips':{'clip_01':2,'clip_02':2}}})
    status = EpisodeTools(ctx).status({})
    assert {'edit','retake','input'} <= set(status['repair_actions']['clip_01'])
    assert status['repair_actions'].get('clip_02', []) == []
    assert 'limit' not in status['retry_policy']
    assert history.generation_limit(ctx.episode_dir, 'clip_01') == 2


def test_ineligible_input_keeps_valid_peer_repair_and_reports_eligible_targets(setup):
    ctx, _, _ = setup
    video = ctx.episode_dir/'existing-good.mp4'; video.write_bytes(b'current existing take')
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {'clips':[
        {'clip_id':'clip_01','selected':{'video':str(video)}}]})
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'feedback': {'clip_02': 'wrong setting'}})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [decision(),
        decision(clip_id='clip_02', shots=[{'index': 2, 'location': '宿舍'}])]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['changed'] == ['clip_02'] and result['input_repaired'] == ['clip_02']
    assert 'eligible' in result['argument_errors']['clip_01']
    assert 'clip_02' in result['argument_errors']['clip_01']
    assert not list((ctx.episode_dir/'work/input_repairs').glob('clip_01-*.json'))


def test_adopted_unfilmed_input_can_be_fixed_before_a_failed_render(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions':[decision()]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] and result['input_repaired'] == ['clip_01']
    assert history.generation_counts(ctx.episode_dir) == {}


def test_status_exposes_actual_input_and_recheck_allowances(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {
        '*': {'limit': 2, 'input_limit': 1, 'recheck_limit': 1, 'reason': 'bounded production'}})
    receipts=ctx.episode_dir/'work/input_repairs'
    atomic_write_json(receipts/'clip_01-1.json', {'clip_id':'clip_01','status':'tool_error','error':'old tool failure'})
    atomic_write_json(receipts/'clip_02-1.json', {'clip_id':'clip_02','status':'prepared','decision':decision(clip_id='clip_02')})
    atomic_write_json(ctx.run_dir/'tool-repair-decisions.json', [{'keep_checks':['clip_01','clip_02']}, {'keep_checks':['clip_02']}])
    state=EpisodeTools(ctx).dispatch('status')
    assert state['remaining_input_attempts']['clip_01']==1
    assert state['remaining_input_attempts']['clip_02']==0
    assert state['remaining_input_attempts']['clip_03']==1
    assert state['remaining_rechecks']['clip_01']==state['remaining_rechecks']['clip_02']==0
    assert state['remaining_rechecks']['clip_03']==1
    assert 'not counted' in state['input_repairs']['clip_01']['next_action']
    assert read_json(receipts/'clip_01-1.json')['status']=='tool_error'


def test_one_clip_input_grant_does_not_expand_other_clip_allowances(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {
        '*': {'input_limit':1}, 'clip_01':{'input_limit':2}})
    folder=ctx.episode_dir/'work/input_repairs'
    for cid in ['clip_01','clip_02']:
        atomic_write_json(folder/f'{cid}-1.json', {'clip_id':cid,'status':'prepared','published':True,
                                                'decision':decision(clip_id=cid)})
    assert input_repair.remaining_input_attempts(ctx,['clip_01','clip_02'])=={'clip_01':1,'clip_02':0}
    revised=decision(shots=[{'index':1,'light':'日光'}])
    receipt,_=input_repair.input_receipt(ctx,'clip_01',revised)
    assert receipt.name=='clip_01-2.json'
    with pytest.raises(ValueError,match='input repair allowance used'):
        input_repair.input_receipt(ctx,'clip_02',decision(clip_id='clip_02',shots=[{'index':1,'light':'夜晚'}]))


def test_new_input_allowance_after_spent_revision_creates_a_separate_receipt(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*':{'input_limit':1},'clip_01':{'input_limit':2}})
    path=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path,{'clip_id':'clip_01','status':'rejected','revision_count':1,'decision':decision()})
    original=path.read_bytes()
    changed=decision(shots=[{'index':1,'light':'日光'}])
    receipt,record=input_repair.input_receipt(ctx,'clip_01',changed)
    assert receipt.name=='clip_01-2.json' and record['decision']==changed
    assert path.read_bytes()==original
    assert input_repair.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}


def test_conditional_extra_preparation_can_replace_a_spent_old_revision_once(setup):
    ctx,_,_=setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'*':{'input_limit':1},
        'clip_01':{'conditional_extra':{'limit':3,'input_limit':2,'reason':'approved recovery',
                                       'authorization':'task','prepared':False}}})
    old=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(old,{'clip_id':'clip_01','status':'rejected','revision_count':1,'decision':decision()})
    before=old.read_bytes()
    path,_=input_repair.input_receipt(ctx,'clip_01',decision(shots=[{'index':1,'light':'window light'}]))
    assert path.name=='clip_01-2.json' and old.read_bytes()==before
    assert input_repair.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}


def test_check_reports_repair_argument_errors_without_spending_input_work(setup,monkeypatch):
    ctx,_,_=setup
    monkeypatch.setattr(input_repair,'source_check',lambda *a:pytest.fail('fast check cannot call source model'))
    monkeypatch.setattr(input_repair,'build_phases',lambda *a:pytest.fail('fast check cannot generate images'))
    path=ctx.work_dir/'output/repair.json'
    atomic_write_json(path,{'decisions':[decision(shots=[{'index':1,'remove_wears':True}])]})
    checked=EpisodeTools(ctx).dispatch('check',{'file':'output/repair.json'})
    assert checked['kind']=='repair_arguments' and not checked['valid']
    assert 'remove_wears' in checked['argument_errors']['clip_01']
    atomic_write_json(path,{'decisions':[decision(shots=[{'index':1,'wears':{}}])]})
    checked=EpisodeTools(ctx).dispatch('check',{'file':'output/repair.json'})
    assert checked['valid'] and checked['requires_input_preflight'] is True
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))


def test_incomplete_record_without_decision_offers_revision_not_invalid_resume(setup):
    ctx, _, _ = setup
    path=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path, {'clip_id':'clip_01','status':'checking','source_quote':'甲坐在宿舍'})
    state=input_repair.repair_status(ctx)['clip_01']
    assert 'resume_action' not in state
    assert state['revision_available'] is True
    assert 'source-backed' in state['next_action']
    receipt, record=input_repair.input_receipt(ctx,'clip_01',decision())
    assert receipt==path and record['revision_count']==1


def test_later_tool_error_does_not_hide_existing_input_revision(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*':{'input_limit':1}})
    folder=ctx.episode_dir/'work/input_repairs'
    atomic_write_json(folder/'clip_01-1.json', {'clip_id':'clip_01','status':'checking'})
    atomic_write_json(folder/'clip_01-2.json', {'clip_id':'clip_01','status':'tool_error'})
    state=input_repair.repair_status(ctx)['clip_01']
    assert state['remaining_attempts']==0 and state['revision_available'] is True
    assert 'resume_action' not in state
    receipt, record=input_repair.input_receipt(ctx,'clip_01',decision())
    assert receipt.name=='clip_01-1.json' and record['revision_count']==1


@pytest.mark.parametrize('limit',[1,2])
def test_published_input_cannot_be_relabelled_as_a_different_correction(setup,limit):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'*':{'input_limit':limit}})
    path=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path,{'clip_id':'clip_01','status':'incomplete','step':'refresh',
                          'published':True,'decision':decision(),'resume_count':0})
    before=path.read_bytes()
    changed=decision(shots=[{'index':1,'light':'午夜'}])
    if limit==1:
        with pytest.raises(ValueError,match='input repair allowance used'):
            input_repair.input_receipt(ctx,'clip_01',changed)
    else:
        receipt,record=input_repair.input_receipt(ctx,'clip_01',changed)
        assert receipt!=path and not record.get('published')
        assert record['decision']==changed
    assert path.read_bytes()==before


def test_source_location_repair_preserves_other_clips_and_generation_counts(setup):
    ctx, before, _ = setup
    answer = input_repair.repair(ctx, decision())
    after = read_json(ctx.episode_dir/'clip_plan.json')
    assert answer['changed'] == ['clip_01']
    assert after['clips'][1:] == before['clips'][1:]
    assert after['clips'][0]['generation_seed'] == 1234
    assert history.generation_counts(ctx.episode_dir) == {}
    assert history.load(ctx.episode_dir)['trials'][-1]['method'] == 'episode_agent_input'
    script = read_json(ctx.episode_dir/'chapter_script.json')
    assert script['shots'][0]['location'] == '宿舍'
    assert script['shots'][0]['camera'] == '手部近景，平视固定。'
    assert script['shots'][0]['turns'][0]['text'] == '你来啦。'


def test_source_patch_scope_includes_shared_shot_dependencies_automatically(setup,monkeypatch):
    ctx,plan,_=setup
    plan['clips'][1]['shot_indexes']=[1]
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    result=input_repair.repair(ctx,decision(clip_ids=['clip_01']))
    assert set(result['changed'])=={'clip_01','clip_02'}
    record=read_json(next((ctx.episode_dir/'work/input_repairs').glob('*.json')))
    assert record['decision']['clip_ids']==['clip_01','clip_02']
    assert history.generation_counts(ctx.episode_dir)=={}


def test_source_compression_rebuilds_only_checked_words_without_changing_owner(setup,monkeypatch):
    from novel_manga.application.repair import flow as repair_flow
    ctx,plan,_=setup
    ctx.input_mode='source'
    clip=plan['clips'][0]
    clip['lines'][0]['text']='长'*150
    clip['spoken_text']='长'*150
    script=read_json(ctx.episode_dir/'chapter_script.json')
    script['shots'][0]['turns'][0]['text']='长'*150
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    rebuild=repair_flow.rebuild_clips
    def rewritten(directory,bible_path,script,before,targets,**kwargs):
        after,changed=rebuild(directory,bible_path,script,before,targets,**kwargs)
        after['clips'][0]['lines'][0]['text']=script['shots'][0]['turns'][0]['text']
        after['clips'][0]['spoken_text']=script['shots'][0]['turns'][0]['text']
        return after,changed
    monkeypatch.setattr(repair_flow,'rebuild_clips',rewritten)
    result=input_repair.repair(ctx,decision(shots=[{'index':1,'turn_texts':['你来啦。']}],clip_ids=['clip_01']))
    assert result['changed']==['clip_01']
    current=read_json(ctx.episode_dir/'clip_plan.json')
    assert current['clips'][0]['lines'][0]['text']=='你来啦。'
    assert current['clips'][0]['lines'][0]['speaker_name']==clip['lines'][0]['speaker_name']
    assert current['clips'][1:]==plan['clips'][1:]
    assert history.generation_counts(ctx.episode_dir)=={}


@pytest.mark.parametrize('change', [{'source_quote': '捏造的原文'}, {'shots': [{'index': 1, 'camera': '全景'}]},
                                    {'shots': [{'index': 2, 'location': '宿舍'}]}])
def test_bad_source_or_protected_edits_do_not_call_a_model_or_write_plan(setup, monkeypatch, change):
    ctx, before, _ = setup
    monkeypatch.setattr(input_repair, 'source_check', lambda *a: pytest.fail('invalid input must be caught first'))
    with pytest.raises(ValueError):
        input_repair.repair(ctx, decision(**change))
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


@pytest.mark.parametrize('change,field', [
    ({'shots':[{'index':1,'wears':'黑色披风'}]}, 'shots[0].wears'),
    ({'shots':{'index':1,'wears':{}}}, 'shots'),
    ({'shots':['invalid patch']}, 'shots[0]'),
    ({'shots':[{'index':1,'props':'黑色披风'}]}, 'shots[0].props'),
    ({'phases':{'characters':{'甲':['invalid phase']}}}, 'phases.characters.甲'),
    ({'phase':{'characters':{}}}, 'phase'),
])
def test_malformed_input_is_actionable_before_models_assets_or_receipts(setup, monkeypatch, change, field):
    ctx,before,shared=setup
    original=(shared/'phases.json').read_bytes()
    monkeypatch.setattr(input_repair,'source_check',lambda *a:pytest.fail('malformed arguments reached source model'))
    monkeypatch.setattr(input_repair,'build_phases',lambda *a:pytest.fail('malformed arguments drew assets'))
    with pytest.raises(ValueError) as caught:
        input_repair.repair(ctx,decision(**change))
    assert field in str(caught.value)
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    assert (shared/'phases.json').read_bytes()==original
    assert read_json(ctx.episode_dir/'clip_plan.json')==before
    assert history.generation_counts(ctx.episode_dir)=={}


def test_single_phase_object_and_one_item_list_are_the_same_input_effect(setup, monkeypatch):
    ctx,_,_=setup
    draws=[]
    def draw(ctx,names):
        draws.extend(names)
        phases=read_json(ctx.novel_dir/'series_assets/phases.json')['characters']
        for name in names:
            path=ctx.novel_dir/'series_assets/characters'/phases[name][0]['asset_id']/'turnaround.jpeg'
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'checked phase image')
    monkeypatch.setattr(input_repair,'build_phases',draw)
    phase={'wardrobe':'黑色披风'}
    shorthand=decision(phases={'characters':{'甲':phase}})
    explicit=decision(phases={'characters':{'甲':[phase]}})
    assert input_repair.effects(shorthand)==input_repair.effects(explicit)
    first=input_repair.repair(ctx,shorthand)
    assert first['input_repaired']==['clip_01'] and draws==['甲']
    again=input_repair.repair(ctx,explicit)
    assert again['reused'] and draws==['甲']
    receipts=list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    assert len(receipts)==1 and read_json(receipts[0])['resume_count']==0
    assert history.generation_counts(ctx.episode_dir)=={}


@pytest.mark.parametrize('revisions',[0,1])
def test_saved_invalid_arguments_are_not_offered_as_resumable_service_work(setup,revisions):
    ctx,_,_=setup
    record={'clip_id':'clip_01','status':'incomplete','step':'compile','resume_count':0,
            'revision_count':revisions,'error':'dictionary update sequence element has wrong length',
            'decision':decision(shots=[{'index':1,'wears':'黑色披风'}])}
    path=ctx.episode_dir/'work/input_repairs/clip_01-1.json';atomic_write_json(path,record)
    before=path.read_bytes()
    state=input_repair.repair_status(ctx)['clip_01']
    assert 'resume_action' not in state
    assert 'wears' in state['argument_error']
    assert bool(state.get('revision_available'))==(revisions<1)
    assert path.read_bytes()==before


def test_public_apply_reports_input_argument_errors_without_spending_input_budget(setup,monkeypatch):
    ctx,_,_=setup
    atomic_write_json(ctx.episode_dir/'episode_review.json',{'feedback':{'clip_01':'input mismatch'}})
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision(shots=['invalid patch'])]})
    monkeypatch.setattr(input_repair,'source_check',lambda *a:pytest.fail('must validate before model'))
    result=EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is False and 'shots[0]' in result['argument_errors']['clip_01']
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))


@pytest.mark.parametrize('scope', [1, 'clip_01', {}, [], ['clip_01', 1], [None], [' '],
                                  ['clip_02'], ['clip_01', 'clip_99']])
def test_invalid_scope_is_an_argument_failure_without_resetting_progress(setup, monkeypatch, scope):
    from novel_manga.application.agents.episode.flow import _made_progress, _progress
    ctx, _, _ = setup
    monkeypatch.setattr(input_repair, 'source_check', lambda *a: pytest.fail('invalid scope reached model'))
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'feedback': {'clip_01': 'input mismatch'}})
    output = ctx.work_dir/'output/repair.json'
    atomic_write_json(output, {'decisions': [decision(shots=['invalid patch'])]})
    before_status = {'job': {'id': 'before', 'status': 'completed', 'action': 'apply',
                              'result': EpisodeTools(ctx).dispatch('apply')}}
    before = _progress(ctx, before_status)
    atomic_write_json(output, {'decisions': [decision(clip_ids=scope)]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is False and 'clip_ids' in result['argument_errors']['clip_01']
    status = {'job': {'id': 'after', 'status': 'completed', 'action': 'apply', 'result': result}}
    assert not _made_progress(ctx, before, before_status, status)
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    assert history.generation_counts(ctx.episode_dir) == {}


def test_explicit_null_wears_remains_a_supported_clear(setup):
    ctx,_,_=setup
    row=decision(shots=[{'index':1,'wears':None}])
    result=input_repair.repair(ctx,row)
    assert result['input_repaired']==['clip_01']
    assert read_json(ctx.episode_dir/'chapter_script.json')['shots'][0]['wears'] is None


def test_bad_input_row_does_not_cancel_another_valid_input_repair(setup):
    ctx,_,_=setup
    atomic_write_json(ctx.episode_dir/'episode_review.json',{'feedback':{
        'clip_01':'input mismatch','clip_02':'wrong setting'}})
    rows=[decision(characters={'甲':{'appearance':'unsupported adopted character request'}}),
          decision(clip_id='clip_02',shots=[{'index':2,'location':'宿舍'}])]
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':rows})
    result=EpisodeTools(ctx).dispatch('apply')
    assert result['changed']==['clip_02'] and result['input_repaired']==['clip_02']
    assert 'clip_01' in result['argument_errors'] and 'clip_02' not in result['blocked']
    assert len(list((ctx.episode_dir/'work/input_repairs').glob('*.json')))==1


def test_image_failure_restores_episode_phase_ledger_and_never_changes_shared_ledger(setup, monkeypatch):
    ctx, before, shared = setup
    original = (shared/'phases.json').read_bytes()
    def fail(*a):
        raise ValueError('image provider failed')
    monkeypatch.setattr(input_repair, 'build_phases', fail)
    phases = {'characters': {'甲': [{'appearance': '穿着黑色披风', 'wears': '黑色披风'}]}}
    with pytest.raises(ValueError, match='image provider failed'):
        input_repair.repair(ctx, decision(phases=phases))
    assert (shared/'phases.json').read_bytes() == original
    assert (ctx.novel_dir/'series_assets/phases.json').read_bytes() == original
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


def test_phase_builds_overlap_by_character_and_keep_completed_work(setup, monkeypatch):
    import subprocess
    import threading
    from types import SimpleNamespace
    ctx, _, _ = setup
    rendezvous = threading.Barrier(3)
    calls = []
    def run(command, **kwargs):
        name = command[command.index('--only')+1]
        calls.append((name, kwargs.get('timeout')))
        rendezvous.wait(timeout=2)
        if name == '乙':
            return SimpleNamespace(returncode=2)
        (ctx.run_dir/f'{name}-completed').write_text('retained')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(input_repair.subprocess, 'run', run)
    with pytest.raises(ValueError, match='乙'):
        input_repair.build_phases(ctx, {'甲', '乙', '丙'})
    assert {name for name, _ in calls} == {'甲', '乙', '丙'}
    assert all(timeout is None for _, timeout in calls)
    assert (ctx.run_dir/'甲-completed').exists() and (ctx.run_dir/'丙-completed').exists()


def test_input_reuses_registered_character_state_without_changing_its_design(setup, monkeypatch):
    ctx, _, shared = setup
    state = {'asset_id':'character_002-cloak', 'from':1, 'to':1,
             'label':'黑披风', 'appearance':'黑发青年', 'base_costume':'黑色披风'}
    atomic_write_json(shared/'phases.json', {'characters':{'甲':[state]}})
    card = shared/'characters/character_002-cloak/turnaround.jpeg'
    card.parent.mkdir();card.write_bytes(b'existing registered image')
    original = card.read_bytes()
    seen = []
    monkeypatch.setattr(input_repair, 'source_check', lambda source, quote, shots, phases, *a:
                        seen.append(phases) or {'consistent':True,'problems':[]})
    def build(context, names):
        current=read_json(context.novel_dir/'series_assets/phases.json')['characters']['甲'][0]
        assert current['asset_id']==state['asset_id'] and current['base_costume']==state['base_costume']
    monkeypatch.setattr(input_repair, 'build_phases', build)
    answer=input_repair.repair(ctx, decision(phases={'characters':{'甲':{'reuse_asset_id':state['asset_id']}}}))
    assert answer['input_repaired']==['clip_01']
    assert seen[0]['甲'][0]['base_costume']=='黑色披风'
    assert all(c['references'][2]['asset_id']==state['asset_id'] for c in read_json(ctx.episode_dir/'clip_plan.json')['clips'])
    assert card.read_bytes()==original and history.generation_counts(ctx.episode_dir)=={}


@pytest.mark.parametrize('phase', [
    {'reuse_asset_id':'character_002-missing'},
    {'reuse_asset_id':'character_001-someone-else'},
    {'reuse_asset_id':'character_002-no-image'},
    {'reuse_asset_id':'character_002-cloak','base_costume':'白袍'},
])
def test_invalid_phase_reuse_is_rejected_before_spending_or_drawing(setup, monkeypatch, phase):
    ctx, before, shared = setup
    atomic_write_json(shared/'phases.json',{'characters':{
        '乙':[{'asset_id':'character_001-someone-else','from':1,'to':1}],
        '甲':[{'asset_id':'character_002-no-image','from':1,'to':1}]}})
    other=shared/'characters/character_001-someone-else/turnaround.jpeg'
    other.parent.mkdir();other.write_bytes(b'other actor')
    monkeypatch.setattr(input_repair, 'source_check', lambda *a:pytest.fail('invalid reuse spent model work'))
    monkeypatch.setattr(input_repair, 'build_phases', lambda *a:pytest.fail('invalid reuse drew images'))
    with pytest.raises(input_repair.RepairArgumentError, match='reuse_asset_id'):
        input_repair.repair(ctx, decision(phases={'characters':{'甲':phase}}))
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    assert read_json(ctx.episode_dir/'clip_plan.json')==before


def test_phase_cards_rebind_all_dependent_clips_without_changing_original_cards(setup, monkeypatch):
    ctx, _, shared = setup
    def build(ctx, names):
        ledger = read_json(ctx.novel_dir/'series_assets/phases.json')['characters']
        for name in names:
            card = ctx.novel_dir/'series_assets/characters'/ledger[name][0]['asset_id']/'turnaround.jpeg'
            card.parent.mkdir(parents=True)
            card.write_bytes(b'new phase image')
    monkeypatch.setattr(input_repair, 'build_phases', build)
    answer = input_repair.repair(ctx, decision(phases={'characters': {'甲': [{'wears': '黑色披风'}]}}))
    assert len(answer['changed']) == 8
    assert read_json(shared/'phases.json')['characters'] == {}
    assert history.generation_counts(ctx.episode_dir) == {}
    assert (ctx.work_dir/'input/phases.json').is_file()
    after = read_json(ctx.episode_dir/'clip_plan.json')
    assert all('agent-' in c['references'][2]['path'] for c in after['clips'])


def test_exhausted_affected_clip_refuses_before_drawing(setup, monkeypatch):
    ctx, before, _ = setup
    monkeypatch.setattr(history, 'generation_counts', lambda d: {'clip_02': 3})
    monkeypatch.setattr(input_repair, 'build_phases', lambda *a: pytest.fail('no remaining budget'))
    with pytest.raises(ValueError, match='remaining video budget'):
        input_repair.repair(ctx, decision(phases={'characters': {'甲': [{'wears': '黑色披风'}]}}))
    assert read_json(ctx.episode_dir/'clip_plan.json') == before


def test_agent_apply_routes_supported_input_repair_and_keeps_the_same_budget(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {'blocked_clips': {'clip_01': ['wrong scene']}})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [decision()]})
    answer = EpisodeTools(ctx).dispatch('apply')
    assert answer['ok'] is True
    assert answer['input_repaired'] == ['clip_01'] and answer['changed'] == ['clip_01']
    assert answer['generation_counts'] == {}


def test_audited_tool_errors_preserve_records_without_consuming_input_attempts(setup):
    ctx, _, _ = setup
    receipts = ctx.episode_dir/'work/input_repairs'
    for i in [1, 2]:
        atomic_write_json(receipts/f'clip_01-{i}.json', {'status': 'tool_error', 'clip_id': 'clip_01'})
    answer = input_repair.repair(ctx, decision())
    assert answer['changed'] == ['clip_01']
    assert read_json(receipts/'clip_01-1.json')['status'] == 'tool_error'
    assert read_json(receipts/'clip_01-3.json')['status'] == 'prepared'
    assert history.generation_counts(ctx.episode_dir) == {}


def test_common_input_limit_stops_after_one_source_repair(setup):
    ctx, _, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*': {'limit': 2, 'input_limit': 1, 'reason': 'advance'}})
    input_repair.repair(ctx, decision())
    with pytest.raises(ValueError, match='input repair allowance used'):
        input_repair.repair(ctx, decision(shots=[{'index': 1, 'location': '另一间宿舍'}]))


def test_failed_translation_cannot_publish_a_plan_without_english(setup, monkeypatch):
    ctx, before, _ = setup
    def missing(clip, **kwargs):
        clip.pop('prompt_h3', None)
        return False
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert', missing)
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda *a, **k: pytest.fail('missing English is not an executable candidate'))
    with pytest.raises(ValueError, match='no English H3 request'):
        input_repair.repair(ctx, decision())
    assert read_json(ctx.episode_dir/'clip_plan.json') == before
    assert history.generation_counts(ctx.episode_dir) == {}
    assert history.load(ctx.episode_dir)['trials'] == []


def test_translation_rejection_reports_its_reason_and_cannot_publish_stale_english(setup, monkeypatch):
    ctx, before, _ = setup
    def reject(clip, **kwargs):
        if kwargs.get('problems') is not None:
            kwargs['problems'].append('Current location reference shows an exterior but this shot is indoors')
        return False  # the candidate still carries old English from its previous inputs
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert', reject)
    with pytest.raises(ValueError, match='Current location reference shows an exterior'):
        input_repair.repair(ctx, decision())
    assert read_json(ctx.episode_dir/'clip_plan.json') == before
    record = read_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json')
    assert 'Current location reference shows an exterior' in record['error']
    assert record['step'] == 'translate:clip_01'
    assert history.generation_counts(ctx.episode_dir) == {}
    assert history.load(ctx.episode_dir)['trials'] == []


def test_new_location_is_registered_and_referenced_only_in_this_episode(setup, monkeypatch):
    from novel_manga.application.agents.episode import input_assets
    ctx, before, shared = setup
    original = (shared/'phases.json').read_bytes()
    def draw(ctx, names):
        for name in names:
            phase = read_json(ctx.novel_dir/'series_assets/phases.json')['locations'][name][0]
            card = ctx.novel_dir/'series_assets/locations'/phase['asset_id']/'establishing.jpeg'
            card.parent.mkdir(parents=True, exist_ok=True)
            card.write_bytes(b'new empty bedroom')
    monkeypatch.setattr(input_assets, 'build_locations', draw)
    answer = input_repair.repair(ctx, decision(locations={'宿舍': '空置宿舍，木床和朝外的窗户'}))
    plan = read_json(ctx.episode_dir/'clip_plan.json')
    ref = next(r for r in plan['clips'][0]['references'] if r['role'] == 'location')
    assert answer['changed'] == ['clip_01'] and 'location_001-agent-' in ref['path']
    assert (ctx.novel_dir/ref['path']).read_bytes() == b'new empty bedroom'
    assert plan['clips'][1:] == before['clips'][1:]
    assert (shared/'phases.json').read_bytes() == original
    assert read_json(ctx.work_dir/'input/story_bible.json')['locations'] == ['宿舍：空置宿舍，木床和朝外的窗户']
    assert not (ctx.source_novel_dir/'story_bible.json').exists()
    assert history.generation_counts(ctx.episode_dir) == {}


def test_connection_failure_resumes_existing_images_and_translations_once(setup, monkeypatch):
    import httpx
    from novel_manga.application.profiles import h3_stamp
    ctx, before, shared = setup
    source_calls, drawn, translated, checks = [], [], [], []
    monkeypatch.setattr(input_repair, 'source_check', lambda *a: source_calls.append(1) or {'consistent': True})
    def draw(ctx, names):
        phases = read_json(ctx.novel_dir/'series_assets/phases.json')['characters']
        for name in names:
            card = ctx.novel_dir/'series_assets/characters'/phases[name][0]['asset_id']/'turnaround.jpeg'
            if not card.exists():
                card.parent.mkdir(parents=True, exist_ok=True)
                card.write_bytes(b'completed image'); drawn.append(str(card))
    monkeypatch.setattr(input_repair, 'build_phases', draw)
    def translate(clip, **kwargs):
        assert kwargs['source_script']['shots'][0]['location'] == '宿舍'
        clip['prompt_h3_of'] = h3_stamp(clip, '')
        translated.append(clip['clip_id'])
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert', translate)
    def check(*args):
        checks.append(1)
        if len(checks) == 1:
            raise httpx.ConnectError('service unavailable after drawing')
        return {'consistent': True, 'problems': []}
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency', check)
    row = decision(phases={'characters': {'甲': [{'wears': '黑色披风'}]}})
    with pytest.raises(httpx.ConnectError):
        input_repair.repair(ctx, row)
    assert read_json(ctx.episode_dir/'clip_plan.json') == before
    state = input_repair.repair_status(ctx)['clip_01']
    assert state['status'] == 'incomplete' and state['step'] == 'check:clip_01'
    result = input_repair.repair(ctx, state['resume_action'])
    assert len(result['changed']) == 8 and len(drawn) == len(source_calls) == 1
    assert len(translated) == 8  # the first translated request was kept across the connection error
    assert len(history.load(ctx.episode_dir)['trials']) == 1
    assert history.generation_counts(ctx.episode_dir) == {}
    again = input_repair.repair(ctx, row)
    assert again['reused'] is True and again['changed'] == []
    assert len(drawn) == len(history.load(ctx.episode_dir)['trials']) == 1


def test_continuation_is_bounded_without_spending_a_new_input_candidate(setup, monkeypatch):
    import httpx
    ctx, _, _ = setup
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda *a: (_ for _ in ()).throw(httpx.ConnectError('offline')))
    for _ in range(2):
        with pytest.raises(httpx.ConnectError):
            input_repair.repair(ctx, decision())
    with pytest.raises(ValueError, match='continuation allowance used'):
        input_repair.repair(ctx, decision())
    assert len(list((ctx.episode_dir/'work/input_repairs').glob('*.json'))) == 1


def test_apply_groups_identical_costume_repairs_and_defers_stale_prompt_edits(setup, monkeypatch):
    ctx, _, _ = setup
    calls = []
    def repair(ctx, row):
        calls.append(row['clip_id'])
        return {'changed': ['clip_01', 'clip_02', 'clip_03'], 'input_repaired': [row['clip_id']]}
    monkeypatch.setattr(input_repair, 'repair', repair)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {'blocked_clips': {
        'clip_01': ['outfit'], 'clip_02': ['outfit'], 'clip_03': ['outfit']}})
    first = decision(shots=[], phases={'characters': {'甲': [{'wears': '黑色披风'}]}})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [first,
        {**first, 'clip_id': 'clip_02'}, {'clip_id': 'clip_03', 'action': 'edit', 'reason': 'old prompt', 'prompt_h3': 'stale'}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is True and calls == ['clip_01']
    assert result['deferred'] == ['clip_03'] and result['blocked'] == {}


def test_wearing_checks_without_edits_keep_their_clip_scope():
    assert input_repair.effects({'clip_id': 'clip_01'}) != input_repair.effects({'clip_id': 'clip_02'})
    assert input_repair.effects({'clip_id': 'clip_01', 'phases': {'characters': {}}}) == input_repair.effects({'clip_id': 'clip_01'})


def test_rejected_owner_gets_one_changed_proposal_without_repeating_rejection(setup, monkeypatch):
    ctx, _, _ = setup
    calls = []
    def reject(*a):
        calls.append(1)
        raise input_repair.UnsupportedInput('garment belongs to another person')
    monkeypatch.setattr(input_repair, 'source_check', reject)
    with pytest.raises(input_repair.UnsupportedInput):
        input_repair.repair(ctx, decision())
    assert input_repair.repair_status(ctx)['clip_01']['revision_available']
    with pytest.raises(ValueError, match='same input correction'):
        input_repair.repair(ctx, decision())
    with pytest.raises(input_repair.UnsupportedInput):
        input_repair.repair(ctx, decision(shots=[{'index': 1, 'wears': {}}]))
    with pytest.raises(ValueError, match='revision allowance'):
        input_repair.repair(ctx, decision(shots=[{'index': 1, 'wears': {'甲': None}}]))
    assert len(calls) == 2 and history.generation_counts(ctx.episode_dir) == {}


def test_technical_continuation_does_not_consume_the_source_revision(setup, monkeypatch):
    ctx, _, _ = setup
    import httpx
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda *a: (_ for _ in ()).throw(httpx.ConnectError('offline')))
    for _ in range(2):
        with pytest.raises(httpx.ConnectError):
            input_repair.repair(ctx, decision())
    assert input_repair.repair_status(ctx)['clip_01']['revision_available']
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda *a: {'consistent': True, 'problems': []})
    assert input_repair.repair(ctx, decision(shots=[{'index': 1, 'wears': {}}]))['changed'] == ['clip_01']
    record = read_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json')
    assert record['revision_count'] == record['resume_count'] == 1 and 'error' not in record
    assert history.generation_counts(ctx.episode_dir) == {}


def test_host_continues_approved_translation_but_leaves_source_and_posture_decisions_to_agent(setup):
    from novel_manga.application.agents.episode.flow import _pending_input_continuations
    ctx, _, _ = setup
    folder = ctx.episode_dir/'work/input_repairs'
    for cid, step, source in [('clip_01', 'translate:clip_01', {'consistent': True}),
                              ('clip_02', 'source', {}), ('clip_03', 'compile', {'consistent': True})]:
        atomic_write_json(folder/f'{cid}-1.json', {'clip_id': cid, 'status': 'incomplete',
            'step': step, 'source': source, 'decision': decision(clip_id=cid), 'resume_count': 0})
    assert _pending_input_continuations(ctx)
    rows = read_json(ctx.work_dir/'output/resume-inputs.json')['decisions']
    assert [r['clip_id'] for r in rows] == ['clip_01'] and rows[0]['resume'] is True


def test_authored_timing_recovery_deduplicates_the_same_asset_with_noop_source_patches(setup):
    from novel_manga.application.agents.episode.flow import _pending_input_continuations
    ctx, _, _ = setup
    ctx.input_mode = 'source'
    for i in [1, 2]:
        cid = f'clip_{i:02d}'
        atomic_write_json(ctx.episode_dir/f'work/input_repairs/{cid}-1.json', {
            'clip_id': cid, 'status': 'incomplete', 'step': 'compile', 'resume_count': 0,
            'error': 'ValueError: input repair changed protected timing or shot addresses',
            'source': {'consistent': True}, 'decision': decision(clip_id=cid,
                shots=[{'index': i, 'wears': {}}], phases={'characters': {'甲': [{'wears': '黑色披风'}]}})})
    assert _pending_input_continuations(ctx)
    rows = read_json(ctx.work_dir/'output/resume-inputs.json')['decisions']
    assert len(rows) == 1


def test_input_publication_keeps_the_posture_and_visor_ledgers_used_by_compilation(setup, monkeypatch):
    from novel_manga.application.repair import flow
    ctx, _, _ = setup
    rebuild = flow.rebuild_clips
    posture = {'stages': {'1': {'start': {'甲': {'posture': '坐'}}}}, 'issues': []}
    visor = {'stages': {'1': {'甲': 'open'}}}
    def with_ledgers(*args, repack_report=None, **kwargs):
        repack_report.update(_posture_states=posture, _visor_states=visor)
        return rebuild(*args,**kwargs)
    monkeypatch.setattr(flow, 'rebuild_clips', with_ledgers)
    input_repair.repair(ctx, decision())
    assert read_json(ctx.episode_dir/'posture_states.json') == posture
    assert read_json(ctx.episode_dir/'visor_states.json') == visor


def test_process_restart_continues_the_current_input_operation_without_another_allowance(setup):
    ctx, _, _ = setup
    path = ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(path, {'clip_id': 'clip_01', 'status': 'checking', 'step': 'translate:clip_01',
        'decision': decision(), 'source': {'consistent': True}, 'resume_count': 1})
    assert input_repair.repair_status(ctx)['clip_01']['resume_action']['resume'] is True
    result = input_repair.repair(ctx, {'clip_id': 'clip_01', 'resume': True, 'reason': 'Resume stopped process'})
    assert result['changed'] == ['clip_01']
    assert read_json(path)['resume_count'] == 1 and read_json(path)['status'] == 'prepared'
    assert history.generation_counts(ctx.episode_dir) == {}


def test_shared_phase_can_correct_all_dependent_source_wearing_fields(setup, monkeypatch):
    ctx, _, _ = setup
    def draw(ctx, names):
        phases = read_json(ctx.novel_dir/'series_assets/phases.json')['characters']
        for name in names:
            card = ctx.novel_dir/'series_assets/characters'/phases[name][0]['asset_id']/'turnaround.jpeg'
            card.parent.mkdir(parents=True, exist_ok=True)
            card.write_bytes(b'phase image')
    monkeypatch.setattr(input_repair, 'build_phases', draw)
    result = input_repair.repair(ctx, decision(
        phases={'characters': {'甲': [{'wears': '黑色披风'}]}},
        shots=[{'index': 1, 'wears': {'甲': '黑色披风', '乙': None}}, {'index': 2, 'wears': {'乙': None}}]))
    assert len(result['changed']) == 8
    assert read_json(ctx.episode_dir/'chapter_script.json')['shots'][1]['wears'] == {'乙': None}


def test_shared_wrong_wearer_can_be_removed_from_blocked_shots_without_drawing_cards(setup, monkeypatch):
    ctx, before, _ = setup
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {'blocked_clips': {
        'clip_01': ['wrong wearer'], 'clip_02': ['wrong wearer']}})
    monkeypatch.setattr(input_repair, 'build_phases', lambda *a: pytest.fail('correct card should be reused'))
    result = input_repair.repair(ctx, decision(shots=[{'index': 1, 'wears': {}}, {'index': 2, 'wears': {}}]))
    assert result['changed'] == ['clip_01', 'clip_02']
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][2:] == before['clips'][2:]
    assert history.generation_counts(ctx.episode_dir) == {}


def test_scoped_costume_repair_does_not_require_another_take_of_an_earlier_accepted_clip(setup, monkeypatch):
    ctx, before, _ = setup
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*': {'limit': 2}})
    monkeypatch.setattr(history, 'generation_counts', lambda *a: {'clip_01': 2})
    scopes = []
    monkeypatch.setattr(input_repair, 'source_check', lambda *a: scopes.append(a[-1]) or {'consistent': True})
    def draw(ctx, names):
        phase = read_json(ctx.novel_dir/'series_assets/phases.json')['characters']['甲'][0]
        card = ctx.novel_dir/'series_assets/characters'/phase['asset_id']/'turnaround.jpeg'
        card.parent.mkdir(parents=True, exist_ok=True); card.write_bytes(b'new state')
    monkeypatch.setattr(input_repair, 'build_phases', draw)
    result = input_repair.repair(ctx, decision(clip_id='clip_02', clip_ids=['clip_02'], shots=[],
        phases={'characters': {'甲': [{'wears': '黑色披风'}]}}))
    assert result['changed'] == ['clip_02']
    after = read_json(ctx.episode_dir/'clip_plan.json')
    assert after['clips'][0] == before['clips'][0] and after['clips'][2:] == before['clips'][2:]
    assert len(scopes[0]) == 1 and scopes[0][0]['index'] == 2


def test_keep_of_already_accepted_take_does_not_abort_a_successful_input_repair(setup, monkeypatch):
    from novel_manga.review.contracts import REVIEW_POLICY
    from novel_manga.review.storage import take_identity
    ctx, _, _ = setup
    video = ctx.episode_dir/'clip_02.mp4'; video.write_bytes(b'current reviewed video')
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {'blocked_clips': {'clip_01': ['input']},
        'clips': [{'clip_id': 'clip_02', 'selected': {'video': str(video), 'passed': True}}]})
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'policy': REVIEW_POLICY, 'review_mode':'classic',
        'clips': {'clip_02': {'severity': 'pass', 'video': str(video), 'take': take_identity(video)}}, 'feedback': {}})
    monkeypatch.setattr(input_repair, 'repair', lambda *a: {'changed': ['clip_01'], 'input_repaired': ['clip_01']})
    monkeypatch.setattr('novel_manga.application.review.execution.recheck_current_clip',
                        lambda *a: pytest.fail('already verified current take requires no extra review'))
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [decision(),
        {'clip_id': 'clip_02', 'action': 'keep', 'reason': 'Already passed'}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] and result['changed'] == ['clip_01'] and result['accepted'] == ['clip_02']
    assert read_json(ctx.run_dir/'tool-repair-decisions.json')[-1]['input_repaired'] == ['clip_01']
