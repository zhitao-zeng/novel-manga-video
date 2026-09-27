import json
from pathlib import Path
from unittest.mock import patch
from novel_manga.application.repair.diagnosis import clip_context
from novel_manga.application.repair import flow
from novel_manga.application.repair import history, managed
from support.split_episode import split_episode


def test_diagnosis_reads_the_explicit_part_and_modern_subject_bindings(tmp_path):
    n=tmp_path/'book';e=n/'book_12-1';e.mkdir(parents=True)
    (e/'clip_plan.json').write_text(json.dumps({'clips':[{'clip_id':'c','shot_indexes':[1], 'references':[
        {'role':'character','name':'甲','path':'a.jpg'},{'role':'location','name':'车门','path':'b.jpg'}]}]}))
    (e/'chapter_script.json').write_text(json.dumps({'shots':[{'visual_prompt':'本上集'}]}))
    (e/'episode_review.json').write_text(json.dumps({'clips':{'c':{'defect_issue':'多余身体'}}}))
    c=clip_context(n,12,'c',episode_dir=e)
    assert c['stages'][0]['visual_prompt']=='本上集'
    assert c['subjects'][0]['name']=='甲' and c['issue']=='多余身体'
    assert not (n/'book_12').exists()


def test_prior_real_generations_are_adopted_once_and_not_reset(tmp_path):
    q={'first_pass_generated':{'a':1,'b':0},'automatic_corrections':['a']}
    report={'quality_review':q,'clips':[{'clip_id':'a','attempts':[{'generated_this_run':True}]}]}
    assert history.adopt_reviewed_run(tmp_path,report)=={'a':2}
    assert history.adopt_reviewed_run(tmp_path,report)=={'a':2}
    state=history.load(tmp_path)
    state['trials']=[{'managed':True,'renders':[{'clips':{'a':{'generated_takes':[{'video':'v','take':[1,2,3]}]}}}]}]
    assert managed.generated_counts(state)=={'a':3}


def test_part_context_is_forwarded_to_repair_without_reconstructing_chapter_path(tmp_path,monkeypatch):
    e=tmp_path/'book'/'book_12-1';e.mkdir(parents=True)
    seen={}
    def propose(n,i,**kw):
        seen.update(kw)
        from novel_manga.repair.proposal import RepairProposal
        return RepairProposal({'changed':[]})
    monkeypatch.setattr(flow,'propose_episode',propose)
    flow.repair_episode(e.parent,12,False,episode_dir=e)
    assert seen['episode_dir']==e
