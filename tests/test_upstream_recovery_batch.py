from pathlib import Path
from novel_manga.application.production import upstream_batch


def test_accepted_episode_is_skipped():
    picked,held=upstream_batch.eligible_rows([{'episode':1}],lambda r:{'accepted':True,'live_owner':False})
    assert picked==held==[]


def test_live_owner_is_not_restarted():
    assert upstream_batch.eligible_rows([{'episode':1}],lambda r:{'accepted':False,'live_owner':True})==([],[])


def test_same_failed_recovery_not_requeued():
    selected,held=upstream_batch.eligible_rows([{'episode':1}],lambda r:{'accepted':False,'live_owner':False,
        'recovery_failed':True,'upstream_action':{'kind':'asset'},'reason':'same attempt failed'})
    assert not selected and held[0]['reason']=='same attempt failed'


def test_no_eligibility_records_reason_without_grant(tmp_path):
    selected,held=upstream_batch.eligible_rows([{'episode':1}],lambda r:{'accepted':False,'live_owner':False,
        'recovery_failed':False,'upstream_action':None,'reason':'no current source evidence'})
    assert not selected and held[0]['reason']=='no current source evidence'
    assert list(tmp_path.iterdir())==[]


def test_partial_batch_success_keeps_other_failures():
    def inspect(row):return {'accepted':False,'live_owner':False,'recovery_failed':False,
        'upstream_action':{'kind':'asset'}if row['episode']==1 else None,'reason':'needs source'}
    selected,held=upstream_batch.eligible_rows([{'episode':1},{'episode':2}],inspect)
    assert [r['episode']for r in selected]==[1]
    assert [r['episode']for r in held]==[2]
