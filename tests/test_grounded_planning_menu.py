from novel_manga.application.planning import source_menu
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.normalization import normalize_turns
from novel_manga.story.dialogue import clip_bindings
from novel_manga.util import atomic_write_json, read_json


def test_source_extra_keeps_identity_through_dialogue_compilation():
    identity = {'unmatched_actors': [
        {'name': '酒吧老板', 'kind': 'individual', 'presence': 'on_stage'},
        {'name': '林间的呼喊者', 'kind': 'individual', 'presence': 'voice'},
        {'name': '铜箱', 'kind': 'object', 'presence': 'on_stage'},
        {'name': '被提及的商人', 'kind': 'individual', 'presence': 'mentioned'}]}
    menu = source_menu.speakers(['无名旅人'], identity)
    assert menu == ['无名旅人', '酒吧老板', '林间的呼喊者']
    ctx = PlannerContext(anonymous_speakers=menu)
    errors = []
    turns, visible = normalize_turns({'turns': [{'speaker_name': '酒吧老板',
        'delivery_mode': 'offscreen_dialogue', 'text': '请跟我来。'}]}, [], [], 1, ctx, errors, [])
    assert not errors and not visible
    bindings = clip_bindings([{'index': 1, 'turns': turns}])
    assert bindings[0]['speaker_name'] == '酒吧老板'


def test_fine_locations_survive_coarse_book_merge_but_only_used_places_are_adopted(tmp_path):
    atomic_write_json(tmp_path/'story_bible.json', {'characters': [], 'locations': ['酒店：旧参考图']})
    atomic_write_json(tmp_path/'reading_cast.json', {'locations': []})
    atomic_write_json(tmp_path/'lean/extract/ch-423.json', {'chapter': 423, 'answer': {'locations': [
        {'name': '酒店卧房', 'sketch': '床和落地窗', 'evidence': '他离开了卧房'},
        {'name': '酒店大堂', 'sketch': '大理石地面', 'evidence': '来到酒店大堂'},
        {'name': '错误地点', 'sketch': '无依据', 'evidence': '另一本书的内容'}]}})
    places = source_menu.locations(tmp_path, 423, '他离开了卧房，来到酒店大堂。')
    assert [p['name'] for p in places] == ['酒店卧房', '酒店大堂']
    assert places[0]['chapters'] == [423]
    source_menu.adopt_selected(tmp_path, places, [{'location': '酒店卧房'}])
    assert read_json(tmp_path/'story_bible.json')['locations'] == ['酒店：旧参考图', '酒店卧房']
    assert read_json(tmp_path/'reading_cast.json')['locations'][0]['evidence'] == '他离开了卧房'


def test_selected_known_place_keeps_existing_book_evidence(tmp_path):
    existing = {'name':'酒店大堂','description':'book-wide evidence','chapters':[1,423]}
    atomic_write_json(tmp_path/'story_bible.json',{'locations':['酒店大堂：固定设计']})
    atomic_write_json(tmp_path/'reading_cast.json',{'locations':[existing]})
    source_menu.adopt_selected(tmp_path,[{'name':'酒店大堂','description':'local sketch',
        'chapters':[423]}],[{'location':'酒店大堂'}])
    assert read_json(tmp_path/'reading_cast.json')['locations']==[existing]


import pytest

@pytest.mark.parametrize('has_current_identity', [True, False])
def test_grounded_remembered_identity_reaches_planner_schema_without_promoting_presence(tmp_path, monkeypatch, has_current_identity):
    from types import SimpleNamespace
    from copy import deepcopy
    from novel_manga.application.planning.cast_inputs import prepare_cast
    from novel_manga.models.bible import StoryBible, Character
    from novel_manga.application.planning import context as pc
    from novel_manga.planning.contracts import build_schema
    from novel_manga.story.source_identity import active_cast_names
    from novel_manga.planning.normalization import normalize_turns
    novel=tmp_path/'book'; episode=novel/'book_1';episode.mkdir(parents=True)
    bible=StoryBible(novel_title='回忆',genre='fantasy',visual_style='3d',palette='p',style_fingerprint='f',
        characters=[Character(name=n,appearance='外观',wardrobe='衣服') for n in ['甲','乙','丙','匣子']],locations=['山顶'])
    atomic_write_json(novel/'story_bible.json',bible.model_dump())
    identity={'entities':{'e1':'甲','e2':'乙','e3':'匣子'},'mentions':[
        {'entity_id':'e1','presence':'on_stage'}, {'entity_id':'e2','presence':'mentioned'},
        {'entity_id':'e3','presence':'mentioned'}, {'entity_id':'UNKNOWN','presence':'mentioned'}],
        'unmatched_actors':[]}
    original=deepcopy(identity)
    monkeypatch.setattr('novel_manga.application.identity.store.load_chapter',lambda *a,**k:{})
    monkeypatch.setattr('novel_manga.application.identity.store.current_context',lambda *a,**k:identity if has_current_identity else {})
    monkeypatch.setattr('novel_manga.application.identity.context.typed_entities',lambda *a,**k:{'匣子':{'kind':'object'}})
    monkeypatch.setattr(pc,'load_entity_index',lambda *a,**k:None)
    monkeypatch.setattr(pc,'ledger_cast',lambda *a:{'甲':'on_stage','乙':'mentioned','匣子':'mentioned'})
    args=SimpleNamespace(dry_run=True,replay=None,grammar=None,bible=novel/'story_bible.json',novel_id='book')
    source=SimpleNamespace(episode=SimpleNamespace(index=1),novel_dir=novel,episode_dir=episode,part=0,
        full_bible=bible,chapter_text='甲想起乙曾说：等我。',segments=[{'segment_id':'seg_1','text':'甲想起乙曾说：等我。'}])
    ctx=PlannerContext();cast=prepare_cast(args,ctx,source)
    assert cast.names == ['甲','乙']
    schema=build_schema(cast.names,list(cast.location_map),['seg_1'],ctx=ctx)
    stage=schema['properties']['clips']['items']['properties']['stages']['items']['properties']
    assert '乙' in stage['turns']['items']['properties']['speaker_name']['enum']
    assert '乙' in stage['in_frame']['items']['enum']
    errors=[]
    turns,visible=normalize_turns({'turns':[{'speaker_name':'乙','delivery_mode':'offscreen_dialogue','text':'等我。'}]},
        [],cast.names,1,ctx,errors,[])
    assert not errors and not visible and turns[0]['speaker_name']=='乙'
    assert active_cast_names(identity)=={'甲'} and identity==original
