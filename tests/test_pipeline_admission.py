from pathlib import Path
import pytest

from support.episode_conductor import setup, make, write
from novel_manga.application.production import episode_agents
from novel_manga.util import read_json


def script(root, number):
    write(root/f'book/book_{number}/chapter_script.json', {'shots': [{'index':1}]})
    write(root/f'book/book_{number}/clip_plan.json', {'clips': [{'clip_id':'clip_01'}]})


def test_auto_admission_dispatches_ready_and_failed_without_waiting_for_whole_book(setup):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=3)
    for n in (1, 2, 3):
        script(root, n)
    write(root/'book/h3_preparation/episodes/1.json', {'status': 'ready'})
    write(root/'book/h3_preparation/episodes/2.json', {'status': 'needs_repair', 'reason': 'wrong scene'})
    c = make(config, root)
    c.tick()
    assert [row[0] for row in spawned] == ['agent_1', 'agent_2', 'prepare_3']
    assert c.agents['1']['input_mode'] == 'script'
    assert c.agents['2']['input_mode'] == 'script'
    assert c.agents['3']['phase'] == 'preparing_inputs'
    assert spawned[2][1][1] == str(Path(episode_agents.__file__).resolve().parents[4]/'scripts/prepare_h3_book.py')
    c.tick()
    assert len(spawned) == 3
    c.procs['prepare_3'].poll = lambda: 0
    write(root/'book/h3_preparation/episodes/3.json', {'status': 'ready'})
    c.tick()
    assert spawned[-1][0] == 'agent_3'


def test_failed_preparation_process_enters_agent_instead_of_repeating(setup):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    script(root, 1)
    c = make(config, root)
    c.tick()
    assert spawned[0][0] == 'prepare_1'
    c.procs['prepare_1'].poll = lambda: 1
    c.tick()
    assert spawned[-1][0] == 'agent_1'
    assert c.agents['1']['input_mode'] == 'script'
    failure = read_json(root/'book/h3_preparation/episodes/1.json')
    assert failure['status'] == 'error' and failure['reason']


def test_auto_admission_preserves_started_run_even_when_source_preparation_failed(setup):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    script(root, 1)
    write(root/'book/h3_preparation/episodes/1.json', {'status': 'needs_repair'})
    write(root/'agent-output/book/book_1-script/agent-run.json', {'status': 'deferred', 'remaining_seconds': 20})
    c = make(config, root)
    c.tick()
    assert spawned[0][0] == 'agent_1'
    assert c.agents['1']['input_mode'] == 'script'


def test_coordinator_restart_adopts_live_preparation_instead_of_duplicate(setup, monkeypatch):
    from novel_manga.application.production import control
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    script(root, 1)
    first = make(config, root)
    first.tick()
    name, command, _ = spawned[0]
    assert name == 'prepare_1'
    monkeypatch.setattr(control, 'processes', lambda: [{'pid': 1234, 'cwd': str(root), 'args': command}])
    restarted = make(config, root)
    restarted.tick()
    assert len(spawned) == 1
    assert restarted.agents['1']['pid'] == 1234
    assert restarted.agents['1']['phase'] == 'preparing_inputs'


def test_preparation_tools_follow_loaded_code_not_runtime_checkout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novel_manga.application.preparation import actions
    monkeypatch.setattr(actions, 'ROOT', tmp_path)
    called = []
    monkeypatch.setattr(actions.subprocess, 'run', lambda args, **kw: called.append((args, kw)) or SimpleNamespace(returncode=0))
    actions.run_tool(['scripts/build_cards_thin.py', '--assets', 'location_001'])
    assert called[0][0][1] == str(Path(actions.__file__).resolve().parents[4]/'scripts/build_cards_thin.py')
    assert called[0][1]['cwd'] == tmp_path


def test_switch_to_auto_updates_saved_unstarted_source_rows(setup):
    root, config, spawned = setup
    script(root, 1)
    write(root/'production/state.json', {'executor': 'episode_agent', 'agents': {'1': {
        'episode': 1, 'status': 'pending', 'input_mode': 'source',
        'run_dir': str(root/'agent-output/book/book_1-source'), 'output_root': str(root/'agent-output')}}})
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    c = make(config, root)
    c.tick()
    assert spawned[0][0] == 'prepare_1'
    assert c.agents['1']['requested_input_mode'] == 'auto'


def test_failed_preparation_releases_normal_reservation_before_agent_handoff(setup):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1, normal_parallel=1)
    for n in (1,2,3):script(root,n)
    c=make(config,root);c.tick()
    assert spawned[0][0]=='prepare_1'
    c.procs['prepare_1'].poll=lambda:0
    write(root/'book/h3_preparation/episodes/1.json',{'status':'needs_repair','reason':'source conflict'})
    c.tick()
    assert spawned[-1][0]=='prepare_2'
    assert c.agents['1']['production_lane']=='repair'
    assert c.agents['1']['status']=='pending'


@pytest.mark.parametrize('kind,stage,expected', [('location', 2, 'script'),
    ('speaker', 1, 'script'), ('missing_event', 0, 'source')])
def test_preparation_reason_controls_rewrite_scope(setup, kind, stage, expected):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    script(root, 1)
    write(root/'book/h3_preparation/episodes/1.json', {'status':'needs_replan',
        'audit':{'source_readable':True, 'issues':[{'kind':kind, 'stage':stage}]}})
    c=make(config,root);c.tick()
    assert spawned[0][0]=='agent_1'
    assert c.agents['1']['input_mode']==expected


def test_unreadable_source_blocks_without_spending_an_agent_retry(setup):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    script(root, 1)
    write(root/'book/h3_preparation/episodes/1.json', {'status':'needs_source',
        'audit':{'source_readable':False, 'source_problem':'incomplete original'}})
    c=make(config,root);c.tick();c.tick()
    assert not any(name=='agent_1' for name, *_ in spawned)
    assert c.agents['1']['status']=='blocked'
    assert c.agents['1']['waiting_for']=='source'


def test_empty_plan_cannot_enter_local_repair(setup):
    root, config, spawned = setup
    config['novels'][0]['agent'].update(input_mode='auto', parallel=1)
    script(root,1)
    write(root/'book/book_1/clip_plan.json',{'clips':[]})
    write(root/'book/h3_preparation/episodes/1.json',{'status':'error'})
    c=make(config,root);c.tick()
    assert c.agents['1']['input_mode']=='source'


def test_managed_preparation_hands_local_issue_to_agent_without_legacy_reframe(tmp_path, monkeypatch):
    from novel_manga.application.preparation import actions
    directory=tmp_path/'book_1';directory.mkdir()
    answer={'source_readable':True,'issues':[{'kind':'location','stage':1,
        'problem':'speaking in lobby instead of bedroom'}]}
    monkeypatch.setenv('NOVEL_AGENT_MANAGED','1')
    monkeypatch.setattr(actions,'audit',lambda *a,**kw:answer)
    monkeypatch.setattr(actions,'inputs',lambda *a:{'revision':1})
    monkeypatch.setattr(actions,'record',lambda directory,status,**kw:{'status':status,**kw})
    monkeypatch.setattr('novel_manga.application.repair.flow.repair_episode',
                        lambda *a,**kw:pytest.fail('legacy repair must not reinterpret a scene issue'))
    valid, failure=actions.check_script(directory,{'clips':[{'clip_id':'clip_01','shot_indexes':[1]}]})
    assert valid is None and failure['status']=='needs_repair'
    assert failure['audit']==answer
