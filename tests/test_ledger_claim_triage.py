"""Incomplete relation candidates and confirmed proper forms are handled without guessing identities."""
from pathlib import Path
import pytest
from novel_manga.entities.evidence import ground
from novel_manga.application.identity import ledger_store as store, ledger_resolution as resolve, ledger_judges as judges


@pytest.mark.parametrize('kind,subject,object_', [('same_as','e001','UNCERTAIN'),('same_as','UNCERTAIN','UNCERTAIN'),('reveal','e001',''),('death','UNCERTAIN','')])
def test_incomplete_identity_candidates_keep_source_mentions(kind,subject,object_):
    text='哈洛姆大主教，赶快离开吧。'
    raw={'mentions':[{'form':'哈洛姆','entity':'e001','kind':'proper','presence':'on_stage','evidence':text}],
         'claims':[{'type':kind,'subject':subject,'object':object_,'evidence':text}]}
    mentions,_,claims,dropped=ground(1,text,raw)
    assert len(mentions)==1 and claims==[]
    assert dropped[0]['what']=='claim' and dropped[0]['evidence']==text


def test_one_sided_death_and_meaningful_unknown_pair_remain_candidates():
    text='林齐确认他死了，但没有人知道两位蒙面客是否同一个人。'
    raw={'claims':[{'type':'death','subject':'e001','object':'','evidence':'林齐确认他死了'},
                   {'type':'same_as','subject':'e002','object':'e003','evidence':'没有人知道两位蒙面客是否同一个人'}]}
    assert len(ground(1,text,raw)[2])==2


def test_true_short_name_and_contextual_description_are_distinguished(tmp_path):
    ledger=store.Ledger(tmp_path)
    ledger._add_entity('云','person',True,'',1)
    ledger._add_entity('云苍冥','person',True,'',1)
    ledger.mentions_by_chapter[1]=[
        {'entity':'e001','form':'云柔','kind':'proper','presence':'on_stage'},
        {'entity':'e001','form':'少女','kind':'contextual','presence':'on_stage'},
        {'entity':'e001','form':'共同称谓','kind':'proper','presence':'on_stage'},
        {'entity':'e002','form':'共同称谓','kind':'proper','presence':'on_stage'}]
    ledger.rebuild_identity_views()
    assert ledger._resolve_ref('云柔',{})=='e001'
    assert ledger._resolve_ref('NEW:云柔',{})=='e001'
    assert ledger._resolve_ref('少女',{}) is None
    assert ledger._resolve_ref('共同称谓',{}) is None
    assert ledger._resolve_ref('柔',{}) is None


def test_confirmed_proper_alias_is_not_created_again(tmp_path,monkeypatch):
    ledger=store.Ledger(tmp_path)
    ledger._add_entity('云','person',True,'',1)
    ledger.mentions_by_chapter[1]=[{'entity':'e001','form':'云柔','kind':'proper','presence':'on_stage'}]
    store.write_json(ledger.mentions_path(1),ledger.mentions_by_chapter[1]);ledger.rebuild_identity_views();ledger.save()
    monkeypatch.setattr(judges,'judge_same',lambda *a: (_ for _ in ()).throw(AssertionError('alias caused new duplicate identity')))
    raw={'mentions':[{'form':'云柔','entity':'NEW:云柔','kind':'proper','presence':'on_stage','evidence':'云柔站在门前'}],
         'new_entities':[{'name':'云柔','kind':'person','named':True,'description':'','evidence':'云柔站在门前'}], 'claims':[],'relations':[]}
    resolve.resolve_chapter(ledger,2,'云柔站在门前。',raw,workers=1)
    assert len(ledger.entities)==1 and ledger.mentions_by_chapter[2][0]['entity']=='e001'


def test_confirmed_chapter_form_overrides_old_id_and_is_not_global(tmp_path,monkeypatch):
    ledger=store.Ledger(tmp_path)
    ledger._add_entity('云','person',True,'',1)
    ledger._add_entity('云苍冥','person',True,'',1)
    store.write_json(tmp_path/'reading_cast.json',{'chapter_aliases':{'1':{'少女':'云'}}})
    raw={'mentions':[{'form':'少女','entity':'e002','entity_name':'云苍冥','kind':'contextual','presence':'on_stage','evidence':'少女走进房间'}],
         'new_entities':[],'claims':[],'relations':[]}
    resolve.resolve_chapter(ledger,1,'少女走进房间。',raw,workers=1)
    assert ledger.mentions_by_chapter[1][0]['entity']=='e001'
    assert ledger._resolve_ref('少女',{}) is None
    resolve.resolve_chapter(ledger,2,'少女走进房间。',raw,workers=1)
    assert ledger.mentions_by_chapter[2][0]['entity']=='e002'


def test_production_catalog_consumes_only_accepted_merge_names_without_new_cards():
    from novel_manga.story.catalog import IdentityCatalog
    from novel_manga.story.source_identity import map_source_reading
    rows=[{'id':'e370','canonical':'白天','asset_id':'character_001','status':'active','merged_into':None},
          {'id':'e525','canonical':'赤红色头发的男子','asset_id':None,'status':'merged','merged_into':'e370'},
          {'id':'e526','canonical':'红发敌人','asset_id':None,'status':'active','merged_into':None}]
    catalog=IdentityCatalog({'characters':[{'name':'白天','role':''}]},rows,{}, {'characters':[]},[],{})
    assert catalog.by_name['赤红色头发的男子']['id']=='e370'
    assert catalog.by_name['赤红色头发的男子']['asset_id']=='character_001'
    assert '红发敌人' not in catalog.by_name
    actor={'source_id':'a1','name':'赤红色头发的男子','kind':'person','presence':'on_stage','appearance':'赤红发',
           'paragraphs':[1],'forms':[{'form':'赤红色头发的男子','kind':'contextual','paragraphs':[1]}]}
    mapped=map_source_reading({'actors':[actor]},catalog,[{'text':'赤红色头发的男子走了进来。'}])
    assert mapped['mentions'][0]['entity_id']=='e370' and not mapped['unmatched_actors']
