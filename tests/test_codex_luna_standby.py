import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pytest

@pytest.fixture(autouse=True)
def isolated_usage_root(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/standby/codex_luna'))
import luna_transport as m
class AdapterTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.out=Path(self.temp.name);(self.out/'requests').mkdir()
  self.patch=patch.object(m,'OUT',self.out);self.patch.start()
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def request(self):return {'model':'legacy-label','response_format':{'type':'json_object'},'messages':[{'role':'user','content':[{'type':'text','text':'Judge the supplied evidence'}]}]}
 def invoke(self,req=None):return m.post_any(None,['http://unused'],{'Authorization':'SECRET_NOT_TO_BE_LOGGED'},req or self.request())
 def fake(self,text,code=0,completed=True):
  def run(command,folder,**kw):
   (folder/'answer.txt').write_text(text)
   return {'exit_code':code,'completed':completed,'usage':{}}
  return run
 def test_valid_failure_remains_failure(self):
  with patch.object(m,'run',self.fake('{"verdict":"fail","passed":false}')):b=self.invoke()
  self.assertEqual(json.loads(b['choices'][0]['message']['content']),{'verdict':'fail','passed':False})
  self.assertEqual(b['model'],'gpt-6-luna')
  self.assertNotIn('SECRET_NOT_TO_BE_LOGGED',''.join(p.read_text()for p in self.out.rglob('*.json')))
 def test_incomplete_never_becomes_result(self):
  for code,completed in [(124,False),(0,False),(1,True)]:
   with patch.object(m,'run',self.fake('{"result_json":"{}"}',code,completed)),self.assertRaises((RuntimeError, TimeoutError)):self.invoke()
 def test_malformed_never_becomes_empty_pass(self):
  for value in ['', 'not JSON', '[]', '{"verdict":']:
   with patch.object(m,'run',self.fake(value)),self.assertRaises((ValueError,KeyError)):self.invoke()
 def test_inline_image_order_preserved(self):
  import base64
  req=self.request();req['messages'][0]['content'] += [{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(x).decode()}}for x in [b'first',b'second']]
  def run(command,folder,**kw):
   attachments=[Path(command[i+1]).read_bytes()for i,x in enumerate(command)if x=='--image']
   self.assertEqual(attachments,[b'first',b'second']);self.assertIn('Attached image 2',kw['prompt'])
   return self.fake('{"passed":false}')(command,folder,**kw)
  with patch.object(m,'run',run):self.invoke(req)
if __name__=='__main__':unittest.main()


def test_expired_request_does_not_start_codex(tmp_path, monkeypatch):
    from novel_manga.resource_waits import RequestDeadline
    monkeypatch.setattr(m, 'OUT', tmp_path)
    monkeypatch.setattr(m, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('expired request ran')))
    import pytest
    with pytest.raises(TimeoutError):
        m.post_any(None, [], {}, {'messages': []}, deadline=RequestDeadline(0))


def test_sparse_schema_uses_prompt_without_forcing_optional_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'OUT', tmp_path)
    schema = {'type': 'object', 'properties': {'patch': {'type': 'string'}}, 'required': [], 'additionalProperties': False}
    def run(command, folder, **kw):
        assert '--output-schema' not in command and '"patch"' in kw['prompt']
        (folder/'answer.txt').write_text('{}')
        return {'exit_code': 0, 'completed': True, 'usage': {}}
    monkeypatch.setattr(m, 'run', run)
    assert m.post_any(None, [], {}, {'messages': [], 'response_format': {'type': 'json_schema', 'json_schema': {'schema': schema}}})['model'] == 'gpt-6-luna'


