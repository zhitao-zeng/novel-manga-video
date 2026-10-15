import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_repair_agent import configured
from novel_manga.application.repair import agent, managed, history
from novel_manga.util import atomic_write_json, read_json


def candidate(clip, *, wrong=False):
    state='Her hood is worn over her head.' if wrong else 'Her hood rests behind her neck.'
    return {'action':'edit','reason':'correct reference clothing','evidence':['input/prompt.txt'],
            'prompt':clip['prompt_h3'].replace('stands by the door.',f'stands by the door. {state}'),
            'run':'fixture','attempt':'1'}


def audit(directory):
    files=list((directory/'work/request_checks').glob('a.agent-proposals-*.json'))
    assert len(files)==1
    return read_json(files[0])


def test_exact_rejection_returns_to_agent_then_only_valid_candidate_is_adopted(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);before=copy.deepcopy(clips);calls=[]
    problem='参考图1的帽子垂在颈后，候选却写 hood worn over her head（请求行2,9,14；图1）'
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(copy.deepcopy(rejections or []))
        return candidate(clip,wrong=len(calls)==1)
    def check(clip,novel):
        wrong='hood is worn over her head' in clip['prompt_h3']
        return {'consistent':not wrong,'problems':[problem] if wrong else [],
                'observations':['帽子自然垂在颈后'],'findings':[]}
    monkeypatch.setattr(agent,'propose',propose);monkeypatch.setattr(agent,'check_request',check)
    result=managed.prepare(directory,['a'])
    assert result['changed']==['a'] and result['blocked']=={}
    assert len(calls)==2 and calls[0]==[]
    rejected=calls[1][0]
    assert rejected['stage']=='check_request' and problem in rejected['reason']
    assert rejected['candidate']==candidate(clips[0],wrong=True)
    assert rejected['check']['observations']==['帽子自然垂在颈后']
    after=read_json(directory/'clip_plan.json')['clips']
    assert after[0]['prompt_h3']==candidate(clips[0])['prompt'] and after[1]==before[1]
    trials=history.load(directory)['trials']
    assert len(trials)==1 and trials[0]['method']=='agent_prompt'
    assert not any(managed.generated_counts(history.load(directory)).values())
    assert [r['status'] for r in audit(directory)['candidates']]==['rejected','adopted']


