import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
import signal
from novel_manga.util import atomic_write_json
from novel_manga.llm import client as model_client
import novel_manga.application.preparation.flow as flow
import novel_manga.application.preparation.actions as actions
import novel_manga.application.preparation.store as store
import novel_manga.application.preparation.audit as audit
import novel_manga.application.preparation.readiness as clip_readiness
import novel_manga.application.rendering.h3 as build_h3_prompts
import novel_manga.application.profiles as thin_profile
import novel_manga.application.repair.flow as repair_flow_thin
import novel_manga.application.identity.flow as identity_flow_thin


def frozen_preparation():
    results={}
    for mode in ['ready','local','full','incomplete','existing']:
     with tempfile.TemporaryDirectory(prefix='nmv-prep-freeze-') as tmp:
      d=Path(tmp)/'book/book_1';plan={'limits':{'max_clip_seconds':15},'clips':[{'clip_id':'clip_01','kind':'video','shot_indexes':[1],'cast':[],'references':[],'prompt':'角色转身'}]}
      script={'shots':[{'index':1,'characters':[]}]}
      for name,value in [('clip_plan.json',plan),('chapter_script.json',script),('segments.json',[{'segment_id':'seg_1','text':'李明转身告诉王刚，今天要下雨。'}])]:atomic_write_json(d/name,value)
      atomic_write_json(d.parent/'novel.json',{'source':'source.txt'})
      if mode=='existing':(d/'book_1.mp4').write_bytes(b'existing')
      calls=[];statuses=[];original_record=store.record
      issue={'kind':'missing_event' if mode=='full' else 'action','stage':0 if mode=='full' else 1,'source_quote':'李明转身告诉王刚','problem':'动作错人','correction':'改回原文主体'}
      answers=iter([{'source_readable':True,'issues':[] if mode=='ready' else [issue]}, {'source_readable':True,'issues':[]}])
      def audited(directory):calls.append('audit');return next(answers)
      def tool(args,timeout=1200):calls.append(args)
      def record(directory,status,**kw):statuses.append(status);return original_record(directory,status,**kw)
      proposal={'changed':[],'why':'incomplete'} if mode=='incomplete' else {'changed':['clip_01'],'proposal':{'script':script,'plan':plan,'notes':{},'changes':{}}}
      with patch.object(actions,'audit',audited),patch.object(actions,'run_tool',tool),patch.object(flow,'record',record),patch.object(actions,'record',record),patch.object(clip_readiness,'inspect_episode',lambda *a,**kw:(plan,{})),patch.object(thin_profile,'h3_prompt_outdated',lambda *a:False),patch.object(build_h3_prompts,'convert',lambda *a,**k: (_ for _ in ()).throw(AssertionError('no translation needed'))),patch.object(repair_flow_thin,'repair_episode',lambda *a,**k:proposal):value=flow.prepare_one(d)
      value={k:v for k,v in value.items() if k not in {'at','inputs','retry_after'}}
      results[mode]={'result':value,'calls':calls,'statuses':statuses,'script':json.loads((d/'chapter_script.json').read_text()),'history':(d/'repair_history/history.json').exists()}
      results[mode]=json.loads(json.dumps(results[mode]).replace(str(Path(tmp)),'<root>'))
    with tempfile.TemporaryDirectory(prefix='nmv-prep-audit-') as tmp:
     d=Path(tmp)/'book/book_1'
     atomic_write_json(d/'chapter_script.json',{'shots':[{'index':1,'characters':['李明']}]})
     atomic_write_json(d/'segments.json',[{'segment_id':'seg_1','text':'李明转身告诉王刚，今天要下雨。'}])
     atomic_write_json(d.parent/'story_bible.json',{'characters':[{'name':'李明','role':'人物'}]})
     calls=[]
     def ask(parts,schema,**kwargs):
      calls.append({'parts':parts,'schema':schema,'options':kwargs})
      if kwargs['name']=='pre_render_audit_confirmation':return {'confirmed':[],'reason':'已表达'}
      return {'source_readable':True,'source_problem':'','issues':[{'kind':'action','stage':1,'source_quote':'','source_segment':'seg_1','problem':'动作漏了','correction':'补充转身'}]}
     with patch.object(identity_flow_thin,'resolve_chapter',lambda *a,**k:{}),patch.object(model_client,'ask_json',ask):result=audit.audit(d)
     results['audit']={'result':result,'calls':calls}
    return results


def test_preparation_routes_requests_and_writes_match_previous_flow():
    expected = json.loads((Path(__file__).parent / 'fixtures/preparation_before.json').read_text())
    assert frozen_preparation() == expected


def test_pause_stops_new_dispatch_and_waits_for_the_active_child(tmp_path, monkeypatch):
    novel = tmp_path / 'book'; novel.mkdir()
    handlers = {}; launches = []; polls = []
    monkeypatch.setattr(flow.signal, 'signal', lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr('novel_manga.util.load_dotenv', lambda _: None)
    class Child:
        pid = 12345
        returncode = None
        def poll(self):
            polls.append(1)
            if len(polls) < 3:
                return None
            store.record(novel / 'book_1', 'ready')
            self.returncode = 0
            return 0
    def launch(args, **kwargs):
        launches.append(args)
        return Child()
    monkeypatch.setattr(flow.subprocess, 'Popen', launch)
    monkeypatch.setattr(flow.time, 'sleep', lambda _: handlers[signal.SIGTERM]())
    with patch.dict(os.environ):
        flow.run(SimpleNamespace(novel_dir=novel, chapters='1-2', workers=1, episode=None))
    assert len(launches) == 1 and launches[0][-1] == '1'
    assert Path(launches[0][1]).name == 'prepare_h3_book.py'
    assert len(polls) == 3
    status = store.read(novel / 'h3_preparation/status.json')
    assert status['status'] == 'paused' and status['running'] == {}