def test_native_session_keeps_host_tools_skill_and_history(tmp_path, monkeypatch):
    import luna_session as adapter
    from novel_manga.application.agents.episode import session
    from novel_manga.util import atomic_write_json, read_json
    work = tmp_path/'work'; (work/'output').mkdir(parents=True)
    atomic_write_json(work/'input/manifest.json', {'skill_revision': session.SKILL_REVISION})
    atomic_write_json(work/'output/repair.json', {'decisions': ['old candidate']})
    atomic_write_json(work/session.SESSION_FILE, {'session_id': 'previous', 'ledger': {'calls': 4}})
    seen = []
    def run(command, attempt, **kw):
        prompt = kw['prompt']; seen.append(prompt)
        assert 'END THIS TURN' in prompt and 'host decide acceptance and budgets' in prompt
        assert '--sandbox' in command and 'workspace-write' in command
        tool = (work/'tools/codex_episode.py').read_text()
        assert "DEFAULT_SOCKET = Path('/work/episode-tools.sock')" not in tool and 'episode-luna-' in tool
        (attempt/'answer.txt').write_text('Submitted to the host; awaiting its result.')
        (attempt/'events.jsonl').write_text('')
        atomic_write_json(work/'output/repair.json', {'decisions': ['new source-backed candidate']})
        return {'thread_id': 'new', 'usage': {'input_tokens': 7}, 'exit_code': 0, 'completed': True, 'seconds': 0.1, 'errors': []}
    monkeypatch.setattr(adapter, 'run', run)
    result = adapter.run_session(work, 'Read /work/input/current-task.json', resume=True,
        timeout=45, config={'agent_activity': 'existing-grant', 'agent_instructions': 'current'}, enable_tools=True,
        expected_output='repair.json')
    assert result.record['model'] == 'gpt-6-luna' and result.record['timeout_seconds'] == 45
    assert result.record['produced'] == ['repair.json']
    assert read_json(result.directory/'previous-session.json')['ledger'] == {'calls': 4}
    assert read_json(result.directory/'output-before/repair.json') == {'decisions': ['old candidate']}
    state = read_json(work/session.SESSION_FILE)
    assert state['skill_revision'] == session.SKILL_REVISION and state['agent_activity'] == 'existing-grant'
    assert state['agent_instructions'] == 'current' and state['session_id'] == 'new'
    assert (work/'.claude/skills/episode-production/SKILL.md').read_text() == (session.SKILL_SOURCE/'SKILL.md').read_text()


def test_standby_is_explicit_and_propagates_to_python_helpers(tmp_path):
    import os, subprocess
    adapter = ROOT/'scripts/standby/codex_luna'
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(adapter), str(ROOT/'src')]), 'CODEX_LUNA_ADAPTER': '0'}
    probe = 'from novel_manga.llm import client; from novel_manga.application.agents.episode import turns; print(client.post_any.__module__, turns.run_session.__module__)'
    normal = subprocess.run([sys.executable, '-c', probe], env=env, capture_output=True, text=True, check=True)
    assert 'luna_transport' not in normal.stdout and 'luna_session' not in normal.stdout
    code = '''import run, subprocess, sys
from novel_manga.application.agents.episode import cli
def fake(argv):
    subprocess.run([sys.executable, '-c', "from novel_manga.llm import client; from novel_manga.application.agents.episode import turns; assert client.post_any.__module__ == 'luna_transport'; assert turns.run_session.__module__ == 'luna_session'"], check=True)
    return 0
cli.main = fake
assert run.main(['status']) == 0
'''
    subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True, text=True)
    help_result = subprocess.run([sys.executable, str(adapter/'run.py'), '--help'], env=env, check=True, capture_output=True, text=True)
    assert 'prepare,run,status' in help_result.stdout


