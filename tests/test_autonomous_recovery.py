from novel_manga.media.context import RenderContext, RenderOptions, RenderState
import json
from types import SimpleNamespace
import pytest

from novel_manga.application.agents.episode import recovery
from novel_manga.util import atomic_write_json, read_json


def context(tmp_path):
    ctx = SimpleNamespace(run_dir=tmp_path / 'run', work_dir=tmp_path / 'run/sandbox', episode_dir=tmp_path / 'run/book/book_1', source_novel_dir=tmp_path / 'book', source_episode_dir=tmp_path / 'book/book_1')
    return ctx


def test_diagnosis_names_overfull_speech_and_publication_only_target(tmp_path):
    ctx = context(tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [
        {'clip_id': 'clip_01', 'kind': 'video', 'request_seconds': 15, 'spoken_text': '长对白'*80}]})
    atomic_write_json(ctx.work_dir/'input/latest-review.json', {'targets': [{
        'clip': {'clip_id': 'clip_01'}, 'selected_audio': {'passed': False,
        'issues': ['missing_0.9_over_0.5']}}]})
    status = {'repair_actions': {'clip_01': ['input']}, 'job': {'action': 'finish',
        'result': {'error': 'publication_gate', 'clips': ['clip_01']}}, 'media': {}}
    result = recovery.diagnose(ctx, status)
    assert {'speech_overflow', 'publication_gate'} <= {r['kind'] for r in result['issues']}
    assert result['issues'][0]['clips'] == ['clip_01']
    assert result['actionable'] is True


def test_source_judge_fix_reopens_only_old_rejection_once_with_remaining_time(tmp_path):
    ctx = context(tmp_path)
    atomic_write_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json', {
        'status': 'rejected', 'step': 'source', 'clip_id': 'clip_01',
        'decision': {'clip_id': 'clip_01', 'shots': [{'index': 1, 'location': '房间'}]}})
    old = {'status': 'blocked', 'passed': False, 'remaining_seconds': 333,
           'no_progress': 2, 'attempts': ['old'], 'agent_seconds': 125,
           'reason': 'episode agent ended twice without operation or output progress',
           'job': {'id': 'job-10', 'action': 'finish', 'status': 'completed', 'result': {'passed': False}}}
    atomic_write_json(ctx.run_dir/'agent-run.json', old)
    resumed = recovery.resume_source_review(ctx.run_dir, ctx.episode_dir, old)
    assert resumed['status'] == 'deferred' and resumed['remaining_seconds'] == 333
    assert resumed['attempts'] == ['old'] and resumed['agent_seconds'] == 125
    assert resumed['no_progress'] == 0 and resumed.get('job') is None
    assert read_json(ctx.run_dir/'source-review-recovery.json')['previous'] == old
    assert recovery.resume_source_review(ctx.run_dir, ctx.episode_dir, {**resumed, 'status': 'blocked'}) is None


@pytest.mark.parametrize('status,remaining,step,policy,later_prepared', [
    ('finished', 333, 'source', 1, False), ('paused', 333, 'source', 1, False),
    ('blocked', 0, 'source', 1, False), ('blocked', 333, 'compile', 1, False),
    ('blocked', 333, 'source', 2, False), ('blocked', 333, 'source', 1, True)])
def test_source_recovery_leaves_other_stops_and_newer_repairs_untouched(tmp_path, status, remaining, step, policy, later_prepared):
    ctx = context(tmp_path)
    folder = ctx.episode_dir/'work/input_repairs'
    atomic_write_json(folder/'clip_01-1.json', {'status': 'rejected', 'step': step,
        'clip_id': 'clip_01', 'source_review_policy': policy, 'decision': {'clip_id': 'clip_01'}})
    if later_prepared:
        atomic_write_json(folder/'clip_01-2.json', {'status': 'prepared', 'clip_id': 'clip_01', 'decision': {'clip_id': 'clip_01'}})
    saved = {'status': status, 'passed': status == 'finished', 'remaining_seconds': remaining}
    assert recovery.resume_source_review(ctx.run_dir, ctx.episode_dir, saved) is None
    assert not (ctx.run_dir/'source-review-recovery.json').exists()


