import novel_manga.story.source_identity as source_identity_rules
import novel_manga.application.repair.judges as repair_judges
import repair_manager_dispatch_thin as repair_manager_dispatch
import repair_manager_workers_thin as repair_manager_workers

from render_context_support import uninitialized_runner
import copy
import hashlib
import json
from pathlib import Path

import pytest
import novel_manga.application.repair.managed as managed
import novel_manga.application.repair.history as history
import novel_manga.application.repair.flow as repair
import novel_manga.repair.scheduling as schedule_rules
import repair_manager_flow_thin as repair_manager_flow
from novel_manga.story.h3 import request_issues
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.review.storage import take_identity


def fixture_episode(tmp_path):
    d=tmp_path/'book'/'book_1';d.mkdir(parents=True)
    clips=[];reviews={};media=[]
    for cid in ['a','b']:
        video=d/f'{cid}.mp4';video.write_bytes(cid.encode())
        clips.append({'clip_id':cid,'kind':'video','prompt':'original','request_seconds':5,'references':[]})
        reviews[cid]={'video':str(video),'take':take_identity(video),'story_ok':False,'tier':'must_fix',
                      'verify':{'verdict':'obvious','same_person_twice':True,'evidence':'two identical people'},'feedback':'distinct people'}
        media.append({'clip_id':cid,'selected':{'video':str(video),'passed':True}})
    for filename,data in [('clip_plan.json',{'clips':clips}),('chapter_script.json',{'shots':[]}),('segments.json',[]),
                          ('review_feedback.json',{}),('episode_review.json',{'clips':reviews,'feedback':{'a':'wrong','b':'wrong'}}),
                          ('thin_media_report.json',{'clips':media})]:
        (d/filename).write_text(json.dumps(data))
    return d,clips,reviews


def test_descriptive_source_names_are_scene_local_and_ambiguous_roles_are_not_bound(monkeypatch):
    import novel_manga.planning.cast as pc_cast
    monkeypatch.setattr(pc_cast,'_usable_forms',lambda names:{n:[n] for n in names})
    names=['黑袍身影','拿玩具木刀的男孩']
    bible={'characters':[{'name':n} for n in names]}
    context={'entities':{'e1':names[0],'e2':names[1]},'mentions':[
        {'form':'黑袍人','entity_id':'e1','presence':'on_stage'},
        {'form':'男孩','entity_id':'e2','presence':'on_stage'}]}
    identities=source_identity_rules.identity_rows(names,bible,'黑袍人点头，男孩问故事名字。',context=context)
    assert identities[0]['source_names']==['黑袍人']
    assert identities[1]['source_names']==['男孩']
    names.append('抱着玩具熊的男孩');bible['characters'].append({'name':names[-1]})
    identities=source_identity_rules.identity_rows(names,bible,'男孩说话。')
    assert not any(row['source_names'] for row in identities)


def test_literal_quote_cannot_assign_latter_speech_to_the_first_person(monkeypatch):
    quote='莱恩将视线投向神明，后者拍拍手：“不错的故事。”'
    row={'stage':1,'turn':1,'speaker':'莱恩','source_quote':quote,'source_speaker_phrase':'后者','relation':'verbatim','adapted_text':'不错的故事。'}
    shots=[{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'莱恩','text':'不错的故事。'}]}]
    identities=[{'name':'莱恩','source_names':['莱恩']},{'name':'神明','source_names':['神明','后者']}]
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:{'speakers':[row]})
    assert not repair_judges.speaker_contract(quote,shots,['莱恩','神明'],identities,[row])
    row={**row,'speaker':'神明'}
    assert repair_judges.speaker_contract(quote,shots,['莱恩','神明'],identities)=={(1,1):'神明'}


@pytest.mark.parametrize('text', ['two <Subject 2> stand','Two identical <Subject 2> stand','2 instances of <Subject 2>'])
def test_one_reference_identity_cannot_represent_multiple_people(text):
    assert request_issues({'prompt_h3':'detailed_description:\n'+text})


def test_two_distinct_people_and_repeated_mentions_are_allowed():
    assert not request_issues({'prompt_h3':'detailed_description:\n<Subject 1> faces <Subject 2>. <Subject 1> sits. Two unnamed waiters leave.'})
    assert not request_issues({'prompt_h3':'detailed_description:\nBoth <Subject 1> and <Subject 2> sit.'})