def test_repair_environment_keeps_standby_in_real_python_child(tmp_path, monkeypatch):
    import os, subprocess
    from novel_manga.application.configuration import repair_environment
    (tmp_path/'configs').mkdir()
    (tmp_path/'configs/pipeline.json').write_text('{}')
    adapter = ROOT/'scripts/standby/codex_luna'
    path = os.pathsep.join([str(adapter), str(ROOT/'src')])
    monkeypatch.setenv('PYTHONPATH', path)
    monkeypatch.setenv('CODEX_LUNA_ADAPTER', '1')
    env, _ = repair_environment(tmp_path, tmp_path/'run')
    probe = "from novel_manga.llm import client; from novel_manga.application.agents.episode import turns; assert client.post_any.__module__ == 'luna_transport'; assert turns.run_session.__module__ == 'luna_session'"
    subprocess.run([sys.executable, '-c', probe], env=env, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert os.environ['PYTHONPATH'] == path
    monkeypatch.delenv('PYTHONPATH')
    ordinary, _ = repair_environment(tmp_path, tmp_path/'run')
    assert ordinary['PYTHONPATH'] == 'src'


@pytest.mark.parametrize('optional', [False, True])
def test_json_object_uses_existing_client_schema_without_forcing_sparse_fields(tmp_path, monkeypatch, optional):
    monkeypatch.setattr(m, 'OUT', tmp_path)
    schema={'type':'object','properties':{'observed':{'type':'integer'}},
            'required':[] if optional else ['observed'],'additionalProperties':False}
    request={'model':'original','response_format':{'type':'json_object'},'messages':[{'role':'user','content':[
        {'type':'text','text':'Count the visible people'},
        {'type':'text','text':'Return a JSON object matching this schema: '+json.dumps(schema)}]}]}
    def run(command, folder, **kwargs):
        assert ('--output-schema' in command) is (not optional)
        assert json.loads((folder/'response.schema.json').read_text())==schema
        assert kwargs['prompt'].count(json.dumps(schema, ensure_ascii=False)) == 1
        assert 'Count the visible people' in kwargs['prompt']
        assert request['messages'][0]['content'][-1]['text'].startswith('Return a JSON object matching this schema: ')
        (folder/'answer.txt').write_text('{"observed":30}')
        return {'exit_code':0,'completed':True,'usage':{}}
    monkeypatch.setattr(m,'run',run)
    body=m.post_any(None,[],{},request)
    assert json.loads(body['choices'][0]['message']['content'])=={'observed':30}


def test_positional_h3_array_schema_stays_intact_without_unsupported_native_constraint(tmp_path, monkeypatch):
    monkeypatch.setattr(m,'OUT',tmp_path)
    schema={'type':'object','additionalProperties':False,'required':['shots'],'properties':{'shots':{
        'type':'array','prefixItems':[{'type':'string'},{'type':'string'}],'items':False,'minItems':2,'maxItems':2}}}
    request={'response_format':{'type':'json_object'},'messages':[{'role':'user','content':[
        {'type':'text','text':'Return a JSON object matching this schema: '+json.dumps(schema)}]}]}
    def run(command,folder,**kwargs):
        assert '--output-schema' not in command
        assert json.loads((folder/'response.schema.json').read_text())==schema
        assert json.dumps(schema,ensure_ascii=False) in kwargs['prompt']
        (folder/'answer.txt').write_text('{"shots":["first shot","second shot"]}')
        return {'exit_code':0,'completed':True,'usage':{}}
    monkeypatch.setattr(m,'run',run)
    result=m.post_any(None,[],{},request)
    assert json.loads(result['choices'][0]['message']['content'])['shots']==['first shot','second shot']


def test_bounded_sandbox_luna_uses_login_and_preserves_prior_outputs(tmp_path,monkeypatch):
    import luna_sandbox as adapter
    work=tmp_path/'run'; (work/'input').mkdir(parents=True); (work/'output').mkdir()
    (work/'prompt.txt').write_text('Read input/primary.json; write output/decision.json')
    (work/'output/old.json').write_text('{"old":true}')
    def execute(command,attempt,**kwargs):
        assert command[command.index('-m')+1]=='gpt-6-luna' and '--ignore-user-config' in command
        assert command[command.index('--sandbox')+1]=='workspace-write'
        assert kwargs['cwd']==work and kwargs['timeout']==45
        assert 'actual JPEG' in kwargs['prompt']
        (work/'output/decision.json').write_text('{"action":"stop","reason":"insufficient picture evidence"}')
        (attempt/'events.jsonl').write_text('')
        return {'exit_code':0,'completed':True,'seconds':1,'usage':{'input_tokens':9},'thread_id':'native'}
    monkeypatch.setattr(adapter,'execute',execute)
    result=adapter.run_agent('run',config={'runs_root':str(tmp_path),'model':'old-ds'},timeout=45)
    assert result.ok and result.produced==['decision.json']
    assert result.record['model']=='gpt-6-luna' and result.record['timeout_seconds']==45
    assert (work/'output/old.json').read_text()=='{"old":true}'
    assert (result.directory/'output-before/old.json').read_text()=='{"old":true}'


def test_bounded_sandbox_incomplete_native_turn_is_not_current_success(tmp_path,monkeypatch):
    import luna_sandbox as adapter
    work=tmp_path/'run';work.mkdir();(work/'prompt.txt').write_text('Read pictures and write review')
    def execute(command,attempt,**kwargs):
        (attempt/'events.jsonl').write_text('')
        return {'exit_code':0,'completed':False,'seconds':1,'usage':{},'thread_id':None}
    monkeypatch.setattr(adapter,'execute',execute)
    result=adapter.run_agent('run',config={'runs_root':str(tmp_path)},timeout=12)
    assert not result.ok and result.produced==[]


def test_explicit_sandbox_install_covers_preimported_repair_and_independent_callers(tmp_path):
    import os,subprocess
    env={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT/'scripts/standby/codex_luna'),str(ROOT/'src')]),'CODEX_LUNA_ADAPTER':'0'}
    config=tmp_path/'config.json';config.write_text('{"model":"original-ds"}')
    code="""from novel_manga.application.agents import sandbox
from novel_manga.application.repair import agent
from novel_manga.application.review import independent
assert sandbox.run_agent.__module__ != 'luna_sandbox'
import luna_sandbox
luna_sandbox.install()
assert sandbox.run_agent is agent.run_agent is independent.run_agent
assert sandbox.load_config is agent.load_config is independent.load_config
"""+f"assert sandbox.load_config({str(config)!r})['model']=='gpt-6-luna'\n"
    subprocess.run([sys.executable,'-c',code],env=env,check=True,capture_output=True,text=True)


