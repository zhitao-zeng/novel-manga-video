"""Current repair context excludes obsolete targets but retains full evidence files."""
from types import SimpleNamespace
from pathlib import Path

from novel_manga.util import atomic_write_json,read_json


def test_brief_contains_only_current_targets_and_exact_operation_choices(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
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
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    task=prepare(ctx,{'adopted':False,'can_adopt_draft':True},
        {'issues':[{'clips':['clip_01'],'detail':'planner failed'}]},[])
    assert task['mode']=='provisional_draft'
    assert any('output/episode.json' in rule and 'adopt' in rule for rule in task['execution_rules'])
    assert not any(rule.startswith('Only these current targets') for rule in task['execution_rules'])
    assert task['issues'][0]['detail']=='planner failed'
