"""A confirmed configuration fix continues the original candidate once, without a grant."""
import copy
import json
from types import SimpleNamespace
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.agents.episode import input_records, input_repair


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_LOCAL_QWEN_IMAGE_URL','http://127.0.0.1:8770')
    ctx=SimpleNamespace(run_dir=tmp_path/'run',episode_dir=tmp_path/'run/book/book_1')
    decision={'clip_id':'clip_01','action':'input','reason':'correct the state','source_quote':'甲是老人。',
        'phases':{'characters':{'甲':[{'appearance':'白发老人'}]}}}
    record={'clip_id':'clip_01','status':'incomplete','step':'assets','decision':decision,
        'source':{'consistent':True},'resume_count':1,'revision_count':1,
        'error':'ValueError: input phase images failed (甲: image helper exited 2)'}
    directory=ctx.episode_dir/'work/input_repairs'
    atomic_write_json(directory/'clip_01-1.json',{'clip_id':'clip_01','status':'rejected','step':'source'})
    path=directory/'clip_01-2.json';atomic_write_json(path,record)
    log={'name':'甲','asset_id':'character_001-agent-book_1-clip_01-2-resume1',
        'status':'error: ValueError: model resource endpoint has an invalid port','seconds':0.0}
    (ctx.run_dir/'input-assets.log').write_text(json.dumps(log)+'\n')
    return ctx,path,record,decision,log


def test_check_and_apply_continue_same_configuration_failure_without_resetting_counts(saved):
    ctx,path,record,decision,_=saved
    before=input_records.remaining_input_attempts(ctx,['clip_01'])
    assert before=={'clip_01':0}
    _,checked=input_records.input_receipt(ctx,'clip_01',decision,check_only=True)
    assert read_json(path)==record
    assert checked['asset_configuration_recovery']['previous_error']==record['error']
    _,applied=input_records.input_receipt(ctx,'clip_01',decision)
    assert applied['status']=='checking'
    assert applied['decision']==decision
    assert applied['resume_count']==applied['revision_count']==1
    assert input_records.remaining_input_attempts(ctx,['clip_01'])==before
    applied.update(status='incomplete');atomic_write_json(path,applied)
    with pytest.raises(ValueError,match='continuation allowance used'):
        input_records.input_receipt(ctx,'clip_01',decision)


def test_status_offers_precise_original_resume_after_configuration_fix(saved):
    ctx,_,_,_,_=saved
    row=input_repair.repair_status(ctx)['clip_01']
    assert row['resume_action']['resume'] is True
    assert row['remaining_attempts']==0
    assert 'configuration' in row['next_action']


@pytest.mark.parametrize('change',['different_proposal','source_not_confirmed','bad_endpoint','different_failure','later_success'])
def test_configuration_recovery_does_not_reopen_content_or_unfixed_failures(saved,monkeypatch,change):
    ctx,path,record,decision,log=saved;row=copy.deepcopy(decision)
    if change=='different_proposal':row['phases']['characters']['甲'][0]['appearance']='黑发青年'
    if change=='source_not_confirmed':record['source']['consistent']=False;atomic_write_json(path,record)
    if change=='bad_endpoint':monkeypatch.setenv('NOVEL_LOCAL_QWEN_IMAGE_URL','http://127.0.0.1:8770 # note')
    if change=='different_failure':log['status']='error: ValueError: source card mismatch'
    if change=='later_success':log['status']='built';log['seconds']=10
    (ctx.run_dir/'input-assets.log').write_text(json.dumps(log)+'\n')
    with pytest.raises(ValueError,match='allowance used'):
        input_records.input_receipt(ctx,'clip_01',row)