def test_memory_remembers_failure_without_claiming_it_worked(tmp_path):
    ctx = context(tmp_path)
    diagnosis = {'issues': [{'kind': 'speech_overflow', 'clips': ['clip_01']}], 'actionable': True}
    recovery.remember(ctx, diagnosis, {'action': 'apply', 'ok': False, 'blocked': {'clip_01': 'bad arguments'}})
    rows = recovery.recall(SimpleNamespace(**{**vars(ctx), 'run_dir':tmp_path/'next-run'}), diagnosis)
    assert len(rows) == 1 and rows[0]['outcome'] == 'unresolved'
    recovery.remember(ctx, diagnosis, {'action': 'finish', 'ok': True, 'passed': True})
    assert recovery.recall(SimpleNamespace(**{**vars(ctx), 'run_dir':tmp_path/'next-run'}), diagnosis)[0]['outcome'] == 'accepted'
    assert len(recovery.recall(SimpleNamespace(**{**vars(ctx), 'run_dir':tmp_path/'next-run'}), diagnosis)) == 1


def test_pipeline_memory_contains_only_reviewed_media_repairs_and_documents(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'memory_scope':'post_render_repair'})
    diagnosis={'issues':[{'kind':'speech','clips':['clip_01'],'detail':'misheard name'}]}
    recovery.remember(ctx,diagnosis,{'action':'apply','ok':False})
    folder=ctx.source_novel_dir/'.production-memory'
    assert not list(folder.glob('*.json'))
    video=ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True);video.write_bytes(b'actual take')
    recovery.remember(ctx,diagnosis,{'action':'apply','ok':True,'changed':['clip_01']},
        [{'action':'keep','clip_id':'clip_01','reason':'rechecked actual audio'}])
    record=read_json(folder/(ctx.run_dir.name+'.json'))
    assert record['scope']=='post_render_repair' and record['outcome']=='unresolved'
    assert 'misheard name' in (folder/(ctx.run_dir.name+'.md')).read_text()
    assert ctx.run_dir.name+'.md' in (folder/'index.md').read_text()
    recovery.remember(ctx,diagnosis,{'action':'finish','ok':True,'passed':True})
    assert 'accepted' in (folder/'index.md').read_text()


def test_semantic_repair_memory_is_unverified_until_film_acceptance(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'memory_scope':'repair_only','agent_phase':'input_repair'})
    diagnosis={'issues':[{'kind':'input','clips':['clip_01'],'detail':'public dialogue should be private thought'}]}
    recovery.remember(ctx,diagnosis,{'action':'apply','ok':True,'input_repaired':['clip_01']})
    record=read_json(ctx.source_novel_dir/'.production-memory'/f'{ctx.run_dir.name}.json')
    assert record['scope']=='semantic_input_repair' and record['outcome']=='unresolved'
    assert recovery.recall(SimpleNamespace(**{**vars(ctx), 'run_dir':tmp_path/'next-run'}),diagnosis)[0]['outcome']=='unresolved'


def test_preparation_exception_provides_actionable_diagnosis_before_adoption(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'input/preparation/exception.json',{'failure':{
        'audit':{'issues':[{'kind':'location','stage':3,'problem':'room was changed to lobby'}]}}})
    result=recovery.diagnose(ctx,{'adopted':False})
    assert result['actionable'] and 'room was changed to lobby' in str(result['issues'])


def test_staged_local_inputs_diagnose_current_asset_failure_not_old_source_issue(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'input/preparation/exception.json',{'failure':{
        'audit':{'issues':[{'problem':'stale wrong owner'}]}}})
    atomic_write_json(ctx.work_dir/'input/latest-preparation.json',{
        'source_audit':None,'blocked':{'clip_01':'missing current room card'}})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{
        'status':'inputs_failed','blocked_clips':{'clip_01':'missing current room card'}})
    result=recovery.diagnose(ctx,{'adopted':True,'repair_actions':{'clip_01':['input']}})
    assert 'missing current room card' in str(result['issues'])
    assert 'stale wrong owner' not in str(result['issues'])