def test_plural_role_uses_wardrobe_without_copying_one_identity():
    from novel_manga.story.h3 import source_crowds
    from novel_manga.story.h3 import subject_lines,compose
    from novel_manga.application.profiles import h3_source_digest, h3_prompt_outdated
    clip={'clip_id':'c','references':[{'role':'character','name':'警员'},{'role':'character','name':'侍者'}],
          'lines':[],'prompt':'original','request_seconds':5}
    bible={'characters':[{'name':'侍者','role':'旅店侍者'}]}
    crowds=source_crowds(clip,bible,'一名警员呼喊两位男侍者的名字。')
    assert crowds['侍者']['count']==2
    clip['prompt_h3']='old';clip['prompt_h3_of']=h3_source_digest('original')
    assert not h3_prompt_outdated(clip)
    clip['crowd_roles']=crowds
    assert h3_prompt_outdated(clip)
    defs,subjects=subject_lines(clip)
    assert subjects=={'警员':1} and 'clothing only for 2 distinct' in defs[1]
    prompt=compose(clip,['The officer calls two unnamed waiters with different faces.'],[('stage',[])])
    assert not request_issues({'prompt_h3':prompt})
    assert request_issues({'prompt_h3':prompt.replace('two unnamed waiters','<Subject 2>')})
    clip['lines']=[{'speaker_name':'侍者','text':'谁？'}]
    assert not source_crowds(clip,bible,'两位男侍者说话。')


