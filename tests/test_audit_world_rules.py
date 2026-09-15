import json
from pathlib import Path

import thin_review as review
import shared_audit_thin as audit
import repair_review_thin as precise


def test_precise_review_reads_existing_world_rules_without_changing_global_rules(tmp_path):
    (tmp_path/'review_normal.txt').write_text('# 说明\n龙可以直立并使用手掌。\n')
    original=review.VERIFY_QUESTIONS
    context=review.review_world_context(tmp_path)
    assert '龙可以直立并使用手掌' in context
    assert '本段原文' in context and '不能为动作或台词安错人' in context
    assert '# 说明' not in context and review.VERIFY_QUESTIONS==original
    assert review.review_world_context(tmp_path/'another_book')==''


def test_audit_only_import_backs_up_review_and_leaves_video_untouched(tmp_path):
    novel=tmp_path/'book';directory=novel/'book_1';directory.mkdir(parents=True)
    state=novel/'repair_manager';state.mkdir()
    video=directory/'clip.mp4';video.write_bytes(b'original video')
    old={'policy':'old','clips':{'c':{'video':str(video)}}}
    (directory/'episode_review.json').write_text(json.dumps(old))
    (directory/'clip_plan.json').write_text(json.dumps({'clips':[{'clip_id':'c','kind':'video'}]}))
    (directory/'thin_media_report.json').write_text(json.dumps({'clips':[{'clip_id':'c','selected':{'video':str(video)}}]}))
    take=review.take_identity(video)
    row={'ep':1,'clip':'c','video':str(video),'take':take,'mode':'joint','verdict':'fine','people':[],'evidence':'normal dragon'}
    local={precise.evidence_key(1,'c',str(video),take):row}
    assert audit.sync_episode(novel,state,1,local,{})
    assert json.loads((state/'before_reviews/1.json').read_text())==old
    assert json.loads((directory/'episode_review.json').read_text())['clips']['c']['story_ok']
    assert video.read_bytes()==b'original video'
    assert audit.sync_episode(novel,state,1,local,{})
    assert json.loads((state/'before_reviews/1.json').read_text())==old


def test_audit_does_not_overwrite_a_repair_owned_episode(tmp_path,monkeypatch):
    monkeypatch.setattr(audit,'active_episodes',lambda state:{1})
    monkeypatch.setattr(precise,'reconcile',lambda *a,**k:(_ for _ in ()).throw(AssertionError('repair owns the episode')))
    assert not audit.sync_episode(tmp_path/'book',tmp_path/'state',1,{},{})


def test_partial_audit_preserves_unaudited_model_clips(tmp_path):
    novel=tmp_path/'book';directory=novel/'book_1';directory.mkdir(parents=True)
    state=novel/'repair_manager';state.mkdir()
    h3=directory/'h3.mp4';h3.write_bytes(b'h3')
    sd=directory/'sd.mp4';sd.write_bytes(b'sd')
    untouched={'video':str(sd),'severity':'pass','historical_note':'old SD review'}
    old={'policy':'old','clips':{'h3':{'video':str(h3)},'sd':untouched}}
    (directory/'episode_review.json').write_text(json.dumps(old))
    (directory/'clip_plan.json').write_text(json.dumps({'clips':[{'clip_id':cid,'kind':'video'} for cid in ['h3','sd']]}))
    (directory/'thin_media_report.json').write_text(json.dumps({'clips':[{'clip_id':cid,'selected':{'video':str(video)}} for cid,video in [('h3',h3),('sd',sd)]]}))
    take=review.take_identity(h3);row={'ep':1,'clip':'h3','video':str(h3),'take':take,'mode':'joint','verdict':'fine','people':[]}
    local={precise.evidence_key(1,'h3',str(h3),take):row}
    audit.sync_episode(novel,state,1,local,{})
    updated=json.loads((directory/'episode_review.json').read_text())
    assert updated['clips']['sd']==untouched
    assert updated['audit_scope']['checked_clips']==['h3']


def test_nested_sd_audit_respects_the_main_repair_owner(tmp_path,monkeypatch):
    novel=tmp_path/'book';state=novel/'repair_manager/sd_audit'
    monkeypatch.setattr(audit,'active_episodes',lambda directory:{7} if directory==novel/'repair_manager' else set())
    monkeypatch.setattr(precise,'reconcile',lambda *a,**k: (_ for _ in ()).throw(AssertionError('repair owns this episode')))
    assert not audit.sync_episode(novel,state,7,{},{})
