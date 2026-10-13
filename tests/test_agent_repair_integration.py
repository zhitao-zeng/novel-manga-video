from types import SimpleNamespace
from novel_manga.application.agents.episode import recovery
from novel_manga.application.agents.episode.memory_summary import summarize_case
from novel_manga.util import atomic_write_json, read_json


def context(tmp_path):
    ctx = SimpleNamespace(run_dir=tmp_path/'run', work_dir=tmp_path/'run/sandbox',
        episode_dir=tmp_path/'run/book/book_2', source_novel_dir=tmp_path/'book',
        source_episode_dir=tmp_path/'book/book_2')
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'memory_scope':'repair_only','agent_phase':'input_repair'})
    return ctx


def test_early_repair_survives_later_checks_with_its_actual_decision(tmp_path):
    ctx=context(tmp_path)
    diagnosis={'issues':[{'kind':'input','clips':['clip_01'],'detail':'Wrong source speaker'}]}
    decision={'clip_id':'clip_01','action':'input','reason':'Restore source speaker',
              'source_quote':'The captain speaks.', 'shots':[{'index':1,'speaker_map':{'Soldier':'Captain'}}]}
    recovery.remember(ctx,diagnosis,{'action':'apply','ok':True,'input_repaired':['clip_01']},[decision])
    for i in range(10):
        recovery.remember(ctx,diagnosis,{'action':'check','ok':False,'error':f'Check incomplete {i}'})
    record=read_json(ctx.source_novel_dir/'.production-memory/run.json')
    summary=summarize_case(record)
    assert summary['effective_operations'][0]['receipt']=='changed'
    assert record['attempts'][0]['remedies'][0]['decision']==decision
    assert record['outcome']=='unresolved'


def test_recall_prefers_concrete_repairs_and_excludes_the_current_run(tmp_path):
    ctx=context(tmp_path);folder=ctx.source_novel_dir/'.production-memory'
    issue={'kind':'input','clips':['clip_01'],'detail':'Wrong reference'}
    for name,outcome,attempts in [('run','accepted',[]),('empty','accepted',[]),
        ('concrete','unresolved',[{'action':'apply','changed':['clip_01'],'remedies':[{'clip':'clip_01','action':'input','reason':'Bind source-backed phase'}]}])]:
        atomic_write_json(folder/(name+'.json'),{'episode':name,'scope':'semantic_input_repair','outcome':outcome,'issues':[issue],'attempts':attempts})
    cases=recovery.recall(ctx,{'issues':[issue]})
    assert cases[0]['episode']=='concrete'
    assert 'run' not in [r['episode'] for r in cases]


def test_explicit_context_restart_wins_over_old_idle_recovery(tmp_path):
    ctx=context(tmp_path);config={'model':'glm','context_restart':'operator-new-window'}
    saved={'protocol_recovery':{'kind':'idle_turns','at':1},'remaining_seconds':80,'fallback_invocations':2}
    selected,result=recovery.protocol_fallback(ctx,config,saved)
    assert selected['context_restart']=='operator-new-window'
    assert result==saved


def test_historical_fallback_does_not_choose_the_current_model():
    config={'model':'glm','refusal_fallback':{'model':'luna','base_url':'http://gateway','key_var':'KEY'}}
    marker={'from_model':'old-primary','to_model':'old-fallback','reason':'Historical idle loop'}
    saved={'model_switch':marker,'deadline':100,'attempts':['old'],'fallback_invocations':2}
    selected,result=recovery.restore_model_route(config,saved)
    assert selected['model']=='glm'
    assert result['model_switch_history']==[marker]
    assert result['deadline']==100 and result['attempts']==['old'] and result['fallback_invocations']==2


def test_summary_does_not_attach_an_unrelated_clip_remedy():
    record={'episode':'other','issues':[{'kind':'speech','clips':['clip_02']}],
        'attempts':[{'changed':['clip_01','clip_02'],'remedies':[
            {'clip':'clip_01','action':'input','reason':'Change costume'},
            {'clip':'clip_02','action':'edit','reason':'Shorten overfull speech'}]}]}
    assert [r['clip'] for r in summarize_case(record)['effective_operations']]==['clip_02']


def test_compact_current_task_retains_access_to_actual_prior_decisions(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare,prompt_view
    ctx=context(tmp_path)
    decision={'action':'input','clip_id':'clip_01','shots':[{'index':1,'in_frame':['Source actor']}]}
    case={'episode':'previous','issues':[{'kind':'input','clips':['clip_01']}],
        'attempts':[{'changed':['clip_01'],'remedies':[{'action':'input','clip':'clip_01','decision':decision}]}]}
    brief=prepare(ctx,{'adopted':True},{'issues':[]},[case])
    view=prompt_view(brief,continuing=True)
    assert 'related_history' not in view and 'execution_rules' not in view
    record=read_json(ctx.work_dir/view['related_history_files'][0])
    assert record['attempts'][0]['remedies'][0]['decision']==decision


def test_unfinished_model_call_is_not_a_source_defect(tmp_path):
    ctx=context(tmp_path)
    status={'adopted':True,'input_repairs':{'clip_01':{'error':'LLMFailure: Output truncated'}},
            'job':{'result':{'error':'ValueError: verification requires an explicit valid verdict'}}}
    issues=recovery.diagnose(ctx,status)['issues']
    assert len(issues)==2 and all(i['kind']=='execution' for i in issues)


def test_real_source_contradiction_remains_a_content_issue(tmp_path):
    ctx=context(tmp_path)
    issues=recovery.diagnose(ctx,{'adopted':True,'input_repairs':{
        'clip_01':{'error':'UnsupportedInput: speaker disagrees with the source'}}})['issues']
    assert issues[0]['kind']=='input'
