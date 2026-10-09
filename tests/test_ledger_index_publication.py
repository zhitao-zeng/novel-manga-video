"""Authoritative changes immediately publish the two existing consumer indexes."""
import json
from pathlib import Path
import pytest

from novel_manga.application.identity import ledger_store as store, ledger_resolution as resolve, ledger_judges as judges, ledger_flow as flow, ledger_cli as cli
from novel_manga.application.identity.store import load_catalog
from scripts.reconcile_cast_ledger import main as reconcile


def book(tmp_path):
    store.write_json(tmp_path/'story_bible.json', {'characters': [
        {'name':'真实姓名', 'role':'主角'}, {'name':'化名人物', 'role':''}], 'locations':[]})
    ledger = store.Ledger(tmp_path)
    ledger.entities[0]['asset_id'] = 'character_001'
    ledger.claims = [{'id':'c1','chapter':1,'type':'same_as','subject':'e001','object':'e002','status':'pending',
                     'scope':'reality','hidden_from_reader':False,'evidence':'化名人物就是真实姓名。'}]
    ledger.mentions_by_chapter[1] = [
        {'entity':'e001','form':'真实姓名','kind':'proper','presence':'on_stage','chapter':1,'count':1},
        {'entity':'e002','form':'化名人物','kind':'proper','presence':'on_stage','chapter':1,'count':2}]
    store.write_json(ledger.mentions_path(1), ledger.mentions_by_chapter[1])
    with store.ledger_lock(tmp_path): ledger.save()
    return ledger


def index(ledger):
    public = store.read_json(ledger.novel_dir/'entity_index.json', None)
    internal = store.read_json(ledger.base/'index.json', None)
    assert public is not None and public == internal
    return public


@pytest.mark.parametrize('action', ['reject','undo'])
def test_manual_merge_and_reversal_publish_aliases_and_keep_card(tmp_path, action):
    ledger = book(tmp_path)
    with store.ledger_lock(tmp_path):
        assert resolve.decide(ledger,'c1','accepted','confirmed same person')
    merged = index(ledger)
    assert [(r['id'],r['name'],r['asset_id']) for r in merged['characters']] == [('e001','真实姓名','character_001')]
    assert merged['characters'][0]['forms'] == {'化名人物':2,'真实姓名':1}
    assert '化名人物' in load_catalog(tmp_path).by_name['真实姓名']['forms']
    with store.ledger_lock(tmp_path):
        if action == 'undo': assert resolve.undo_merge(ledger,'c1')
        else: assert resolve.decide(ledger,'c1','rejected','different people')
    restored = index(ledger)
    assert {r['id'] for r in restored['characters']} == {'e001','e002'}
    assert next(r for r in restored['characters'] if r['id']=='e001')['asset_id']=='character_001'
    assert next(r for r in restored['characters'] if r['id']=='e002')['forms']=={'化名人物':2}


def test_ordered_source_resolution_publishes_new_unnamed_actor(tmp_path):
    ledger = book(tmp_path)
    raw = {'mentions':[{'form':'山羊','entity':'NEW:山羊','kind':'contextual','presence':'on_stage','evidence':'山羊撞向铲子'}],
           'new_entities':[{'name':'山羊','kind':'animal','named':False,'description':'灰山羊','evidence':'山羊撞向铲子'}],
           'claims':[],'relations':[]}
    store.write_json(ledger.raw_path(2),raw)
    flow.run_resolve(tmp_path,[2],workers=1,texts={2:'山羊撞向铲子。'})
    rows = index(ledger)
    goat = next(r for r in rows['characters'] if r['name']=='山羊')
    assert goat['generic'] is True and goat['contextual_forms']==['山羊'] and rows['chapters']==2
    assert next(c for c in store.read_json(ledger.base/'claims.json',[]) if c['id']=='c1')['status']=='pending'


def test_settle_publishes_merge_without_a_separate_index_command(tmp_path, monkeypatch):
    ledger = book(tmp_path)
    monkeypatch.setattr(judges,'judge_settle',lambda *a: {'verdict':'supports','why':'explicit same person'})
    with store.ledger_lock(tmp_path): resolve.settle_pending(ledger,workers=1)
    assert [r['id'] for r in index(ledger)['characters']]==['e001']
    assert ledger.claims[0]['status']=='accepted'


