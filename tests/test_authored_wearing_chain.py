"""Authored bindings carry wearing evidence through normalization and H3 input."""
import copy
import json
import pytest
from novel_manga.planning.binding import bind_schema, merge
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.contracts import build_schema
from novel_manga.planning.budget import configure_budget
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.application.packing.context import load_context
from novel_manga.application.packing.service import compile_plan
from novel_manga.application.identity.phases import POLICY
from novel_manga.application.rendering import h3
from novel_manga.models.bible import StoryBible

NAME, ARMOR, PLACE = '阿林', '银色护甲', '屋内'
SHEET = {'镜号': 'A1', '摄影角度': '平视', '景别': '中景', '场景': PLACE,
         '画面内容 / 动作': '阿林站在桌前点头。', '台词 / 声音': '声音：风声',
         '机位 / 运镜 / 连续性': '固定机位', '叙事目的': '回应', '预算秒': 8}
BOUND = {'镜号': 'A1', 'segment_id': 'seg_1', 'source_quote': '阿林站在桌前，向对方点头回应。',
         'start_state': '阿林站在桌前。', 'end_state': '阿林点头后站定。',
         'light': '日光', 'in_frame': [NAME], 'extras': [], 'actions': []}


def test_author_and_regular_planner_share_object_definitions():
    ctx = PlannerContext()
    authored = bind_schema({'shots': [SHEET]}, [NAME], [PLACE], ['seg_1'],
                           ctx=ctx, prop_names=[ARMOR])['properties']['bindings']['items']['properties']
    regular = build_schema([NAME], [PLACE], ['seg_1'], ctx=ctx, prop_names=[ARMOR])[
        'properties']['clips']['items']['properties']['stages']['items']['properties']
    for field in ['props', 'wears', 'scene_objects']:
        assert authored[field] == regular[field]
    assert None in authored['wears']['additionalProperties']['enum']


@pytest.mark.parametrize('fields', [{}, {'props': [], 'wears': {}, 'scene_objects': []},
                                      {'props': [], 'wears': {NAME: None}, 'scene_objects': ['桌上护甲']}])
def test_binding_preserves_absence_empty_and_explicit_removal(fields):
    answer = {'bindings': [{**BOUND, **fields}]}
    saved = copy.deepcopy(answer)
    stage = merge({'shots': [SHEET]}, answer)['clips'][0]['stages'][0]
    for key in ['props', 'wears', 'scene_objects']:
        assert (key in stage) == (key in fields)
        if key in fields: assert stage[key] == fields[key]
    if 'wears' in stage: stage['wears'][NAME] = ARMOR
    assert answer == saved


@pytest.mark.parametrize('state', ['worn', 'removed', 'worn_and_empty_copy'])
def test_authored_wearing_reaches_real_pack_and_translation(tmp_path, monkeypatch, state):
    wearing = None if state == 'removed' else ARMOR
    fields = {'props': [] if wearing is None else [ARMOR], 'wears': {NAME: wearing},
              'scene_objects': ['另一套无人护甲站在门边'] if state == 'worn_and_empty_copy' else []}
    raw = merge({'shots': [SHEET]}, {'bindings': [{**BOUND, **fields}]})
    bible = StoryBible.model_validate({'novel_title': '测试', 'genre': 'generic', 'visual_style': '2d',
        'palette': '自然', 'style_fingerprint': 'fixture',
        'characters': [{'name': NAME, 'appearance': '黑发青年', 'wardrobe': '布衬衫'}],
        'locations': [PLACE + '：房间'],
        'props': [{'name': ARMOR, 'category': '战甲', 'appearance': '银色', 'first_chapter': 1, 'quote': '护甲'}]})
    segments = [{'segment_id': 'seg_1', 'text': BOUND['source_quote']}]
    ctx = PlannerContext(); configure_budget(100, shape="fixed", ctx=ctx)
    ctx.authored_storyboard = True; ctx.spoken_range = (0, 300)
    valid = validate_and_normalize(raw, segments, bible, {PLACE: bible.locations[0]}, BOUND['source_quote'], ctx=ctx)
    assert not valid.errors, valid.errors
    assert valid.shots[0]['wears'] == fields['wears']
    assert valid.shots[0].get('scene_objects', []) == fields['scene_objects']
    book = tmp_path/'trial'; episode = book/'trial_1'; episode.mkdir(parents=True)
    (book/'story_bible.json').write_text(bible.model_dump_json())
    (book/'profile.json').write_text(json.dumps({'style': '2d', 'frame': '16:9'}))
    (episode/'segments.json').write_text(json.dumps(segments))
    for asset in ['character_001', 'character_001-p1']:
        path = book/'series_assets/characters'/asset; path.mkdir(parents=True)
        (path/'turnaround.jpeg').write_bytes(b'fixture card; no image request')
    (book/'series_assets/phases.json').write_text(json.dumps({'policy': POLICY, 'characters': {
        NAME: [{'from': 1, 'to': None, 'asset_id': 'character_001-p1', 'label': '穿甲', 'wears': ARMOR}]}}))
    script = {'profile': {'style': '2d', 'frame': '16:9'}, 'shots': valid.shots}
    (episode/'chapter_script.json').write_text(json.dumps(script))
    plan, _ = compile_plan(script, load_context(episode, book/'story_bible.json'))
    clip = next(c for c in plan['clips'] if c['kind'] == 'video')
    card = next(r for r in clip['references'] if r['role'] == 'character')
    assert card['asset_id'] == ('character_001' if wearing is None else 'character_001-p1')
    assert not any(r['role'] == 'prop' for r in clip['references'])
    expected = f'{NAME}脱下穿戴物' if wearing is None else f'{NAME}穿着{ARMOR}'
    assert '穿戴状态：' + expected in clip['prompt']
    seen = []
    def translate(parts, schema, **kwargs):
        seen.append(parts[0]['text'])
        # Model is mocked: verify the real translator input, not its ability to infer wearing.
        return {'shots': ['<Subject 1> stands at the desk and nods.'], 'soundscape': 'Wind.'}
    monkeypatch.setattr(h3, 'ask_json', translate)
    assert h3.convert(clip, tries=1)
    assert any(expected.replace(NAME, '<Subject 1>') in q for q in seen)
    if state == 'worn_and_empty_copy': assert any('另一套无人护甲站在门边' in q for q in seen)