def test_two_summary_completions_do_not_select_fallback(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'old'})
    atomic_write_json(ctx.episode_dir/'repair_history/history.json',{'takes':3})
    config={'model':'DS','refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'KEY'}}
    result={'deadline':1234,'agent_seconds':70,'fallback_invocations':2}
    attempt=SimpleNamespace(record={'summary_only':True},directory=tmp_path/'attempt')
    assert recovery.switch_for_refusal(ctx,config,result,attempt,{}, {}) is None
    replacement=recovery.switch_for_refusal(ctx,config,result,attempt,{}, {})
    assert replacement is None
    assert result['deadline']==1234 and result['agent_seconds']==70
    assert result['fallback_invocations']==2
    assert read_json(ctx.episode_dir/'repair_history/history.json')=={'takes':3}
    assert recovery.switch_for_refusal(ctx,config,result,attempt,{}, {}) is None


def test_summary_after_real_handoff_does_not_restart_context(tmp_path):
    ctx=context(tmp_path)
    result={'summary_only_turns':1}
    attempt=SimpleNamespace(record={'summary_only':True},directory=tmp_path/'attempt')
    config={'refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'KEY'}}
    assert recovery.switch_for_refusal(ctx,config,result,attempt,{}, {'job':{'id':'new'}}) is None
    assert result['summary_only_turns']==0


def test_summary_followed_by_unexecuted_json_is_not_provider_failure(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'same'})
    config={'model':'DS','refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'KEY'}}
    result={'deadline':1234,'agent_seconds':377}
    for index,summary in enumerate([True,False]):
        attempt=SimpleNamespace(record={'summary_only':summary,'exit':0,'produced':[]},directory=tmp_path/f'attempt{index}')
        replacement=recovery.switch_for_refusal(ctx,config,result,attempt,{}, {})
    assert replacement is None
    assert result['deadline']==1234 and result['agent_seconds']==377
    assert recovery.switch_for_refusal(ctx,config,result,attempt,{}, {}) is None


def test_first_task_diagnosis_excludes_errors_for_already_filmed_clips(tmp_path):
    ctx=context(tmp_path)
    result=recovery.diagnose(ctx,{'adopted':True,'first_pass_remaining':['clip_07'],
        'repair_actions':{'clip_07':['input'],'clip_02':[]},
        'input_repairs':{'clip_02':{'error':"KeyError: old actor"}},'media':{}})
    assert not result['issues']


def test_old_interface_failure_resumes_once_with_original_time_and_receipts(tmp_path,monkeypatch):
    ctx=context(tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video'}]})
    monkeypatch.setattr('novel_manga.application.preparation.readiness.render_risk_choices',lambda *a:{'wearer_card':['甲'],'card_people':[]})
    saved={'status':'blocked','remaining_seconds':321,'no_progress':2,'attempts':['old'],
        'reason':'episode agent ended twice without operation or output progress',
        'job':{'action':'apply','status':'completed','result':{'blocked':{'clip_01':
             'risk acceptance must name current wearer/location reference risks'}}}}
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'clip_01':{'limit':2}})
    before=(ctx.episode_dir/'repair_budget_grants.json').read_bytes()
    resumed=recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,saved)
    assert resumed['status']=='deferred' and resumed['remaining_seconds']==321
    assert resumed['attempts']==['old'] and resumed['protocol_recovery']['kind']=='risk_arguments'
    assert (ctx.episode_dir/'repair_budget_grants.json').read_bytes()==before
    assert recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,{**resumed,'status':'blocked'}) is None


@pytest.mark.parametrize('status,remaining',[('paused',100),('finished',100),('blocked',0)])
def test_protocol_recovery_does_not_reopen_paused_finished_or_exhausted(tmp_path,status,remaining):
    ctx=context(tmp_path)
    saved={'status':status,'remaining_seconds':remaining,'job':{'status':'completed','result':{
        'blocked':{'clip_01':'risk acceptance must name current wearer/location reference risks'}}}}
    assert recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,saved) is None


def test_protocol_recovery_ignores_old_compile_error_behind_spent_take_and_prepared_receipt(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json',{
        'clip_id':'clip_01','status':'prepared','step':'complete','decision':{'clip_id':'clip_01'}})
    saved={'status':'blocked','remaining_seconds':321,'reason':'episode agent ended twice without operation or output progress',
        'job':{'status':'completed','result':{'blocked':{'clip_01':'effective clip retry budget used'}}},
        'stop_detail':{'input_repairs':{'clip_01':{'error':'input repair changed clip grouping; preserve existing cuts'}}}}
    assert recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,saved) is None
    assert not (ctx.run_dir/'protocol-recovery.json').exists()


