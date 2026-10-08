from collections import Counter
from pathlib import Path
import pytest

from support.episode_conductor import setup, make, write, select_agent_model, configure_pair
from novel_manga.application.production import episode_agents
from novel_manga.util import read_json


def test_first_pass_qwen_backlog_uses_free_ds_without_restarting_sessions(setup, monkeypatch):
    from support.episode_conductor import configure_native
    root, config, spawned = setup
    configure_native(root, config, monkeypatch, 16)
    config['novels'][0]['agent'].update(first_pass_priority=True, production_parallel=12, parallel=8)
    c = make(config, root)
    originals = {}
    for row in c.agents.values():
        n = row['episode']; run = Path(row['run_dir'])
        row.update(model_endpoint='flashnext', model='qwen-model', production_lane='repair')
        write(run/'agent-run.json', {'status': 'deferred', 'pipeline_phase': 'input_repair',
            'remaining_seconds': 333, 'agent_seconds': 77, 'no_progress': 1,
            'attempts': ['old-qwen-turn'], 'job': {'id': 'job-2', 'action': 'apply', 'status': 'completed'}})
        write(run/f'book/book_{n}/clip_plan.json', {'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
        write(run/'sandbox/episode-session.json', {'session_id': f'session-{n}'})
        write(run/f'book/book_{n}/repair_budget_grants.json', {'clip_01': {'input_limit': 2}})
        for path in [run/'agent-run.json', run/'sandbox/episode-session.json', run/f'book/book_{n}/repair_budget_grants.json']:
            originals[path] = path.read_bytes()
    c.tick()
    assert Counter(env['NOVEL_AGENT_MODEL'] for _, _, env in spawned) == {'agent-model': 8, 'qwen-model': 4}
    assert all(env['NOVEL_FIRST_PASS_ONLY'] == '1' and '--retry-issues' not in command for _, command, env in spawned)
    assert all(path.read_bytes() == before for path, before in originals.items())
    for _, command, env in spawned:
        endpoint = 'deepseek' if env['NOVEL_AGENT_MODEL'] == 'agent-model' else 'flashnext'
        assert command[command.index('--config')+1].endswith(f'{endpoint}.json')
        assert env['NOVEL_H3_TRANSLATION_ENDPOINT'] == endpoint
    # Controller restart keeps allocations, fallback permissions and all saved work.
    restarted = make(config, root)
    for n, row in c.agents.items():
        assert restarted.agents[n].get('model_endpoint') == row.get('model_endpoint')


@pytest.mark.parametrize('boundary', ['fallback', 'accepted', 'running', 'active', 'preferred_available'])
def test_first_pass_reassignment_respects_fallback_and_operation_boundaries(setup, monkeypatch, boundary):
    from support.episode_conductor import configure_native
    from novel_manga.application.production import agent_models
    root, config, _ = setup
    configure_native(root, config, monkeypatch, 5)
    config['novels'][0]['agent'].update(first_pass_priority=True, production_parallel=12)
    c = make(config, root)
    for i in range(1, 5):
        c.agents[str(i)].update(pid=900+i, model_endpoint='flashnext')
    row = c.agents['5']; row.update(model_endpoint='flashnext', status='deferred')
    saved = {'status': 'deferred', 'remaining_seconds': 100}
    if boundary == 'fallback': saved['model_switch'] = {'to_model': 'qwen-model'}
    if boundary in {'accepted', 'running'}: saved['job'] = {'id': 'job-1', 'status': boundary, 'action': 'render'}
    if boundary == 'active': row['pid'] = 999
    if boundary == 'preferred_available': c.agents['4']['pid'] = None
    write(Path(row['run_dir'])/'agent-run.json', saved)
    agent_models.assign(c, row)
    assert row['model_endpoint'] == 'flashnext'
    assert read_json(Path(row['run_dir'])/'agent-run.json') == saved



def test_blocked_summary_loop_gets_one_context_recovery_without_blanket_retry(setup,monkeypatch):
    root,config,spawned=setup
    configure_pair(root,config,monkeypatch,count=1)
    config['novels'][0]['agent']['retry_issues']=True
    c=make(config,root);row=c.agents['1'];folder=Path(row['run_dir'])
    attempts=[]
    for i in range(2):
        d=folder/f'sandbox/attempts/{i}'
        write(d/'attempt.json',{'summary_only':True});attempts.append(str(d))
    saved={'status':'blocked','reason':'episode agent ended twice without operation or output progress',
        'attempts':attempts,'remaining_seconds':100}
    write(folder/'agent-run.json',saved)
    row.update(saved,issue_retry_started=True,model_endpoint='deepseek')
    c.agent_model_groups['deepseek']['config']['refusal_fallback']={
        'model':'qwen-model','base_url':'http://qwen','key_var':'QWEN_KEY'}
    assert episode_agents._retry_due(c,row)
    c.tick()
    assert len(spawned)==1 and '--retry-issues' in spawned[0][1]
    assert row['context_recovery_started']
    row.update(status='blocked',pid=None)
    assert not episode_agents._retry_due(c,row)


def test_both_models_dispatch_complete_hosts_at_8_and_4_with_all_roles(setup, monkeypatch):
    root, config, spawned = setup
    configure_pair(root, config, monkeypatch)
    c = make(config, root)
    c.tick()
    assert Counter(env['NOVEL_AGENT_MODEL'] for _, _, env in spawned) == {'agent-model': 8, 'qwen-model': 4}
    for _, command, env in spawned:
        endpoint = 'flashnext' if env['NOVEL_AGENT_MODEL'] == 'qwen-model' else 'deepseek'
        assert command[command.index('--config')+1] == str(root/f'configs/{endpoint}.json')
        for variable in ('NOVEL_PREPARATION_ENDPOINT','NOVEL_PLANNER_ENDPOINT','SECOND_REVIEW_JUDGE',
                         'NOVEL_REVIEW_VIDEO_JUDGE','VERIFY_JUDGE','NOVEL_H3_REQUEST_CHECK_ENDPOINT'):
            assert env[variable] == endpoint
        if endpoint == 'flashnext':
            assert env['NOVEL_LLM_API_KEY'] == 'test-qwen-credential'
    saved = read_json(root/'production/state.json')
    assert 'test-qwen-credential' not in str(saved)
    assert Counter(row['model_endpoint'] for row in saved['agents'].values() if row.get('pid')) == {'deepseek': 8, 'flashnext': 4}
    c.tick()
    assert len(spawned) == 12


def test_pipeline_review_admission_uses_native_host_for_preparation(setup,monkeypatch):
    root,config,spawned=setup
    configure_pair(root,config,monkeypatch,count=1)
    config['novels'][0]['agent']['input_mode']='auto'
    write(root/'book/book_1/chapter_script.json',{'shots':[{'index':1}]})
    write(root/'book/book_1/clip_plan.json',{'clips':[{'clip_id':'clip_01'}]})
    c=make(config,root)
    for group in c.agent_model_groups.values():group['config']['production_mode']='pipeline_review'
    c.tick()
    assert spawned[0][0]=='agent_1' and c.agents['1']['input_mode']=='script'
    assert c.agents['1']['production_lane']=='normal'


def test_semantic_repair_releases_normal_production_reservation(setup,monkeypatch):
    root,config,spawned=setup
    configure_pair(root,config,monkeypatch,count=1)
    c=make(config,root);row=c.agents['1'];row['production_lane']='normal'
    write(Path(row['run_dir'])/'agent-run.json',{'status':'deferred','pipeline_phase':'input_repair','remaining_seconds':50})
    episode_agents._observe(c,row,[])
    assert row['production_lane']=='repair'


def test_deferred_qwen_keeps_model_and_existing_session_on_restart(setup, monkeypatch):
    root, config, spawned = setup
    configure_pair(root, config, monkeypatch, count=2)
    c = make(config, root);c.tick()
    n = next(n for n,r in c.agents.items() if r.get('model_endpoint') == 'flashnext')
    folder = Path(c.agents[n]['run_dir'])
    write(folder/'agent-run.json', {'status':'deferred','remaining_seconds':200,'session_id':'same-qwen'})
    write(folder/'sandbox/episode-session.json', {'session_id':'same-qwen'})
    restarted = make(config, root);restarted.tick()
    command, env = next((cmd, env) for name,cmd,env in spawned[2:] if name == f'agent_{n}')
    assert env['NOVEL_AGENT_MODEL'] == 'qwen-model'
    assert read_json(folder/'sandbox/episode-session.json')['session_id'] == 'same-qwen'
    assert '--retry-issues' not in command


def test_normal_work_gets_four_positions_with_existing_repair_backlog(setup, monkeypatch):
    root, config, spawned = setup
    configure_pair(root, config, monkeypatch, count=24)
    config['novels'][0]['agent'].update(input_mode='auto', normal_parallel=4)
    for n in range(1, 13):
        write(root/f'agent-output/book/book_{n}-source/agent-run.json', {'status':'deferred','remaining_seconds':200})
    for n in range(13, 25):
        write(root/f'book/book_{n}/chapter_script.json', {'shots': [{'index':1}]})
        write(root/f'book/book_{n}/clip_plan.json', {'clips': [{'clip_id':'clip_01'}]})
    c = make(config, root);c.tick()
    assert all(name.startswith('prepare_') for name, _, _ in spawned[:4])
    assert sum(name.startswith('prepare_') for name, _, _ in spawned) >= 4
    assert any(name.startswith('agent_') for name, _, _ in spawned)
    for name,_,env in spawned:
        assert env['NOVEL_PREPARATION_ENDPOINT'] in {'deepseek','flashnext'}


def test_reservation_does_not_waste_capacity_without_normal_work(setup, monkeypatch):
    root, config, spawned = setup
    configure_pair(root, config, monkeypatch)
    config['novels'][0]['agent']['normal_parallel'] = 4
    c=make(config,root);c.tick()
    assert len(spawned)==12


def test_refusal_switched_episode_resumes_on_qwen_without_resetting_run(setup, monkeypatch):
    root, config, spawned = setup
    configure_pair(root, config, monkeypatch, count=1)
    folder = root/'agent-output/book/book_1-source'
    write(folder/'agent-run.json', {'status':'deferred','remaining_seconds':200,
        'model_switch': {'to_model':'qwen-model'}, 'session_id':'after-refusal'})
    write(folder/'sandbox/episode-session.json', {'session_id':'after-refusal'})
    c=make(config,root);c.tick()
    assert spawned[0][2]['NOVEL_AGENT_MODEL']=='qwen-model'
    assert read_json(folder/'agent-run.json')['remaining_seconds']==200
    assert read_json(folder/'sandbox/episode-session.json')['session_id']=='after-refusal'


def test_external_qwen_worker_ownership_survives_controller_restart(setup, monkeypatch):
    from novel_manga.application.production import control
    root, config, spawned = setup
    configure_pair(root,config,monkeypatch,count=1)
    command=['python','scripts/episode_agent.py','run','--novel-dir',str(root/'book'),
        '--episode','1','--output-root',str(root/'agent-output'),'--input-mode','source',
        '--config',str(root/'configs/flashnext.json')]
    monkeypatch.setattr(control,'processes',lambda:[{'pid':321,'cwd':str(root),'args':command}])
    monkeypatch.setattr(control,'alive',lambda pid:pid==321)
    c=make(config,root);c.tick()
    assert not spawned and c.agents['1']['model_endpoint']=='flashnext'
    monkeypatch.setattr(control,'processes',lambda:[])
    folder=root/'agent-output/book/book_1-source'
    write(folder/'agent-run.json',{'status':'deferred','remaining_seconds':100})
    resumed=make(config,root);resumed.tick()
    assert spawned[0][2]['NOVEL_AGENT_MODEL']=='qwen-model'


def test_failed_normal_first_passes_release_reservation_before_retry_and_restart(setup, monkeypatch):
    from novel_manga.application.production.agent_preparation import normal_work
    root, config, spawned = setup
    configure_pair(root,config,monkeypatch,count=8)
    config['novels'][0]['agent'].update(input_mode='auto', normal_parallel=4, parallel=8, retry_issues=True)
    previous={}
    for n in range(1,5):
        folder=root/f'agent-output/book/book_{n}-source'
        write(folder/'agent-run.json',{'status':'finished_with_issues','passed':False})
        previous[str(n)]={'input_mode':'source','run_dir':str(folder),'production_lane':'normal'}
    write(root/'production/state.json',{'executor':'episode_agent','agents':previous})
    for n in range(5,9):
        write(root/f'book/book_{n}/chapter_script.json',{'shots':[{'index':1}]})
        write(root/f'book/book_{n}/clip_plan.json',{'clips':[{'clip_id':'clip_01'}]})
    c=make(config,root);c.tick()
    assert all(name.startswith('prepare_') for name,_,_ in spawned[:4])
    for n in range(1,5):
        assert c.agents[str(n)]['production_lane']=='repair'
        assert c.agents[str(n)]['issue_retry_started']
        folder=root/f'agent-output/book/book_{n}-source'
        write(folder/'agent-run.json',{'status':'deferred','remaining_seconds':100})
    restarted=make(config,root)
    assert not any(normal_work(restarted,restarted.agents[str(n)]) for n in range(1,5))
