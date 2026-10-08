from novel_manga.application.production.asset_preparation import required_assets, reuse_card
from novel_manga.util import atomic_write_json
import pytest


def test_failed_existing_images_are_reviewed_with_explicit_endpoint_without_redrawing(tmp_path,monkeypatch):
    import json,os,sys
    from types import SimpleNamespace
    from novel_manga.application.production import asset_preparation as batch
    row={'asset_id':'location_001','role':'location','path':'cards/room.jpeg','design':'室内'}
    novel=tmp_path/'book';image=novel/row['path'];image.parent.mkdir(parents=True);image.write_bytes(b'paid image')
    out=tmp_path/'assets';atomic_write_json(out/'state.json',{'results':{'location_001':{'status':'failed'}}})
    monkeypatch.setattr(batch,'required_assets',lambda *a:[row])
    monkeypatch.setattr(batch,'probe_endpoint',lambda name:None,raising=False)
    monkeypatch.setattr(batch,'load_dotenv',lambda *a:None,raising=False)
    monkeypatch.setenv('QWEN38_LOCAL_BASE_URL','http://old-stopped/v1')
    calls=[]
    def run(args,**kwargs):
        calls.append((args,os.environ['QWEN38_LOCAL_BASE_URL']))
        kwargs['stdout'].write(json.dumps({'asset_id':'location_001','status':'reviewed','flags':[]})+'\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(batch.subprocess,'run',run)
    argv=['prepare_batch_assets.py','--novel-dir',str(novel),'--chapters','1','--output',str(out),
          '--endpoint','deepseek','--retry-failed']
    monkeypatch.setattr(sys,'argv',argv)
    assert batch.main()==0
    assert len(calls)==1 and '--review-only' in calls[0][0]
    assert calls[0][1]=='http://172.28.4.52:8831/v1'
    assert image.read_bytes()==b'paid image'
    assert os.environ['QWEN38_LOCAL_BASE_URL']=='http://old-stopped/v1'
    value=json.loads((out/'state.json').read_text())['results']['location_001']
    assert value['status']=='prepared' and value['recovery_attempts']==1


def test_unreachable_review_endpoint_stops_before_spawning_image_workers(tmp_path,monkeypatch):
    import sys,httpx
    from novel_manga.application.production import asset_preparation as batch
    def fail(name):raise httpx.ConnectError('review endpoint unavailable')
    monkeypatch.setattr(batch,'probe_endpoint',fail,raising=False)
    monkeypatch.setattr(batch,'load_dotenv',lambda *a:None,raising=False)
    monkeypatch.setattr(batch,'required_assets',lambda *a:pytest.fail('image preparation before endpoint check'))
    monkeypatch.setattr(sys,'argv',['prepare_batch_assets.py','--novel-dir',str(tmp_path/'book'),
        '--chapters','1','--output',str(tmp_path/'assets'),'--endpoint','deepseek'])
    with pytest.raises(httpx.ConnectError):batch.main()


def test_review_only_card_command_never_constructs_an_image_generator(tmp_path,monkeypatch):
    import sys
    from novel_manga.application.assets import cards
    calls=[]
    monkeypatch.setattr(cards,'load_dotenv',lambda *a:None)
    monkeypatch.setattr(cards,'FramedPhanRouter',lambda *a,**k:pytest.fail('review must not draw'))
    monkeypatch.setattr('novel_manga.application.review.cards.card_verdict_current',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.review.cards.review_cards',
        lambda *a,**k:calls.append(k['only_ids']) or {'flags':[],'locations':{'location_001':{'name':'room'}}})
    monkeypatch.setattr(sys,'argv',['build_cards_thin.py','--novel-dir',str(tmp_path),
        '--assets','location_001','--review-only'])
    assert cards.main()==0 and calls==[{'location_001'}]


def test_review_only_does_not_accept_a_character_card_registered_as_an_object(tmp_path,monkeypatch):
    from novel_manga.application.assets import cards
    atomic_write_json(tmp_path/'entity/types.json',{'铠甲':{'kind':'object'}})
    monkeypatch.setattr('novel_manga.application.review.cards.card_verdict_current',
        lambda *a:{'flags':[],'characters':{'character_001':{'name':'铠甲'}}})
    assert cards.review_existing(tmp_path,['character_001'])==2


def test_review_only_missing_image_cannot_report_success(tmp_path,monkeypatch):
    from novel_manga.application.assets import cards
    monkeypatch.setattr('novel_manga.application.review.cards.card_verdict_current',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.review.cards.review_cards',lambda *a,**k:{'flags':[]})
    with pytest.raises(ValueError,match='no existing image review'):
        cards.review_existing(tmp_path,['location_001'])


def test_interrupted_asset_recovery_keeps_review_and_single_attempt_on_resume(tmp_path,monkeypatch):
    import json,sys
    from types import SimpleNamespace
    from novel_manga.application.production import asset_preparation as batch
    row={'asset_id':'location_001','role':'location','path':'cards/room.jpeg','design':'room'}
    novel=tmp_path/'book';card=novel/row['path'];card.parent.mkdir(parents=True);card.write_bytes(b'keep')
    out=tmp_path/'assets';atomic_write_json(out/'state.json',{'results':{
        'location_001':{'status':'failed','exit':1}}})
    monkeypatch.setattr(batch,'required_assets',lambda *a:[row])
    monkeypatch.setattr(batch,'probe_endpoint',lambda *a:None)
    monkeypatch.setattr(batch,'load_dotenv',lambda *a:None)
    monkeypatch.setattr(sys,'argv',['prepare_batch_assets.py','--novel-dir',str(novel),
        '--chapters','1','--output',str(out),'--endpoint','deepseek','--retry-failed'])
    def interrupted(*a,**k):raise RuntimeError('worker interrupted')
    monkeypatch.setattr(batch.subprocess,'run',interrupted)
    with pytest.raises(RuntimeError,match='worker interrupted'):batch.main()
    pending=json.loads((out/'state.json').read_text())['results']['location_001']
    assert pending['status']=='pending' and pending['recovery_attempts']==1
    assert pending['previous_failure']['status']=='failed'
    calls=[]
    def resumed(args,**kwargs):
        calls.append(args)
        kwargs['stdout'].write(json.dumps({'asset_id':'location_001','status':'reviewed','flags':[]})+'\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(batch.subprocess,'run',resumed)
    assert batch.main()==0 and len(calls)==1 and '--review-only' in calls[0]
    assert json.loads((out/'state.json').read_text())['results']['location_001']['recovery_attempts']==1
    assert card.read_bytes()==b'keep'


def test_pending_asset_recovery_still_owns_missing_card_queue(tmp_path):
    import os
    from novel_manga.application.production.asset_preparation import queued_asset_paths
    row={'asset_id':'location_001','path':'cards/room.jpeg'}
    state={'status':'running','pid':os.getpid(),'results':{'location_001':{
        'status':'pending','recovery_attempts':1,'previous_failure':{'status':'failed'}}}}
    assert queued_asset_paths([row],{'clips':[{'references':[row]}]},tmp_path,state=state)==[str(tmp_path/row['path'])]


def test_reading_cast_and_places_prepare_without_a_screenplay(tmp_path):
    atomic_write_json(tmp_path/'story_bible.json',{'characters':[{'name':'甲'},{'name':'未来人物'}],
        'locations':['客栈：空内景','未来宫殿']})
    atomic_write_json(tmp_path/'bible_aliases.json',{'老甲':'甲'})
    atomic_write_json(tmp_path/'lean/extract/ch-401.json',{'answer':{'people':[
        {'form':'老甲','refers_to':'老甲','presence':'在场'}]}})
    atomic_write_json(tmp_path/'reading_cast.json',{'locations':[
        {'name':'客栈','chapters':[401]},{'name':'未来宫殿','chapters':[900]}]})
    rows=required_assets(tmp_path,{401})
    assert {r['asset_id'] for r in rows}=={'character_001','location_001'}


def test_cache_reuse_requires_matching_character_design(tmp_path):
    cache=tmp_path/'cache';novel=tmp_path/'book'
    row={'path':'series_assets/characters/character_001/turnaround.jpeg','role':'character',
        'design':{'name':'甲','appearance':'黑发','wardrobe':'蓝衣'}}
    source=cache/row['path'];source.parent.mkdir(parents=True);source.write_bytes(b'paid picture')
    atomic_write_json(source.parent/'spec.json',{**row['design'],'wardrobe':'红衣'})
    assert reuse_card(novel,row,[cache]) is None
    atomic_write_json(source.parent/'spec.json',row['design'])
    assert reuse_card(novel,row,[cache])==str(source)
    assert (novel/row['path']).read_bytes()==b'paid picture'


def test_flagged_shared_card_is_rechecked_once_and_unresolved_counts_stay_visible(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from novel_manga.application.production import asset_preparation as batch
    row={'asset_id':'character_001','role':'character','path':'cards/one.jpeg','design':{}}
    novel=tmp_path/'book';card=novel/row['path'];card.parent.mkdir(parents=True);card.write_bytes(b'existing')
    out=tmp_path/'assets';atomic_write_json(out/'state.json',{'results':{
        'character_001':{'status':'needs_review','flags':['identity conflict']}}})
    monkeypatch.setattr(batch,'required_assets',lambda *a:[row])
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        kwargs['stdout'].write(json.dumps({'asset_id':'character_001','status':'built','flags':['still unresolved']})+'\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(batch.subprocess,'run',run)
    args=SimpleNamespace(novel_dir=novel,chapters='1',output=out,cache=[],retry_failed=False,
                         endpoint='deepseek',character_workers=1)
    assert batch.prepare_batch(args)==2
    assert len(calls)==1 and '--fresh-review' in calls[0]
    state=json.loads((out/'state.json').read_text())
    assert state['status']=='finished_with_issues' and state['counts']['needs_review']==1
    assert batch.prepare_batch(args)==2 and len(calls)==1


def test_fresh_card_review_keeps_confirmed_flags_when_no_redraw_is_allowed(tmp_path,monkeypatch,capsys):
    import sys,json
    from novel_manga.application.assets import cards
    from novel_manga.application.review import cards as review
    from novel_manga.config import Settings
    from novel_manga.models.bible import StoryBible,Character
    bible=StoryBible(novel_title='book',genre='generic',visual_style='3D',palette='blue',style_fingerprint='fixed',
        characters=[Character(name='甲',appearance='黑发',wardrobe='蓝衣')],locations=['房间'])
    atomic_write_json(tmp_path/'story_bible.json',bible.model_dump())
    monkeypatch.setattr(cards,'load_dotenv',lambda *a:None)
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda *a,**k:Settings()))
    monkeypatch.setattr(cards,'FramedPhanRouter',lambda *a:object())
    monkeypatch.setattr(cards,'FramedAssetFactory',lambda *a,**k:object())
    monkeypatch.setattr(cards,'build_with_backoff',lambda *a:None)
    calls=[]
    def judged(*a,**k):
        calls.append(1)
        return {'flags':['confirmed identity defect'] if len(calls)==1 else [],
                'characters':{'character_001':{'name':'甲'}},'locations':{}}
    monkeypatch.setattr(review,'review_cards',judged)
    monkeypatch.setattr(review,'remediate_cards',lambda *a:{'deleted':[],'stylized':[],'already_tried':['character_001']})
    monkeypatch.setattr(sys,'argv',['build_cards_thin.py','--novel-dir',str(tmp_path),
        '--assets','character_001','--review','--fresh-review'])
    assert cards.main()==0
    result=json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert calls==[1] and result['flags']==['confirmed identity defect']