def test_old_summary_then_json_refreshes_primary_context_with_unspent_time(tmp_path):
    ctx=context(tmp_path);attempts=[]
    for n in range(2):
        p=tmp_path/f'attempt{n}';attempts.append(str(p))
        atomic_write_json(p/'attempt.json',{'exit':0,'produced':[]})
        (p/'session.jsonl').write_text(json.dumps({'type':'result','result':'summary' if n==0 else '{"decisions": []}'})+'\n')
    saved={'status':'blocked','remaining_seconds':321,'attempts':attempts,
        'reason':'episode agent ended twice without operation or output progress'}
    resumed=recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,saved)
    assert resumed['protocol_recovery']['kind']=='idle_turns'
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'old'})
    config={'model':'DS','refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'KEY'}}
    replacement,continued=recovery.protocol_fallback(ctx,config,resumed)
    assert replacement['model']=='DS' and continued['remaining_seconds']==321
    assert replacement['context_restart'].startswith('idle-protocol-')
    assert continued['deadline']==resumed['deadline']


def summary_block(ctx):
    attempts=[]
    for i in range(2):
        folder=ctx.work_dir/f'attempts/{i}'
        atomic_write_json(folder/'attempt.json',{'summary_only':True})
        attempts.append(str(folder))
    saved={'status':'blocked','reason':'episode agent ended twice without operation or output progress',
        'remaining_seconds':123,'deadline':10,'attempts':attempts,'agent_seconds':88}
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'old'})
    return saved


