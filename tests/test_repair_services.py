"""Repair request and candidate regressions, using the existing split-dialogue fixture."""
import json
import tempfile
from pathlib import Path
import httpx
import pytest
import novel_manga.application.repair.flow as flow
import novel_manga.application.repair.judges as judges
import novel_manga.application.repair.context as context
import novel_manga.application.identity.flow as identity
from support.split_episode import split_episode
from novel_manga.util import atomic_write_json
from novel_manga.repair.proposal import RepairProposal
import novel_manga.application.repair.publication as publication


def repair_contracts():
    results={}
    for mode in ['rewrite','reframe','structure','failed']:
     with tempfile.TemporaryDirectory(prefix='nmv-repair-freeze-') as tmp, pytest.MonkeyPatch.context() as mp:
      episode,script,plan=split_episode.__wrapped__(Path(tmp),mp)
      for file,value in [('chapter_script.json',script),('clip_plan.json',plan),('segments.json',[{'segment_id':'seg_1','text':'林凡走到门边。'}])]:atomic_write_json(episode/file,value)
      calls=[]
      mp.setattr(identity,'resolve_chapter',lambda *a,**k:{'entities':{},'mentions':[]})
      mp.setattr(context,'ledger_cast',lambda *a:{})
      def ask(parts,schema,**options):
       options.pop('settings',None);calls.append({'parts':parts,'schema':schema,'options':options})
       if mode=='failed':raise ValueError('simulated failure')
       return {'stages':[{'origin_index':1,'in_frame':['林凡'],'actions':[{'actor':'林凡','action':'走到门边','target':''}],
          'extras':[],'event':'林凡走到门边','visual_prompt':'林凡走到门边，特写他的手。','camera':'固定近景','shot_scale':'特写','end_state':'林凡站在门边','speakers':[]}]}
      mp.setattr(judges,'ask_json',ask)
      mp.setattr(httpx.HTTPTransport,'handle_request',lambda *a:(_ for _ in ()).throw(AssertionError('real HTTP forbidden')))
      before={name:(episode/name).read_text() for name in ['chapter_script.json','clip_plan.json']}
      result=flow.repair_episode(episode.parent,1,False,use_history=False,reframe=mode in {'reframe','structure'},source_issues={'clip_02':'画面缺少走动'},return_proposal=True,require_structure=mode=='structure')
      after={name:(episode/name).read_text() for name in before}
      results[mode]={'requests':calls,'result':result,'inputs_unchanged':before==after,'history_exists':(episode/'repair_history/history.json').exists()}
    return results


def test_requests_candidates_ranges_and_no_publication_match_frozen_results():
    expected = json.loads((Path(__file__).parent / 'fixtures/repair_services_before.json').read_text())
    assert repair_contracts() == expected


def test_preparation_shares_artifact_order_without_starting_a_repair_trial(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(publication, 'atomic_write_json', lambda path, value: calls.append(path.name))
    monkeypatch.setattr(publication.history, 'begin_trial', lambda *a, **k: pytest.fail('preparation must not start a repair trial'))
    proposal = RepairProposal({'changed': ['clip_01']}, script={'shots': []}, plan={'clips': []}, notes={})
    publication.publish_preparation(tmp_path, proposal)
    assert calls == ['chapter_script.json', 'clip_plan.json', 'review_feedback.json']
