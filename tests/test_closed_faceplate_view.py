"""A worn phase card's closed-faceplate view: packed as a second picture of the same person, declared in English as
the closed look only - never a second person, never where the face comes from.

美漫 ch12 (2026-09-25): with only the open-faceplate card, every closed faceplate was H3's own - gold in four clips
of twenty, silver in two."""
import json

from novel_manga.application.assets.phase_cards import closed_view_prompt
from novel_manga.application.identity.phases import POLICY
from novel_manga.application.packing.assets import build_references
from novel_manga.application.rendering import h3 as translator
from novel_manga.models.bible import Character, Prop, StoryBible
from novel_manga.story.h3 import request_issues, subject_lines


def book(tmp_path, closed_view):
    novel = tmp_path / 'book'
    cards = novel / 'series_assets'
    for asset in ('character_001', 'character_002', 'character_002-p1'):
        (cards / 'characters' / asset).mkdir(parents=True)
        (cards / 'characters' / asset / 'turnaround.jpeg').write_bytes(b'jpeg')
    if closed_view:
        (cards / 'characters' / 'character_002-p1' / 'closed.jpeg').write_bytes(b'jpeg')
    (cards / 'props' / 'prop_001').mkdir(parents=True)
    (cards / 'props' / 'prop_001' / 'turnaround.jpeg').write_bytes(b'jpeg')
    (cards / 'phases.json').write_text(json.dumps({'policy': POLICY, 'characters': {'托尼': [
        {'from': 12, 'to': 12, 'asset_id': 'character_002-p1', 'label': '穿马克2机甲', 'wears': '马克2机甲',
         'closed': '和机身一样是银白色，眼部发光'}]}}))
    bible = StoryBible(novel_title='t', genre='g', visual_style='s', palette='p', style_fingerprint='fp',
                       characters=[Character(name='席勒', role='主角', appearance='黑发', wardrobe='马甲'),
                                   Character(name='托尼', role='配角', appearance='棕发', wardrobe='西装')],
                       locations=['诊所：低矮房间'],
                       props=[Prop(name='马克2机甲', category='战甲', appearance='银白色机甲', material='合金',
                                   first_chapter=12, quote='…', wearable=True)])
    return novel, bible


def pack(tmp_path, closed_view, closed=frozenset({'托尼'})):
    novel, bible = book(tmp_path, closed_view)
    return build_references(['席勒', '托尼'], '诊所', bible, {'诊所': '诊所：低矮房间'}, novel_dir=novel, chapter=12,
                            faceplate_closed=closed)


def test_the_closed_view_is_the_same_persons_second_picture(tmp_path):
    refs, bindings, _ = pack(tmp_path, True)
    tony = [r for r in refs if r.get('name') == '托尼']
    assert [r['path'].rsplit('/', 1)[-1] for r in tony] == ['turnaround.jpeg', 'closed.jpeg']
    assert tony[1]['view'] == 'closed' and tony[1]['asset_id'] == 'character_002-p1'
    assert not [r for r in refs if r['role'] == 'prop']
    assert f"{tony[1]['tag']}也是托尼：面罩合上时的样子" in bindings[1]


def test_a_clip_with_the_faceplate_open_gets_no_closed_view(tmp_path):
    """Sent to every clip, H3 shut the faceplate on its own in 6 of 10 (ch12 part one, 2026-09-25)."""
    refs, bindings, _ = pack(tmp_path, True, closed=frozenset())
    assert [r['path'].rsplit('/', 1)[-1] for r in refs if r.get('name') == '托尼'] == ['turnaround.jpeg']
    assert '面罩合上' not in bindings[1]


def test_without_the_file_nothing_is_added(tmp_path):
    refs, bindings, _ = pack(tmp_path, False)
    assert [r['path'].rsplit('/', 1)[-1] for r in refs if r.get('name') == '托尼'] == ['turnaround.jpeg']
    assert '面罩合上' not in bindings[1]


def test_the_english_declares_one_person_with_a_closed_look(tmp_path, monkeypatch):
    refs, _, _ = pack(tmp_path, True)
    clip = {'clip_id': 'clip_09', 'request_seconds': 5, 'references': refs,
            'prompt': '【阶段一】托尼的面罩合上。结束时：托尼转身。画面呈现'}
    defs, subjects = subject_lines(clip)
    assert subjects == {'席勒': 1, '托尼': 2}
    tony = next(d for d in defs if d.startswith('<Subject 2>'))
    assert tony.startswith('<Subject 2> is the person shown in <Picture 2> and <Picture 3>.')
    assert 'Take only the face, hair, build and clothing from <Picture 2>.' in tony
    assert "<Picture 3> shows this same person with the helmet's faceplate closed" in tony
    monkeypatch.setattr(translator, 'ask_json', lambda parts, schema, **kw: {
        'shots': ["<Subject 2>'s faceplate closes and he turns away."]})
    monkeypatch.setattr(translator, 'english_delivery', lambda *_: {})
    assert translator.convert(clip)
    assert '<Subject 2>: fully_preserved - the identity, face, hair and clothing of <Picture 2>;' in clip['prompt_h3']
    assert request_issues(clip) == []


def test_the_closed_view_prompt_keeps_the_card_and_shuts_the_faceplate():
    prompt = closed_view_prompt('托尼', '马克2机甲', '和机身一样是银白色，眼部发光')
    assert prompt.startswith('图1是托尼穿着马克2机甲的角色卡；图2是马克2机甲的设定图')
    assert '只把头盔面罩完全合上' in prompt and '和机身一样是银白色，眼部发光，看不到脸和头发' in prompt


def test_fully_closed_clip_uses_only_closed_appearance(tmp_path):
    novel,bible=book(tmp_path,True)
    refs,bindings,_=build_references(['席勒','托尼'],'诊所',bible,{'诊所':'诊所：房间'},novel_dir=novel,chapter=12,
                                    faceplate_closed={'托尼'},faceplate_closed_only={'托尼'})
    own=[r for r in refs if r.get('name')=='托尼']
    assert len(own)==1 and own[0]['path'].endswith('/closed.jpeg')
    assert '全段保持' in bindings[1]
    defs,_=subject_lines({'references':refs})
    assert 'stays closed throughout' in '\n'.join(defs)