def test_blocked_summary_resume_preserves_only_the_original_remaining_time(tmp_path,monkeypatch):
    ctx=context(tmp_path);saved=summary_block(ctx)
    monkeypatch.setattr(recovery.time,'time',lambda:1000)
    config={'model':'DS','refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'KEY'}}
    assert recovery.blocked_summary_loop(ctx.run_dir)
    replacement,resumed=recovery.resume_summary_loop(ctx,config,saved)
    assert replacement['model']=='DS' and resumed['deadline']==1123
    assert replacement['context_restart'].startswith('summary-context-')
    assert resumed['status']=='deferred' and resumed['agent_seconds']==88
    assert resumed['attempts']==saved['attempts'] and resumed['remaining_seconds']==123
    assert not recovery.blocked_summary_loop(ctx.run_dir)


def test_legacy_summary_recovery_does_not_refund_the_final_spent_slice(tmp_path,monkeypatch):
    ctx=context(tmp_path);saved=summary_block(ctx)
    stopped=recovery.time.mktime(recovery.time.strptime('2026-10-06 18:00:00','%Y-%m-%d %H:%M:%S'))
    saved.update(deadline=stopped+20, updated_at='2026-10-06 18:00:00', remaining_seconds=123)
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    monkeypatch.setattr(recovery.time,'time',lambda:stopped+500)
    config={'model':'DS','refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'KEY'}}
    _,resumed=recovery.resume_summary_loop(ctx,config,saved)
    assert resumed['remaining_seconds']==20 and resumed['deadline']==stopped+520


def test_blocked_without_prior_defer_can_recover_its_unspent_time(tmp_path):
    ctx=context(tmp_path);saved=summary_block(ctx)
    stopped=recovery.time.mktime(recovery.time.strptime('2026-10-06 18:00:00','%Y-%m-%d %H:%M:%S'))
    saved.update(deadline=stopped+20,updated_at='2026-10-06 18:00:00')
    saved.pop('remaining_seconds')
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    assert recovery.blocked_summary_loop(ctx.run_dir)


def test_refusal_switch_keeps_deadline_and_does_not_reset_history(tmp_path):
    ctx = context(tmp_path)
    atomic_write_json(ctx.work_dir/'episode-session.json', {'session_id': 'old'})
    atomic_write_json(ctx.episode_dir/'repair_history/history.json', {'takes': 3})
    config = {'model': 'DS', 'base_url': 'http://ds', 'key_var': 'DS_KEY',
              'refusal_fallback': {'model': 'Qwen', 'base_url': 'http://qwen', 'key_var': 'QWEN_KEY'}}
    result = {'deadline': 1234, 'agent_seconds': 70}
    attempt = SimpleNamespace(record={'refusal': 'I refuse to continue this operation.'}, directory=tmp_path/'attempt')
    replacement = recovery.switch_for_refusal(ctx, config, result, attempt, {}, {})
    assert replacement['model'] == 'Qwen' and result['deadline'] == 1234
    assert read_json(ctx.episode_dir/'repair_history/history.json') == {'takes': 3}
    assert not (ctx.work_dir/'episode-session.json').exists()
    assert recovery.switch_for_refusal(ctx, replacement, result, attempt, {}, {}) is None


def test_refusal_does_not_switch_after_effective_handoff_or_normal_exit(tmp_path):
    ctx = context(tmp_path)
    config = {'refusal_fallback': {'model': 'Qwen', 'base_url': 'http://qwen', 'key_var': 'KEY'}}
    attempt = SimpleNamespace(record={'refusal': 'I cannot help with this request.'}, directory=tmp_path/'attempt')
    assert recovery.switch_for_refusal(ctx, config, {}, attempt, {}, {'job': {'id': 'job-1'}}) is None
    attempt.record = {'exit': 124}
    assert recovery.switch_for_refusal(ctx, config, {}, attempt, {}, {}) is None


def test_refusal_detection_is_limited_to_assistant_refusal():
    assert recovery.refusal_reason("I won't operate this pipeline for that purpose.")
    assert recovery.refusal_reason('抱歉，我无法协助生成这类内容。')
    assert not recovery.refusal_reason('The clip failed because the tool rejected an invalid reference.')
    assert not recovery.refusal_reason('No take allowance remains; recording the unresolved result.')


def test_selected_phase_does_not_assert_an_unverified_base_gender(tmp_path):
    from novel_manga.application.review.evidence import character_cards
    from novel_manga.models.bible import Character
    character = Character.model_construct(name='灵', gender='男', appearance='old base', wardrobe='coat')
    card = tmp_path/'series_assets/characters/character_001-p2/turnaround.jpeg'
    card.parent.mkdir(parents=True); card.write_bytes(b'current-image')
    phase = {'from': 1, 'to': 10, 'asset_id': 'character_001-p2',
             'appearance': 'Source-supported twin sister with short hair', 'base_costume': 'white dress'}
    atomic_write_json(tmp_path/'series_assets/phases.json', {'characters': {'灵': [phase]}})
    clip = {'cast': ['灵'], 'references': [{'name': '灵', 'role': 'character',
        'asset_id': phase['asset_id'], 'path': str(card.relative_to(tmp_path))}]}
    rows, _, _ = character_cards(clip, SimpleNamespace(characters=[character]), tmp_path, 2)
    assert rows['灵'].gender != '男'
    assert rows['灵'].appearance == phase['appearance']
    assert character.gender == '男'


def test_explicit_source_checked_phase_gender_is_used():
    from novel_manga.application.identity.phases import phased
    from novel_manga.models.bible import Character
    character = Character.model_construct(name='灵', gender='男')
    assert phased(character, {'gender': '女'}).gender == '女'


def test_shared_shot_scope_expands_only_direct_dependencies():
    from novel_manga.application.agents.episode.input_repair import expand_shared_scope
    plan={'clips':[{'clip_id':'clip_01','shot_indexes':[1,2]},
                   {'clip_id':'clip_02','shot_indexes':[2,3]},
                   {'clip_id':'clip_03','shot_indexes':[4]}]}
    row={'clip_id':'clip_01','clip_ids':['clip_01'],'shots':[{'index':2,'location':'room'}]}
    fixed=expand_shared_scope(row,plan)
    assert fixed['clip_ids']==['clip_01','clip_02'] and row['clip_ids']==['clip_01']


def test_corrupt_primary_asset_uses_valid_alternate_and_reports_problem(tmp_path):
    from PIL import Image
    from novel_manga.application.agents.episode.authoring import usable_assets
    ctx=context(tmp_path);folder=ctx.work_dir/'input/refs';folder.mkdir(parents=True)
    (folder/'bad.jpeg').write_bytes(b'bad image')
    Image.new('RGB',(12,12)).save(folder/'good.jpeg')
    catalog=[{'id':'bad','name':'actor','role':'character','image':'input/refs/bad.jpeg'},
             {'id':'good','name':'actor','role':'character','image':'input/refs/good.jpeg'}]
    atomic_write_json(ctx.run_dir/'catalog.json',catalog)
    assets,pictures=usable_assets(ctx,catalog)
    assert [a['id'] for a in assets]==['good'] and 'good' in pictures
    assert read_json(ctx.work_dir/'input/unusable-assets.json')[0]['id']=='bad'
    assert [a['id'] for a in read_json(ctx.run_dir/'catalog.json')]==['good']


def test_diagnosis_uses_current_success_after_keep_not_old_export(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'input/latest-review.json',{'targets':[{
        'clip':{'clip_id':'clip_01'},'verdict':{'severity':'fail','feedback':'old wrong costume'},
        'selected_audio':{'passed':False,'issues':['missing_1.0_over_0.5']}}]})
    atomic_write_json(ctx.episode_dir/'episode_review.json',{'clips':{'clip_01':{'severity':'pass'}}})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{'clips':[
        {'clip_id':'clip_01','selected':{'passed':True,'issues':[]}}]})
    assert recovery.diagnose(ctx,{'adopted':True,'repair_actions':{'clip_01':[]}})['issues']==[]


@pytest.mark.parametrize('legacy', [True, False])
def test_route_restoration_requires_current_activity_and_model_pair(tmp_path, legacy):
    ctx=context(tmp_path)
    config={'model':'GLM','refusal_fallback':{'model':'Luna','base_url':'http://gateway','key_var':'KEY'}}
    saved={'agent_activity':'a','deadline':1234,'fallback_invocations':1,
           'upstream_recovery':{'authorization':'grant-a','proposals':2,'max_invocations':12}}
    attempt=SimpleNamespace(record={'refusal':'sensitive'},directory=tmp_path/'attempt')
    recovery.switch_for_refusal(ctx,config,saved,attempt,{}, {})
    marker=dict(saved['model_switch'])
    if legacy:
        saved['model_switch']={'from_model':'DS','to_model':'Qwen','reason':'old refusal'}
    else:
        restored,_=recovery.restore_model_route(config,saved)
        assert restored['model']=='Luna'
        saved['upstream_recovery']={**saved['upstream_recovery'],'authorization':'grant-b'}
    old=saved['model_switch']
    restored,clean=recovery.restore_model_route(config,saved)
    assert restored['model']=='GLM'
    assert clean['model_switch_history'][-1]==old and 'model_switch' not in clean
    assert clean['deadline']==1234 and clean['fallback_invocations']==2
    assert clean['upstream_recovery']['proposals']==2
    if not legacy:
        changed={**saved,'model_switch':marker}
        changed['upstream_recovery']={'authorization':'grant-a'}
        restored,_=recovery.restore_model_route({**config,'model':'new-primary'},changed)
        assert restored['model']=='new-primary'


def test_technical_fallback_uses_last_common_attempt_and_never_refunds_it(tmp_path):
    ctx=context(tmp_path)
    config={'model':'GLM','refusal_fallback':{'model':'Luna','base_url':'http://gateway','key_var':'KEY'}}
    result={'agent_activity':'a','deadline':1234,'fallback_invocations':0}
    attempt=SimpleNamespace(record={'failure_kind':'transient_api'},directory=tmp_path/'attempt')
    assert recovery.switch_for_refusal(ctx,config,result,attempt,{}, {},remaining=10) is None
    assert recovery.retry_failed_invocation(config,result,attempt,10) is not None
    fallback=recovery.switch_for_refusal(ctx,config,result,attempt,{}, {},remaining=10)
    assert fallback['model']=='Luna' and result['fallback_invocations']==2
    assert recovery.retry_failed_invocation(fallback,result,attempt,10) is None
    assert recovery.switch_for_refusal(ctx,fallback,result,attempt,{}, {},remaining=10) is None
    assert result['deadline']==1234


@pytest.mark.parametrize('remaining,proposals', [(0,1),(10,12)])
def test_no_fallback_when_time_or_invocation_allowance_is_exhausted(tmp_path,remaining,proposals):
    ctx=context(tmp_path)
    config={'model':'GLM','refusal_fallback':{'model':'Luna','base_url':'http://gateway','key_var':'KEY'}}
    result={'deadline':1234,'upstream_recovery':{'proposals':proposals,'max_invocations':12}}
    attempt=SimpleNamespace(record={'refusal':'sensitive'},directory=tmp_path/'attempt')
    assert recovery.switch_for_refusal(ctx,config,result,attempt,{}, {},remaining=remaining) is None
    assert 'model_switch' not in result
