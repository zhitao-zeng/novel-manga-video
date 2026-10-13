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
