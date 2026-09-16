import repair_manager_dispatch_thin as repair_manager_dispatch
import repair_manager_state_thin as repair_manager_state
import repair_manager_workers_thin as repair_manager_workers
import json

from thin_profile import speech_gate_result, blocking_clip_failures
from review_evidence_thin import source_contract_block
from novel_manga.story.source_identity import identity_rows as source_identities


def test_speech_observation_keeps_other_gates_and_can_be_reenabled(tmp_path):
    raw={'passed':False,'issues':['missing_1.0_over_0.5','excess_unplanned_speech']}
    assert speech_gate_result(tmp_path,raw)==raw
    (tmp_path/'profile.json').write_text(json.dumps({'speech_gate':'observe'}))
    observed=speech_gate_result(tmp_path,raw)
    assert observed['passed'] and observed['speech_issues']==raw['issues']
    assert raw['passed'] is False
    assert not speech_gate_result(tmp_path,{**raw,'issues':[*raw['issues'],'black_frames']})['passed']
    assert not speech_gate_result(tmp_path,{'passed':False,'issues':[]})['passed']
    (tmp_path/'profile.json').write_text('{}')
    assert not speech_gate_result(tmp_path,observed)['passed']


def test_old_reports_and_observed_new_reports_have_consistent_blocking(tmp_path):
    report={'gate_failed_clips':['a','b'],'clips':[
        {'clip_id':'a','selected':{'passed':False,'issues':['missing_1.0_over_0.5']}},
        {'clip_id':'b','selected':{'passed':False,'issues':['black_frames']}}]}
    (tmp_path/'profile.json').write_text(json.dumps({'speech_gate':'observe'}))
    assert blocking_clip_failures(tmp_path,report)==['b']
    report={'clips':[{'clip_id':'a','selected':speech_gate_result(tmp_path,report['clips'][0]['selected'])}]}
    assert blocking_clip_failures(tmp_path,report)==[]
    (tmp_path/'profile.json').write_text('{}')
    assert blocking_clip_failures(tmp_path,report)==['a']


def test_current_source_binding_reaches_review_but_stale_or_wrong_binding_does_not(tmp_path):
    quote='伯爵说：“我不甘心。”'
    (tmp_path/'segments.json').write_text(json.dumps([{'segment_id':'s','text':quote}]))
    fact={'stage':1,'turn':1,'speaker':'伯爵','adapted_text':'我不甘心。','source_quote':quote,'relation':'verbatim'}
    (tmp_path/'source_speaker_contract.json').write_text(json.dumps([fact]))
    clip={'shot_indexes':[1],'lines':[{'speaker_name':'伯爵','text':'我不甘心。'}]}
    assert '伯爵' in source_contract_block(clip,tmp_path)
    assert not source_contract_block({**clip,'lines':[{'speaker_name':'莱恩','text':'我不甘心。'}]},tmp_path)
    (tmp_path/'segments.json').write_text(json.dumps([{'segment_id':'s','text':'另一段原文'}]))
    assert not source_contract_block(clip,tmp_path)


def test_collective_offscreen_voice_and_named_duchess_can_be_grounded(monkeypatch):
    import novel_manga.planning.cast as pc_cast
    monkeypatch.setattr(pc_cast,'_usable_forms',lambda names:{n:[n] for n in names})
    names=['无名群声','西米尔公爵夫人']
    bible={'characters':[{'name':'西米尔公爵夫人','gender':'女'}]}
    context={'entities':{'e1':'无名群声','e2':'西米尔公爵夫人'},'mentions':[
        {'form':'姑娘们','entity_id':'e1','presence':'voice'},
        {'form':'西米尔公爵的夫人','entity_id':'e2','presence':'on_stage'}]}
    rows=source_identities(names,bible,'姑娘们都在倒数。西米尔公爵的夫人点了点头。',context=context)
    assert {r['name'] for r in rows if r['source_names']}==set(names)


def test_current_full_delivery_finishes_despite_an_old_failed_scan(tmp_path,monkeypatch):
    import time
    import repair_manager_flow_thin as repair_manager_flow
    m=repair_manager_flow.Manager(tmp_path/'book',tmp_path/'legacy')
    m.state.update(phase=2,scan_started=True,jobs=[{'kind':'scan','status':'needs_attention','episodes':[]}])
    m.last_delivery=time.monotonic()
    def refresh(manager):
        m.info={1:{}}
        m.state['summary']={'deliverable_precise':1}
        m.last_refresh=time.monotonic()
    monkeypatch.setattr(repair_manager_state,'refresh',refresh)
    monkeypatch.setattr(repair_manager_workers,'reap',lambda manager:False)
    monkeypatch.setattr(repair_manager_dispatch,'schedule',lambda manager:None)
    monkeypatch.setattr(repair_manager_workers,'launch',lambda manager:None)
    m.run()
    assert m.state['status']=='complete'