def test_old_plan_fingerprint_is_unchanged_until_an_explicit_new_take_is_requested():
    plan={'clips':[{'clip_id':'a','kind':'video','prompt':'p','request_seconds':5}]}
    material=[('a','video','p',[],5,'',[])]
    old=hashlib.sha256(json.dumps(material,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    assert plan_fingerprint(plan)==old
    plan['clips'][0]['repair_take']=1
    assert plan_fingerprint(plan)!=old


def test_new_clip_error_has_budget_even_if_another_clip_has_exhausted_its_takes(tmp_path):
    d,clips,reviews=fixture_episode(tmp_path)
    takes=[{'video':'old','take':[i,1,2]} for i in range(3)]
    history.save(d,{'trials':[{'managed':True,'renders':[{'clips':{'a':{'generated_takes':takes}}}]}],'observations':{}})
    wanted,blocked=managed.candidates(d)
    assert wanted==['b'] and 'a' in blocked


def test_cached_takes_and_duplicate_report_reads_do_not_spend_retry_budget():
    row={'generated_takes':[{'video':'v','take':[1,2,3]}]}
    record={'trials':[{'managed':True,'renders':[{'clips':{'a':row}},{'clips':{'a':row,'b':{'generated_takes':[]}}}]}]}
    assert managed.generated_counts(record)=={'a':1}


def test_legacy_episode_counts_do_not_block_new_clip_work(tmp_path):
    m=repair_manager_flow.Manager(tmp_path/'book',tmp_path/'legacy');m.state['phase']=2;m.state['passes']['1']=2
    m.state['recovery_attempts']['1']={'residual':1}
    m.info[1]={'status':'done','ready':True,'bad':1,'held':False,'can_fill':False,'flash_pending':0,'unverified':0,'managed_clips':['b']}
    repair_manager_dispatch.schedule(m)
    assert len(m.state['jobs'])==1 and m.state['jobs'][0]['episodes']==[1]


def test_unresolved_clip_preparation_does_not_keep_first_pass_running_forever():
    state={'jobs':[],'passes':{}}
    info={1:{'bad':1,'unverified':0,'can_fill':False,'managed_clips':[]}}
    assert schedule_rules.second_pass_ready(state,info)


def test_unchanged_failed_preparation_waits_for_corrected_inputs(tmp_path):
    d,clips,reviews=fixture_episode(tmp_path)
    takes=managed.current_takes(d,{'clips':clips},{'clips':reviews})
    state={'a':{'status':'blocked','reason':'missing source','inputs':managed.input_state(d,clips[0],reviews['a'],takes)}}
    (d/'repair_routing.json').write_text(json.dumps(state))
    assert managed.candidates(d)[0]==['b']
    clips[0]['prompt']='corrected source picture'
    (d/'clip_plan.json').write_text(json.dumps({'clips':clips}))
    assert managed.candidates(d)[0]==['a','b']


def test_all_repair_preparation_stages_use_the_integrated_entry(tmp_path):
    m=repair_manager_flow.Manager(tmp_path/'book',tmp_path/'legacy')
    for step in [1,4,7]:
        cmd,_=repair_manager_workers.command(m, {'kind':'repair','episodes':[1],'step':step,'id':'job-test'})
        assert 'scripts/prepare_recovery_thin.py' in cmd and cmd[cmd.index('--kind')+1]=='managed'
        assert cmd[cmd.index('--job-id')+1]==f'job-test-step{step}'


def test_failed_source_preparation_does_not_prevent_another_clip_from_being_prepared(tmp_path,monkeypatch):
    d,clips,reviews=fixture_episode(tmp_path)
    import novel_manga.application.repair.diagnosis as diagnose
    import novel_manga.application.repair.source_recheck as source
    import novel_manga.application.packing.blocked as blocked
    monkeypatch.setattr(blocked,'repair_episode',lambda *a,**k:{'changed':[]})
    monkeypatch.setattr(diagnose,'clip_context',lambda *a:{})
    monkeypatch.setattr(diagnose,'diagnose_numbered',lambda *a:{'cause':'script_mismatch'})
    def prepare(directory,targets,**kw):
        cid=targets[0]
        if cid=='a':raise ValueError('source missing')
        plan=managed.read(d/'clip_plan.json',{})
        plan['clips'][1]['prompt']='corrected'
        history.begin_trial(d,{'b'},'source_recheck',after_plan=plan)
        (d/'clip_plan.json').write_text(json.dumps(plan))
        return {'changed':['b'],'accepted':[]}
    monkeypatch.setattr(source,'prepare_source_recheck',prepare)
    result=managed.prepare(d)
    assert result['changed']==['b'] and 'a' in result['blocked']
    assert history.load(d)['trials'][0]['managed']


def test_explicit_generation_retry_invalidates_old_cache_without_spoken_instruction(tmp_path):
    import novel_manga.application.rendering.flow as render
    r=uninitialized_runner()
    from types import SimpleNamespace
    r.context.settings=SimpleNamespace(local_h3_base_url='pool');r.context.novel_dir=tmp_path;r.context.feedback={}
    clip={'clip_id':'a','request_seconds':5,'repair_take':1,'prompt_h3':'same instruction'}
    saved={'duration':5,'prompt':'same instruction','references':[],'reference_sha256':[]}
    assert not r.request_matches(clip,saved,[],[])
    assert r.request_matches(clip,{**saved,'repair_take':1},[],[])


def test_contradictory_request_is_blocked_before_acquiring_a_generation_slot(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import novel_manga.application.rendering.flow as render
    r=uninitialized_runner()
    r.context.work=tmp_path/'work';r.context.novel_dir=tmp_path;r.context.cache_only=False;r.context.feedback={};r.context.prescreen=False
    r.context.settings=SimpleNamespace(local_h3_base_url='pool')
    r.clip_prompt=lambda clip:clip['prompt_h3']
    clip={'clip_id':'c','request_seconds':5,'references':[],
          'prompt_h3':'detailed_description:\nTwo <Subject 2> stand up.'}
    monkeypatch.setattr(render,'acquire_inflight_slot',lambda *a:pytest.fail('must not take a generation slot'))
    with pytest.raises(ValueError,match='identity repair'):
        r.generate_clip(clip,1)
    assert r.context._blocked_clips['c'][0].startswith('request:')


def test_one_render_is_recorded_for_every_prepared_clip_and_counted_once(tmp_path):
    d,clips,reviews=fixture_episode(tmp_path)
    plan={'clips':clips}
    history.begin_trial(d,{'a'},'source_recheck',after_plan=plan)
    history.begin_trial(d,{'b'},'source_recheck',after_plan=plan)
    record=history.load(d)
    for trial in record['trials']:trial['managed']=True
    history.save(d,record)
    media=managed.read(d/'thin_media_report.json',{})
    media.update(clip_plan_fingerprint=plan_fingerprint(plan),review_feedback={})
    for clip in media['clips']:clip['selected']['generated_this_run']=True
    history.record_render(d,media);history.record_render(d,media)
    assert managed.generated_counts(history.load(d))=={'a':1,'b':1}


def test_reframe_recut_translates_and_tracks_every_replacement(tmp_path, monkeypatch):
    import novel_manga.application.rendering.h3 as build_h3_prompts
    import novel_manga.application.repair.diagnosis as diagnose_clip_repair
    import novel_manga.application.packing.blocked as repair_blocked_plan
    d, clips, reviews = fixture_episode(tmp_path)
    monkeypatch.setattr(repair_blocked_plan, 'repair_episode', lambda *a, **k: {'changed': []})
    monkeypatch.setattr(diagnose_clip_repair, 'clip_context', lambda *a: {})
    monkeypatch.setattr(diagnose_clip_repair, 'diagnose_numbered', lambda *a: {'cause': 'generation_mismatch'})
    monkeypatch.setattr(history, 'repeated_errors', lambda *a: ['same_person_twice'])
    updated = {'clips': [dict(clips[0], prompt='first location'),
                         dict(clips[0], clip_id='c', prompt='second location'), clips[1]]}
    monkeypatch.setattr(repair, 'repair_episode', lambda *a, **k: {'changed': ['a', 'c'], 'proposal': {
        'plan': updated, 'script': {'shots': []}, 'notes': {}, 'changes': {},
        'structural_repair': {'groups': [{'old': ['a'], 'new': ['a', 'c']}]}}})
    translated = []
    def convert(entry, **kwargs):
        from novel_manga.application.profiles import h3_source_digest
        translated.append(entry['clip_id'])
        entry.update(prompt_h3='safe request', prompt_h3_of=h3_source_digest(entry['prompt']))
    monkeypatch.setattr(build_h3_prompts, 'convert', convert)
    result = managed.prepare(d, ['a'])
    assert result['changed'] == ['a', 'c'] and not result['blocked']
    assert translated == ['a', 'c']
    assert history.load(d)['trials'][0]['clips'] == ['a', 'c']
