from types import SimpleNamespace

from novel_manga.application.agents.episode import identity_repair
from novel_manga.application.planning import repair
from novel_manga.util import atomic_write_json, read_json


def context(tmp_path):
    source=tmp_path/'source'/'book_52'
    local=tmp_path/'private'/'book_52'
    script={'shots':[{'index':1,'event':'原稿'}]}
    for folder in (source,local):
        atomic_write_json(folder/'chapter_script.json',script)
    atomic_write_json(source/'planning_attempt.json',{'candidate':{'shots':['original']},'outline':{}})
    return SimpleNamespace(source_episode_dir=source,episode_dir=local,
                           novel_dir=local.parent,run_dir=tmp_path/'run')


def test_identity_handoff_keeps_original_record_and_candidate(tmp_path,monkeypatch):
    ctx=context(tmp_path)
    batch=SimpleNamespace()
    monkeypatch.setattr(identity_repair,'Batch',lambda args:batch)
    def run(target,chapter,**kwargs):
        assert target is batch and chapter==52
        assert kwargs['existing'] is True
        assert kwargs['record_path']==ctx.source_episode_dir/'planning-repair.json'
        assert read_json(ctx.episode_dir/'planning_attempt.json')['candidate']=={'shots':['original']}
        assert target.rows[52]['repair_stage']=='identity'
        return {'status':'planned'}
    monkeypatch.setattr(identity_repair,'repair_chapter',run)
    assert identity_repair.repair(ctx,{},'identity semantic review unresolved: wrong person')['status']=='planned'


def test_exhausted_original_planning_budget_cannot_be_renewed_in_private_book(tmp_path,monkeypatch):
    import threading
    ctx=context(tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':['existing']})
    record=ctx.source_episode_dir/'planning-repair.json'
    atomic_write_json(record,{'initial_seconds':100,'used_seconds':100})
    batch=SimpleNamespace(novel_id='book',episode_dir=lambda n:ctx.episode_dir,
                          planning_stop=threading.Event())
    monkeypatch.setattr(identity_repair,'Batch',lambda args:batch)
    monkeypatch.setattr(repair,'current_context',lambda *a:{})
    monkeypatch.setattr(repair,'failure_inputs',lambda *a:{'kind':'identity','source_segments':[{'text':'甲'}]})
    monkeypatch.setattr(repair,'run_session',lambda *a,**k:(_ for _ in ()).throw(AssertionError('fresh budget')))
    result=identity_repair.repair(ctx,{'runs_root':str(tmp_path/'agents')},'identity semantic review unresolved: wrong person')
    assert result['status']=='held'
    assert read_json(record)['used_seconds']==100
    assert not (ctx.episode_dir/'planning-repair.json').exists()


def test_handoff_does_not_replace_a_newer_local_screenplay(tmp_path,monkeypatch):
    ctx=context(tmp_path)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':['new local draft']})
    monkeypatch.setattr(identity_repair,'Batch',lambda *a:(_ for _ in ()).throw(AssertionError('stale draft')))
    assert identity_repair.repair(ctx,{},'identity semantic review unresolved: wrong person')['status']=='held'


def test_both_agent_phases_use_the_same_existing_watchable_criteria():
    from novel_manga.application.agents.episode.pipeline_preparation import semantic_prompt, review_prompt
    from novel_manga.review.contracts import WATCHABLE_CRITERIA
    assert WATCHABLE_CRITERIA in semantic_prompt()
    assert WATCHABLE_CRITERIA in review_prompt()
