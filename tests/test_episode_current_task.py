from novel_manga.media.context import RenderContext, RenderOptions, RenderState
"""Current repair context excludes obsolete targets but retains full evidence files."""
from types import SimpleNamespace
from pathlib import Path

from novel_manga.util import atomic_write_json,read_json


def test_brief_contains_only_current_targets_and_exact_operation_choices(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path / 'sandbox', episode_dir=tmp_path / 'book/book_1', run_dir=tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[
        {'clip_id':'clip_02','shot_indexes':[2]}, {'clip_id':'clip_07','shot_indexes':[9,10],
         'references':[{'name':'甲','role':'character','image':'input/refs/a.jpeg'}]}]})
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'agent_phase':'input_repair'})
    status={'first_pass_remaining':['clip_07'],'repair_actions':{'clip_02':[],'clip_07':['input','accept_risk']},
        'remaining_input_attempts':{'clip_07':1},'risk_choices':{'clip_07':{'wearer_card':['甲']}},
        'input_repairs':{'clip_02':{'error':'old failure'}},
        'execution':{'blocked':{'clip_02':'old failure'}},'job':None}
    brief=prepare(ctx,status,{'issues':[]},[{'episode':'old','outcome':'unresolved','attempts':[{'blocked':'X'*5000}]}])
    assert [r['clip_id'] for r in brief['targets']]==['clip_07']
    assert brief['targets'][0]['risk_choices']=={'wearer_card':['甲']}
    assert 'old failure' not in str(brief)
    assert len(str(brief))<4500
    assert read_json(ctx.work_dir/'input/current-status.json')==status
    assert read_json(ctx.work_dir/'input/current-task.json')==brief


def test_current_task_keeps_provisional_draft_and_adoption_workflow(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path / 'sandbox', episode_dir=tmp_path / 'book/book_1', run_dir=tmp_path)
    task=prepare(ctx,{'adopted':False,'can_adopt_draft':True},
        {'issues':[{'clips':['clip_01'],'detail':'planner failed'}]},[])
    assert task['mode']=='provisional_draft'
    assert any('output/episode.json' in rule and 'adopt' in rule for rule in task['execution_rules'])
    assert not any(rule.startswith('Only these current targets') for rule in task['execution_rules'])
    assert task['issues'][0]['detail']=='planner failed'


def test_related_history_keeps_effectful_repair_and_failed_choice_before_finish(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.recovery import recall
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'run/book/book_2',
        run_dir=tmp_path/'run',source_novel_dir=tmp_path/'book')
    atomic_write_json(ctx.source_novel_dir/'.production-memory/old.json',{
        'episode':'book_1','outcome':'accepted','updated_at':1,
        'issues':[{'kind':'input','clips':['clip_01'],'detail':'base card still wears armor'}],
        'attempts':[
            {'action':'apply','ok':False,'changed':['clip_01'],'accepted_clips':[],
             'blocked':{'clip_02':'revision allowance used'},'remedies':[
                 {'action':'input','clip':'clip_01','reason':'bind the source-backed unarmored phase'},
                 {'action':'input','clip':'clip_02','reason':'repeat the rejected proposal'}]},
            {'action':'review','ok':True,'changed':[],'remedies':[]},
            {'action':'finish','ok':True,'changed':[],'remedies':[]}]})
    diagnosis={'issues':[{'kind':'input','clips':['clip_01'],'detail':'wrong costume'}]}
    memories=recall(ctx,diagnosis)
    task=prepare(ctx,{'adopted':True,'repair_actions':{'clip_01':['input']}},diagnosis,memories)
    case=task['related_history'][0]
    assert case['issues'][0]['detail']=='base card still wears armor'
    assert case['effective_operations']==[{'action':'input','clip':'clip_01',
        'reason':'bind the source-backed unarmored phase','receipt':'changed'}]
    assert case['failed_operations'][0]['error']=='revision allowance used'
    assert case['failed_operations'][0]['clip']=='clip_02'
    assert case['outcome']=='accepted'
    assert 'last_result' not in case