def test_roster_adoption_publishes_proven_record_without_forcing_pending_clear(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    source=tmp_path/'source.txt';source.write_text('第一章 开始\n山羊冲了出来。')
    store.write_json(tmp_path/'novel.json',{'source':str(source)})
    store.write_json(tmp_path/'reading_cast.json',{'characters':['山羊'],'aliases':{}})
    store.write_json(ledger.raw_path(1),{'new_entities':[{'name':'山羊','kind':'animal','named':False,'description':'灰山羊','evidence':'山羊冲了出来'}]})
    monkeypatch.setattr('sys.argv',['reconcile_cast_ledger.py','--novel-dir',str(tmp_path),'--fix'])
    assert reconcile()==0
    assert next(r for r in index(ledger)['characters'] if r['name']=='山羊')['generic'] is True
    assert store.read_json(ledger.base/'claims.json',[])[0]['status']=='pending'


def test_save_does_not_reload_chapter_files(tmp_path, monkeypatch):
    ledger=book(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('save reloaded files already owned by the ledger')
    monkeypatch.setattr(store,'read_json',forbidden)
    with store.ledger_lock(tmp_path): ledger.save()
    public=json.loads((tmp_path/'entity_index.json').read_text())
    assert public['chapters']==1 and public['characters'][1]['forms']=={'化名人物':2}


def test_explicit_default_index_refresh_updates_both_existing_views(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    store.write_json(ledger.base/'index.json',{'characters':[]})
    monkeypatch.setattr('sys.argv',['entity_ledger_thin.py','index','--novel-dir',str(tmp_path)])
    assert cli.main()==0
    assert len(index(ledger)['characters'])==2


def test_old_index_command_uses_ledger_instead_of_reintroducing_bible_duplicates(tmp_path,monkeypatch):
    from novel_manga.application.identity import index as index_command
    ledger = book(tmp_path)
    with store.ledger_lock(tmp_path): resolve.decide(ledger,'c1','accepted','source proof')
    monkeypatch.setattr('sys.argv',['build_entity_index.py','--novel-dir',str(tmp_path),'--apply'])
    assert index_command.main()==0
    assert [r['id'] for r in index(ledger)['characters']]==['e001']


def test_shared_initialization_adoption_keeps_unproven_roster_labels_pending(tmp_path):
    ledger = book(tmp_path)
    source=tmp_path/'source.txt';source.write_text('第一章 开始\n山羊冲了出来。一个小个子站在旁边。')
    store.write_json(tmp_path/'novel.json',{'source':str(source)})
    store.write_json(ledger.raw_path(1),{'new_entities':[{'name':'山羊','kind':'animal','named':False,'description':'灰山羊','evidence':'山羊冲了出来'}]})
    result=flow.adopt_roster(tmp_path, {'characters':['山羊','小个子','已绑定别名'], 'aliases':{'已绑定别名':'真实姓名'}})
    assert result=={'seeded':['山羊'],'unresolved':['小个子']}
    current=store.Ledger(tmp_path)
    assert '小个子' not in current.by_name
    assert next(r for r in index(ledger)['characters'] if r['name']=='山羊')['generic'] is True
    assert current.claims[0]['status']=='pending'


def test_setup_command_uses_the_shared_adoption_without_models(tmp_path,monkeypatch):
    from novel_manga.application.planning import setup_book
    novel=tmp_path/'outputs/nov'
    novel.mkdir(parents=True)
    source=tmp_path/'source.txt';source.write_text('第一章 开始\n山羊冲了出来。小个子站在旁边。')
    store.write_json(novel/'novel.json', {'source':str(source)})
    store.write_json(novel/'story_bible.json', {'characters':[{'name':'真实姓名','role':'主角'}], 'locations':[]})
    store.write_json(novel/'reading_cast.json', {'characters':['真实姓名','山羊','小个子'], 'aliases':{}})
    store.write_json(novel/'bible_aliases.json', {})
    store.write_json(novel/'entity/raw/ch_0001.json', {'new_entities':[{'name':'山羊','kind':'animal','named':False,'description':'灰山羊','evidence':'山羊冲了出来'}]})
    config=tmp_path/'agent.json';store.write_json(config,{'runs_root':str(tmp_path/'runs')})
    store.write_json(tmp_path/'runs/nov-merge/output/export/story_bible.json', {'characters':[]})
    monkeypatch.setattr(setup_book,'ROOT',tmp_path)
    monkeypatch.setattr(setup_book,'preflight',lambda *a,**kw: ({},[]))
    monkeypatch.setattr(setup_book,'run',lambda *a,**kw: 0)
    monkeypatch.setattr('sys.argv',['setup_book_thin.py',str(source),'--novel-id','nov','--title','测试','--style','meiman','--skip-read','--agent-config',str(config)])
    assert setup_book.main()==0
    ledger=store.Ledger(novel)
    assert set(ledger.by_name)=={'真实姓名','山羊'}
    assert {r['name'] for r in index(ledger)['characters']}=={'真实姓名','山羊'}