@pytest.mark.parametrize('interrupted', [False, True])
def test_planning_standby_resumes_the_saved_codex_thread(tmp_path,monkeypatch,interrupted):
    import luna_session as adapter
    from novel_manga.application.agents.episode import session
    from novel_manga.util import atomic_write_json,read_json
    work=tmp_path/'work';(work/'input').mkdir(parents=True)
    atomic_write_json(work/'input/current-task.json',{'mode':'planning_repair'})
    seen=[]
    def execute(command,attempt,**kwargs):
        seen.append(command)
        assert '--ephemeral' not in command
        if len(seen)>=2:
            assert 'resume' in command and 'native-planning-thread' in command
        else:assert 'resume' not in command
        atomic_write_json(work/'output/repair.json',{'kind':'draft','revision':len(seen)})
        (attempt/'answer.txt').write_text('候选已交付')
        (attempt/'events.jsonl').write_text('')
        if interrupted and len(seen)==2:
            return {'exit_code':124,'completed':False,'seconds':1,'thread_id':'native-planning-thread',
                    'usage':{},'errors':[]}
        return {'exit_code':0,'completed':True,'seconds':1,'thread_id':'native-planning-thread',
            'usage':{'input_tokens':100 if len(seen)==1 else 180,
                     'output_tokens':10 if len(seen)==1 else 16},'errors':[]}
    monkeypatch.setattr(adapter,'run',execute)
    adapter.run_session(work,'修第一版',timeout=10,expected_output='repair.json')
    if interrupted:
        stopped=adapter.run_session(work,'中断的续接',resume=True,timeout=8,expected_output='repair.json')
        assert stopped.record['usage']=={}
    result=adapter.run_session(work,'根据宿主反馈修第二版',resume=True,timeout=8,expected_output='repair.json')
    assert result.record['resume'] is True
    assert result.record['usage']['input_tokens']==80 and result.record['usage']['output_tokens']==6
    assert read_json(result.directory/'execution.json')['usage']['input_tokens']==180
    assert read_json(work/session.SESSION_FILE)['session_id']=='native-planning-thread'


def test_batch_model_children_keep_the_explicit_standby(tmp_path,monkeypatch):
    from novel_manga.application.production.flow import Batch
    from novel_manga.application.production.batch_cli import main
    from novel_manga.util import atomic_write_json
    import os
    book=tmp_path/'book';source=tmp_path/'source.md';source.write_text('第一章 开始\n甲走来。')
    atomic_write_json(book/'novel.json',{'source':str(source),'title':'书'})
    atomic_write_json(book/'story_bible.json',{'characters':[],'locations':[]})
    adapter=ROOT/'scripts/standby/codex_luna'
    monkeypatch.setenv('PYTHONPATH',str(adapter))
    import argparse
    args=argparse.Namespace(novel_dir=book,source=None,title=None,notes_json=None,
        unattended=False,review_only=False,tier=None,card_parallel=1,resubmit_unconfirmed=False)
    batch=Batch(args)
    assert str(adapter) in batch.env['PYTHONPATH'].split(os.pathsep)
    assert str(ROOT/'src') in batch.env['PYTHONPATH'].split(os.pathsep)


def test_explicit_cli_preflight_checks_cli_login_instead_of_the_unused_api(tmp_path,monkeypatch):
    from novel_manga.application.planning import setup_book
    from novel_manga.application.production.flow import Batch
    monkeypatch.setenv('CODEX_LUNA_ADAPTER','1')
    seen=[]
    monkeypatch.setattr(m,'check_available',lambda:seen.append('codex login') or [],raising=False)
    monkeypatch.setattr(setup_book,'alive',lambda *a:pytest.fail('CLI standby must not depend on Phanthy availability'))
    monkeypatch.setattr(Batch,'endpoint_answers',lambda *a:pytest.fail('CLI standby must not probe an unused API'))
    overrides,problems=setup_book.preflight({'key_var':'NO_KEY_FOR_UNUSED_API'},reading=False)
    assert overrides=={} and problems==[]
    Batch.check_qwen(object())
    assert seen==['codex login','codex login']


def test_cli_response_exposes_standard_token_fields_without_losing_native_counts(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'OUT',tmp_path)
    def execute(command,folder,**kwargs):
        (folder/'answer.txt').write_text('{}')
        return {'exit_code':0,'completed':True,'usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':7}}
    monkeypatch.setattr(m,'run',execute)
    result=m.post_any(None,[],{}, {'messages':[]})
    assert result['usage']['prompt_tokens']==100
    assert result['usage']['completion_tokens']==7
    assert result['usage']['prompt_tokens_details']['cached_tokens']==80
    assert result['usage']['input_tokens']==100