def test_accepted_finish_alone_is_not_a_known_repair(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    memory={'episode':'old','outcome':'accepted','issues':[{'kind':'speech','detail':'old ASR flag'}],
        'attempts':[{'action':'finish','ok':True,'changed':[],'remedies':[]}]}
    task=prepare(ctx,{'adopted':True},{'issues':[]},[memory])
    assert task['related_history'][0]['effective_operations']==[]
    assert task['related_history'][0]['repair_details']=='not_recorded'


def test_history_change_is_still_unresolved_until_formal_acceptance(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    memory={'episode':'old','outcome':'unresolved','issues':[{'kind':'speech','detail':'extra sentence'}],
        'attempts':[{'action':'apply','ok':True,'changed':['clip_01'],'remedies':[
            {'action':'edit','clip':'clip_01','reason':'replace the conflicting narration'}]}]}
    task=prepare(ctx,{'adopted':True},{'issues':[]},[memory])
    case=task['related_history'][0]
    assert case['outcome']=='unresolved'
    assert case['effective_operations'][0]['receipt']=='changed'
    assert case['effective_operations'][0]['reason']=='replace the conflicting narration'


def test_summary_prefers_concrete_change_and_does_not_promote_blocked_candidate():
    from novel_manga.application.agents.episode.memory_summary import summarize_case
    summary=summarize_case({'outcome':'accepted','attempts':[
        {'action':'apply','changed':['clip_01','clip_02'],
         'blocked':{'clip_02':'dependent clip cannot be compiled'},'remedies':[
             {'action':'input','clip':'clip_01','reason':'bind the corrected phase'},
             {'action':'input','clip':'clip_02','reason':'an incomplete candidate'}]},
        {'action':'apply','accepted_clips':['clip_03','clip_04','clip_05','clip_06'],
         'remedies':[{'action':'keep','clip':cid,'reason':'audio recheck passed'}
                     for cid in ['clip_03','clip_04','clip_05','clip_06']]},
        {'action':'finish','ok':True}]})
    assert summary['effective_operations'][0]['clip']=='clip_01'
    assert len(summary['effective_operations'])==3
    assert all(r['clip']!='clip_02' for r in summary['effective_operations'])
    assert summary['failed_operations'][0]['error']=='dependent clip cannot be compiled'


def test_recall_includes_retrospective_lessons_without_overriding_live_outcome(tmp_path):
    from novel_manga.application.agents.episode.recovery import recall
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(source_novel_dir=tmp_path/'book',work_dir=tmp_path/'run/sandbox',
        episode_dir=tmp_path/'run/book/book_2',run_dir=tmp_path/'run')
    folder=ctx.source_novel_dir/'.production-memory'
    atomic_write_json(folder/'old.json',{'episode':'book_1','outcome':'unresolved','updated_at':2,
        'issues':[{'kind':'input','detail':'the base card still wears armor'}],'attempts':[]})
    atomic_write_json(folder/'summaries/review/summary.json',{'cases':[
        {'source_memory':str(folder/'old.json'),'recorded_formal_acceptance':True,
         'case_summary':'A phase card was bound before the old acceptance.',
         'lesson_and_limits':'Check the actual phase image before another take.'}]})
    diagnosis={'issues':[{'kind':'input','detail':'wrong costume'}]}
    task=prepare(ctx,{'adopted':True},diagnosis,recall(ctx,diagnosis))
    case=task['related_history'][0]
    assert case['retrospective']['lesson_and_limits']=='Check the actual phase image before another take.'
    assert case['outcome']=='unresolved'
    assert case['effective_operations']==[]


def test_current_task_routes_resumed_agents_to_the_installed_repair_playbook(tmp_path):
    from novel_manga.application.agents.episode.inputs import _install_skill
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    _,references=_install_skill(ctx)
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'agent_phase':'review','skill_references':references})
    task=prepare(ctx,{'adopted':True},{'issues':[]},[])
    assert (ctx.work_dir/task['repair_playbook']).is_file()
    assert task['repair_playbook']==references['repair_playbook']
    assert any('repair_playbook' in rule and 'read' in rule for rule in task['execution_rules'])
