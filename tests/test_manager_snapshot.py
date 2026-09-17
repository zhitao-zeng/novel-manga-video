import copy
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from novel_manga.util import atomic_write_json
from novel_manga.review.contracts import REVIEW_POLICY
import repair_manager_flow_thin as flow
import repair_manager_state_thin as state
import repair_manager_progression_thin as progression
import novel_manga.application.review.store as store
import novel_manga.application.repair.delivery as delivery
import novel_manga.application.repair.history as history
import novel_manga.application.repair.managed as managed
import novel_manga.application.production.runs as thin_runs
import novel_manga.application.preparation.readiness as clip_readiness


def manager_cases():
    results={}
    for advance in [False,True]:
     with tempfile.TemporaryDirectory(prefix='nmv-manager-scan-') as tmp:
      base=Path(tmp);novel=base/'book';manager=flow.Manager(novel,base/'old')
      manager.state.update(jobs=[{'kind':'fill','status':'running','episodes':[2],'id':'busy','step':0},
                                {'kind':'recovery','status':'done','episodes':[3],'id':'recovery','step':0}],scan_started=True)
      for n in [1,2,3,4,5,6]:
       d=novel/f'book_{n}';d.mkdir(parents=True)
       atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
      calls=[];pending={1:True}
      def status(d,h3):
       n=int(d.name.rsplit('_',1)[1])
       if n==4:raise ValueError('unreadable')
       return 'done_with_warnings' if n==3 else 'stale' if n==5 else 'plan_blocked' if n==6 else 'done'
      def reconciliation(d,local,flash,**kw):
       n=int(d.name.rsplit('_',1)[1]);passed=n!=3
       result={'policy':REVIEW_POLICY,'clips':{'clip_01':{'video':'v','take':[1,2,3],'story_ok':passed,
              'verify':{'verdict':'fine' if passed else 'obvious'}}},'feedback':{} if passed else {'clip_01':'bad'}}
       return {},result,{'clip_01':{'video':'v','take':[1,2,3]}}
      def persist(d,previous,review):calls.append(('review',int(d.name.rsplit('_',1)[1])))
      def old_reconcile(d,l,f,write=True):
       previous,review,takes=reconciliation(d,l,f)
       if write:persist(d,previous,review)
       return review,takes
      def observe(d,r,t):calls.append(('history',int(d.name.rsplit('_',1)[1])))
      def publish(d,r,t):
       n=int(d.name.rsplit('_',1)[1]);calls.append(('publish',n));pending[n]=False;return n==1
      with patch.object(clip_readiness,'current_blocks',lambda d:{'clip_01':['blocked']}),patch.object(store,'current_evidence',lambda *a:({},{})),patch.object(thin_runs,'episode_status',status),patch.object(thin_runs,'render_runs',lambda *a:1),patch.object(history,'observe',observe),patch.object(delivery,'publish_if_ready',publish),patch.object(delivery,'publication_pending',lambda d:pending.get(int(d.name.rsplit('_',1)[1]),False)),patch.object(managed,'candidates',lambda *a:(['clip_01'],{})),patch.object(time,'monotonic',lambda:1000):
       with patch.object(store,'read_reconciled',reconciliation),patch.object(store,'write_reconciled',persist):
        if advance:progression.advance(manager)
        else:state.install_snapshot(manager,state.read_snapshot(manager))
      results['advance' if advance else 'query']={'state':manager.state,'info':manager.info,'inspection':manager.inspection_rows,'calls':calls}
      results['advance' if advance else 'query']=json.loads(json.dumps(results['advance' if advance else 'query'],ensure_ascii=False).replace(str(base),'<root>'))
    return results


def test_query_and_progression_match_prior_views_and_per_episode_write_order():
    expected=json.loads((Path(__file__).parent/'fixtures/manager_snapshot_before.json').read_text())
    assert manager_cases()==expected


def test_query_changes_neither_manager_nor_production_files(tmp_path, monkeypatch):
    manager=flow.Manager(tmp_path/'book',tmp_path/'old');directory=manager.novel/'book_1';directory.mkdir(parents=True)
    atomic_write_json(directory/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    atomic_write_json(directory/'episode_review.json',{'original':True})
    atomic_write_json(directory/'repair_history/history.json',{'trials':[]})
    (directory/'book_1.mp4').write_bytes(b'original final')
    original=copy.deepcopy(manager.state)
    files={p:p.read_bytes() for p in manager.novel.rglob('*') if p.is_file()}
    monkeypatch.setattr(thin_runs,'episode_status',lambda *a:'done')
    monkeypatch.setattr(delivery,'publication_pending',lambda *a:True)
    monkeypatch.setattr(store,'read_reconciled',lambda *a:({}, {'clips':{},'feedback':{}},{}))
    def forbidden(*args,**kwargs):raise AssertionError('state query attempted a production write')
    monkeypatch.setattr(store,'write_reconciled',forbidden)
    monkeypatch.setattr(history,'observe',forbidden)
    monkeypatch.setattr(delivery,'publish_if_ready',forbidden)
    result=state.read_snapshot(manager)
    assert result.state['summary']['total']==1
    assert manager.state==original and manager.info=={} and manager.inspection_rows==[]
    assert {p:p.read_bytes() for p in manager.novel.rglob('*') if p.is_file()}==files