def test_three_rejected_candidates_stay_blocked_and_same_input_does_not_restart(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(copy.deepcopy(rejections or []));return candidate(clip,wrong=True)
    monkeypatch.setattr(agent,'propose',propose)
    monkeypatch.setattr(agent,'check_request',lambda *a:{'consistent':False,'problems':['帽子仍错']})
    first=managed.prepare(directory,['a']);again=managed.prepare(directory,['a'])
    assert len(calls)==3 and list(map(len,calls))==[0,1,2]
    assert first['blocked']==again['blocked'] and first['skip_render'] and again['skip_render']
    assert read_json(directory/'clip_plan.json')['clips']==clips
    assert not history.load(directory)['trials']
    assert [r['status'] for r in audit(directory)['candidates']]==['rejected']*3


def test_keep_cannot_clear_final_movie_issue_and_returns_to_agent(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    review=read_json(directory/'episode_review.json')
    review['clips']['a']['final_cut']={'movie':'actual-delivery.mp4','issues':[{
        'clip_ids':['a'],'problem':'two phones visible in the final movie'}]}
    atomic_write_json(directory/'episode_review.json',review)
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(copy.deepcopy(rejections or []))
        if len(calls)==1:return {'action':'keep','reason':'native was fine','evidence':[]}
        return candidate(clip)
    monkeypatch.setattr(agent,'propose',propose)
    monkeypatch.setattr(agent,'check_request',lambda *a:{'consistent':True,'problems':[]})
    monkeypatch.setattr('novel_manga.application.repair.source_recheck.prepare_source_recheck',
                        lambda *a:pytest.fail('native keep is insufficient'))
    result=managed.prepare(directory,['a'])
    assert result['changed']==['a'] and len(calls)==2
    assert calls[1][0]['stage']=='unresolved_final_keep'
    assert [r['status'] for r in audit(directory)['candidates']]==['rejected','adopted']


def test_sandbox_validation_rejection_exports_candidate_file_and_feedback_before_second_run(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);config={'runs_root':str(tmp_path/'runs')};calls=[]
    wrong=candidate(clips[0])['prompt'].replace('[Shot 1]','[Shot 2]')
    monkeypatch.setattr(agent,'load_config',lambda:config)
    def frames(d,cid,video,target):
        target.mkdir(parents=True);path=target/'frame.jpg';path.write_bytes(b'frame');return [path],'fixture'
    monkeypatch.setattr(agent,'provided_frames',frames)
    def run(name,**kwargs):
        calls.append(name);work=Path(config['runs_root'])/name
        primary=read_json(work/'input/primary.json');evidence=read_json(work/'input/evidence.json')
        assert kwargs['timeout']==600
        if len(calls)==2:
            rejected=primary['rejections'][0]
            assert rejected['stage']=='validate_request'
            assert 'shot timing' in rejected['reason']
            assert (work/rejected['candidate']['prompt_path']).read_text()==wrong
            assert evidence['rejections']==primary['rejections']==read_json(work/'input/rejections.json')
            assert 'host rejection' in (work/'prompt.txt').read_text().lower()
        atomic_write_json(work/'output/decision.json',{'action':'edit','reason':'correct only allowed directions',
                                                     'evidence':['input/prompt.txt']})
        (work/'output/prompt.txt').write_text(wrong if len(calls)==1 else candidate(clips[0])['prompt'])
        return SimpleNamespace(ok=True,produced=['decision.json','prompt.txt'],attempt='1')
    monkeypatch.setattr(agent,'run_agent',run)
    monkeypatch.setattr(agent,'check_request',lambda *a:{'consistent':True,'problems':[]})
    result=managed.prepare(directory,['a'])
    assert result['changed']==['a'] and len(calls)==2
    assert len(list(Path(config['runs_root']).glob('repair-*')))==2
    assert [r['status'] for r in audit(directory)['candidates']]==['rejected','adopted']


def test_final_proposal_validation_rejection_also_returns_exact_candidate(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[];valid=agent.final_proposal
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(copy.deepcopy(rejections or []));return candidate(clip)
    def final(d,plan,clip,decision):
        if len(calls)==1:raise ValueError('protected binding differs from the saved request')
        return valid(d,plan,clip,decision)
    monkeypatch.setattr(agent,'propose',propose);monkeypatch.setattr(agent,'final_proposal',final)
    monkeypatch.setattr(agent,'check_request',lambda *a:{'consistent':True,'problems':[]})
    assert managed.prepare(directory,['a'])['changed']==['a']
    assert len(calls)==2 and calls[1][0]['stage']=='final_proposal'
    assert calls[1][0]['reason']=='protected binding differs from the saved request'


def test_explicit_stop_never_requests_another_candidate(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    monkeypatch.setattr(agent,'propose',lambda *a,**k:calls.append(1) or
                        {'action':'stop','reason':'source needs a new scene','evidence':['input/primary.json']})
    result=managed.prepare(directory,['a'])
    assert calls==[1] and result['skip_render'] and 'agent stopped' in result['blocked']['a']
    assert read_json(directory/'clip_plan.json')['clips']==clips
    assert audit(directory)['candidates'][0]['status']=='stopped'


@pytest.mark.parametrize('changed_at',['propose','check_request','final_cut'])
def test_source_change_stops_before_retry_or_adoption(tmp_path,monkeypatch,changed_at):
    directory,clips,_=configured(tmp_path);calls=[]
    def change():atomic_write_json(directory/'chapter_script.json',{'shots':[],'other_session':True})
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(1)
        if changed_at=='propose':change()
        return candidate(clip)
    def check(*args):
        if changed_at=='check_request':change()
        if changed_at=='final_cut':
            review=read_json(directory/'episode_review.json');review['clips']['a']['final_cut']={'movie':'a newer final movie'}
            atomic_write_json(directory/'episode_review.json',review)
        return {'consistent':False,'problems':['帽子仍错']}
    monkeypatch.setattr(agent,'propose',propose);monkeypatch.setattr(agent,'check_request',check)
    result=managed.prepare(directory,['a'])
    assert calls==[1] and 'inputs changed' in result['blocked']['a']
    if changed_at=='final_cut':
        assert read_json(directory/'episode_review.json')['clips']['a']['final_cut']['movie']=='a newer final movie'
    else:
        assert read_json(directory/'chapter_script.json')['other_session'] is True
    assert read_json(directory/'clip_plan.json')['clips']==clips and not history.load(directory)['trials']


@pytest.mark.parametrize('failed_at',['propose','check_request','scope_recheck'])
def test_service_failure_is_not_a_candidate_rejection_retry(tmp_path,monkeypatch,failed_at):
    directory,clips,_=configured(tmp_path);calls=[]
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(1)
        if failed_at=='propose':raise ValueError('repair agent did not produce a current decision')
        return candidate(clip)
    def check(*args):
        if failed_at=='check_request':raise RuntimeError('request checker service 502')
        return {'consistent':False,'problems':['unverified first-pass hood claim'],
                'scope_check':{'error':'ReadTimeout'}}
    monkeypatch.setattr(agent,'propose',propose);monkeypatch.setattr(agent,'check_request',check)
    result=managed.prepare(directory,['a'])
    assert calls==[1] and result['skip_render'] and result['blocked']['a']
    assert read_json(directory/'clip_plan.json')['clips']==clips and not history.load(directory)['trials']
    assert audit(directory)['candidates'][0]['status']=='stopped'


def test_sdk_service_failure_retries_only_on_next_explicit_resume_without_new_video_budget(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(1)
        if len(calls)==1:raise agent.AgentUnavailable('repair agent unavailable: HTTP 402')
        return candidate(clip)
    monkeypatch.setattr(agent,'propose',propose)
    monkeypatch.setattr(agent,'check_request',lambda *a:{'consistent':True,'problems':[]})
    first=managed.prepare(directory,['a'])
    assert calls==[1] and first['skip_render']
    assert read_json(directory/'repair_routing.json')['a']['retryable_service_failure'] is True
    assert 'a' in managed.candidates(directory)[0]
    second=managed.prepare(directory,['a'])
    assert calls==[1,1] and second['changed']==['a']
    assert not any(managed.generated_counts(history.load(directory)).values())


def test_budget_exhaustion_between_rejected_candidates_does_not_call_agent_again(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    monkeypatch.setattr(agent,'propose',lambda *a,**k:calls.append(1) or candidate(a[1],wrong=True))
    def check(*args):
        record=history.load(directory);record['prior_generation_counts']={'clips':{'a':3}};history.save(directory,record)
        return {'consistent':False,'problems':['帽子仍错']}
    monkeypatch.setattr(agent,'check_request',check)
    result=managed.prepare(directory,['a'])
    assert calls==[1] and 'budget' in result['blocked']['a'] and result['skip_render']
    assert read_json(directory/'clip_plan.json')['clips']==clips and not history.load(directory)['trials']


def test_old_blocked_policy_is_reconsidered_without_resetting_history(tmp_path,monkeypatch):
    directory,clips,review=configured(tmp_path);calls=[]
    state=managed.input_state(directory,clips[0],review['a'],managed.current_takes(directory,{'clips':clips},{'clips':review}))
    state['repair_evidence_policy']='same-take-frames-v2-primary-vision'
    old={'status':'blocked','reason':'old single-candidate hood rejection','inputs':state}
    atomic_write_json(directory/'repair_routing.json',{'a':old})
    monkeypatch.setattr(agent,'propose',lambda *a,**k:calls.append(1) or candidate(a[1]))
    monkeypatch.setattr(agent,'check_request',lambda *a:{'consistent':True,'problems':[]})
    assert managed.prepare(directory,['a'])['changed']==['a'] and calls==[1]
    assert audit(directory)['previous_decision']==old
    assert not any(managed.generated_counts(history.load(directory)).values())


def test_retaking_inconsistent_input_returns_feedback_and_edits_before_any_video(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    def propose(d,clip,verdict,*,rejections=None):
        calls.append(copy.deepcopy(rejections or []))
        return {'action':'retake','reason':'input seems correct','evidence':['input/prompt.txt']} if len(calls)==1 else candidate(clip)
    monkeypatch.setattr(agent,'propose',propose)
    monkeypatch.setattr(agent,'check_request',lambda c,*a:{
        'consistent':'hood rests behind her neck' in c['prompt_h3'],
        'problems':[] if 'hood rests behind her neck' in c['prompt_h3'] else ['original request clothing conflict']})
    result=managed.prepare(directory,['a'])
    assert result['changed']==['a'] and len(calls)==2
    assert calls[1][0]['candidate']['action']=='retake'
    trials=history.load(directory)['trials']
    assert len(trials)==1 and trials[0]['method']=='agent_prompt'
    assert not any(managed.generated_counts(history.load(directory)).values())


def test_final_movie_frames_are_copied_with_global_times_and_related_boundary(tmp_path,monkeypatch):
    directory,clips,review=configured(tmp_path);original=copy.deepcopy(review['a']);config={'runs_root':str(tmp_path/'runs')}
    paths=[]
    for name in ['a-final.jpg','b-boundary.jpg','unrelated.jpg']:
        path=tmp_path/name;path.write_bytes(name.encode());paths.append(str(path))
    finding={'clip_ids':['a','b'],'problem':'platform jumps','evidence':'two actual boundary frames',
             'frame_paths':paths[:2],'source_basis':'same platform and dry shoes'}
    review['a']['final_cut']={'movie':str(tmp_path/'reviewed.mp4'),
        'issues':[finding,{'clip_ids':['c'],'frame_paths':[paths[2]],'problem':'not this clip'}],
        'frames':[{'path':paths[0],'seconds':130.2,'clip_id':'a','shot_index':17},
                  {'path':paths[1],'seconds':139.7,'clip_id':'b','shot_index':18},
                  {'path':paths[2],'seconds':155.0,'clip_id':'c','shot_index':20}]}
    saved=copy.deepcopy(review['a'])
    def frames(d,cid,video,target):
        target.mkdir(parents=True);path=target/'native.jpg';path.write_bytes(b'native');return [path],'fixture'
    monkeypatch.setattr(agent,'provided_frames',frames)
    def run(name,**kwargs):
        work=Path(config['runs_root'])/name
        primary=read_json(work/'input/primary.json');evidence=read_json(work/'input/evidence.json')
        final=primary['final_cut']
        assert final==evidence['final_cut']==primary['verdict']['final_cut']==evidence['verdict']['final_cut']
        assert len(final['issues'])==1 and len(final['frames'])==2
        assert [f['seconds'] for f in final['frames']]==[130.2,139.7]
        assert [f['clip_id'] for f in final['frames']]==['a','b']
        assert final['source_movie']=='reviewed.mp4' and 'global' in final['seconds_timebase']
        for row,source in zip(final['frames'],paths):
            assert row['path'].startswith('input/final-cut/') and not Path(row['path']).is_absolute()
            assert (work/row['path']).read_bytes()==Path(source).read_bytes()
        assert final['issues'][0]['frame_paths']==[f['path'] for f in final['frames']]
        assert 'Native footage cannot substitute for final-movie continuity' in (work/'prompt.txt').read_text()
        atomic_write_json(work/'output/decision.json',{'action':'stop','reason':'source continuity needs review',
                                                     'evidence':[final['frames'][0]['path']]})
        return SimpleNamespace(ok=True,produced=['decision.json'],attempt='1')
    monkeypatch.setattr(agent,'run_agent',run)
    assert agent.propose(directory,clips[0],review['a'],config=config)['action']=='stop'
    assert review['a']==saved and original.get('final_cut') is None


def test_missing_required_final_movie_frames_stop_without_blind_agent_call(tmp_path,monkeypatch):
    directory,clips,review=configured(tmp_path);missing=str(tmp_path/'missing-final.jpg')
    review['a']['final_cut']={'movie':'final.mp4','issues':[{'clip_ids':['a'],'frame_paths':[missing],
        'problem':'reported jump','source_basis':'same position'}],
        'frames':[{'path':missing,'seconds':120.0,'clip_id':'a','shot_index':17}]}
    monkeypatch.setattr(agent,'run_agent',lambda *a,**k:pytest.fail('No final evidence to verify'))
    with pytest.raises(ValueError,match='required movie frames unavailable'):
        agent.propose(directory,clips[0],review['a'],config={'runs_root':str(tmp_path/'runs')})


def test_new_final_cut_evidence_reopens_old_blocked_with_native_take_unchanged(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);calls=[]
    monkeypatch.setattr(agent,'propose',lambda *a,**k:calls.append(1) or
                        {'action':'stop','reason':'more evidence needed','evidence':['input/primary.json']})
    assert managed.prepare(directory,['a'])['skip_render']
    review=read_json(directory/'episode_review.json')
    native=(review['clips']['a']['video'],review['clips']['a']['take'])
    review['clips']['a']['final_cut']={'movie':'reviewed.mp4','issues':[{'clip_ids':['a'],'problem':'new boundary finding'}]}
    atomic_write_json(directory/'episode_review.json',review)
    assert managed.prepare(directory,['a'])['skip_render'] and calls==[1,1]
    current=read_json(directory/'episode_review.json')['clips']['a']
    assert (current['video'],current['take'])==native
    assert read_json(directory/'clip_plan.json')['clips']==clips


def test_changed_runtime_reopens_blocked_input_without_resetting_take(tmp_path,monkeypatch):
    directory,clips,_=configured(tmp_path);runtime={'model':'deepseek-v4.1-flash','effort':'medium'}
    monkeypatch.setattr(agent,'load_config',lambda:copy.deepcopy(runtime))
    review=read_json(directory/'episode_review.json');takes=managed.current_takes(directory,{'clips':clips},review)
    state=managed.input_state(directory,clips[0],review['clips']['a'],takes)
    atomic_write_json(directory/'repair_routing.json',{'a':{'status':'blocked','reason':'prior timeout','inputs':state}})
    assert managed.candidates(directory,review)[1]['a']=='prior timeout'
    runtime['repair_extra_body']={'thinking':{'type':'disabled'},'output_config':{'effort':'low'}}
    assert 'a' in managed.candidates(directory,review)[0]
    assert managed.current_takes(directory,{'clips':clips},review)==takes


def test_protocol_failure_continues_same_conversation_within_original_window(tmp_path,monkeypatch):
    directory,clips,review=configured(tmp_path);config={'runs_root':str(tmp_path/'runs')};calls=[]
    monkeypatch.setattr(agent,'load_config',lambda:config)
    monkeypatch.setattr(agent,'provided_frames',lambda *a: ([], 'fixture unavailable'))
    # Evidence remains available through a real fixture frame.
    def frames(d,cid,video,target):
        target.mkdir(parents=True,exist_ok=True);p=target/'frame.jpg';p.write_bytes(b'frame');return [p],'fixture'
    monkeypatch.setattr(agent,'provided_frames',frames)
    ticks=iter([100,400]);monkeypatch.setattr(agent.time,'monotonic',lambda:next(ticks))
    def run(name,**kwargs):
        calls.append(kwargs);work=Path(config['runs_root'])/name;out=work/'output';out.mkdir(exist_ok=True)
        attempt=work/'attempts'/str(len(calls));attempt.mkdir(parents=True)
        if len(calls)==1:
            atomic_write_json(out/'decision.json',{'action':'edit','reason':'fix hoodie','evidence':['input/prompt.txt']})
            (attempt/'session.jsonl').write_text(json.dumps({'type':'result','terminal_reason':'malformed_tool_use_exhausted'}))
            return SimpleNamespace(ok=False,produced=['decision.json'],directory=attempt)
        (out/'prompt.txt').write_text(candidate(clips[0])['prompt'])
        return SimpleNamespace(ok=True,produced=['prompt.txt'],directory=attempt)
    monkeypatch.setattr(agent,'run_agent',run)
    result=agent.propose(directory,clips[0],review['a'])
    assert result['action']=='edit' and len(calls)==2
    assert calls[0]['timeout']==600 and calls[1]['timeout']==300
    assert '--continue' in calls[1]['config']['extra_args']
    assert 'saved conversation' in calls[1]['prompt'].read_text()


def test_other_service_failure_does_not_start_protocol_continuation(tmp_path,monkeypatch):
    directory,clips,review=configured(tmp_path);config={'runs_root':str(tmp_path/'runs')};calls=[]
    monkeypatch.setattr(agent,'load_config',lambda:config)
    def frames(d,cid,video,target):
        target.mkdir(parents=True,exist_ok=True);p=target/'frame.jpg';p.write_bytes(b'frame');return [p],'fixture'
    monkeypatch.setattr(agent,'provided_frames',frames)
    def run(name,**kwargs):
        calls.append(kwargs);work=Path(config['runs_root'])/name;attempt=work/'attempts/1';attempt.mkdir(parents=True)
        (attempt/'session.jsonl').write_text(json.dumps({'type':'result','terminal_reason':'api_error'}))
        return SimpleNamespace(ok=False,produced=[],directory=attempt)
    monkeypatch.setattr(agent,'run_agent',run)
    with pytest.raises(ValueError,match='did not produce'):agent.propose(directory,clips[0],review['a'])
    assert len(calls)==1
