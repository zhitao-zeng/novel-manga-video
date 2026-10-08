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
