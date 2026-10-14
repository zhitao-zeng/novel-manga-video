import json
from PIL import Image
from novel_manga.application.review import cast_video, evidence


def test_scope_change_invalidates_cached_card_description(tmp_path, monkeypatch):
    card = tmp_path / 'card.jpg'
    Image.new('RGB', (30, 40)).save(card)
    questions = []
    def ask(parts, *args, **kwargs):
        questions.append(parts[-1]['text'])
        return {'look': '深色翻领外套，可见胸口'}
    monkeypatch.setattr(cast_video.model_client, 'ask_json', ask)
    cast_video.card_look(tmp_path, '甲', card)
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope': 'identity_portrait'}))
    cast_video.card_look(tmp_path, '甲', card)
    assert len(questions) == 2
    assert '不能推断衣摆长度' in questions[-1]


def test_portrait_look_does_not_override_current_card_with_base_wardrobe(tmp_path, monkeypatch):
    card = tmp_path / 'card.jpg'
    Image.new('RGB', (30, 40)).save(card)
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope': 'identity_portrait'}))
    (tmp_path / 'story_bible.json').write_text(json.dumps({'characters': [{'name': '甲', 'wardrobe': '深灰及大腿中段羊毛长外套'}]}))
    monkeypatch.setattr(cast_video, 'card_look', lambda *args: '深色翻领外套，可见胸口')
    result = cast_video.looks({'cast': ['甲'], 'references': [{'name': '甲', 'role': 'character', 'path': 'card.jpg'}]}, tmp_path, tmp_path/'book_1')
    assert '深灰及大腿中段羊毛长外套' not in result['甲']
    assert '不能推断衣摆长度' in result['甲']
    assert '不要求画外部位入镜' in result['甲']


def test_frame_review_carries_portrait_scope(tmp_path, monkeypatch):
    from novel_manga.models.bible import StoryBible, Character
    work = tmp_path/'book_1/work/review/clip_01'; work.mkdir(parents=True)
    card = tmp_path/'card.jpg'; Image.new('RGB', (30,40)).save(card)
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope':'identity_portrait'}))
    monkeypatch.setattr(evidence, 'clip_frames', lambda *args: [])
    bible = StoryBible(novel_title='n', genre='g', visual_style='live', palette='', style_fingerprint='x', characters=[Character(name='甲', appearance='青年', wardrobe='长外套')])
    _, facts = evidence.collect_clip_evidence({'cast':['甲'], 'references':[{'name':'甲','role':'character','path':'card.jpg'}]}, tmp_path/'v.mp4', bible, work, verify=True)
    assert '不能推断衣摆长度' in ''.join(facts.legend)


def test_scope_annotation_invalidates_same_take_review_without_regenerating(tmp_path,monkeypatch):
    from novel_manga.application.review import episode,judges,adjudication
    from novel_manga.util import atomic_write_json
    novel=tmp_path/'book';ep=novel/'book_1';work=ep/'work/clips/c/attempt_01';work.mkdir(parents=True)
    video=work/'clip.mp4';video.write_bytes(b'existing video')
    card=novel/'card.jpg';Image.new('RGB',(24,32)).save(card)
    atomic_write_json(novel/'story_bible.json',{'novel_title':'n','genre':'g','visual_style':'v','palette':'p','style_fingerprint':'f'})
    atomic_write_json(ep/'clip_plan.json',{'clips':[{'clip_id':'c','kind':'video','references':[{'role':'character','name':'甲','path':'card.jpg'}]}]})
    atomic_write_json(ep/'thin_media_report.json',{'clips':[{'clip_id':'c','selected':{'video':str(video)}}]})
    calls=[]
    def judge(*a,**kw):
        calls.append(1)
        return {'severity':'pass','identity_ok':True,'story_ok':True,'location_ok':True,'time_of_day_ok':True,'visible_people':1,'visual_defects':False,'text_or_watermark':False}
    monkeypatch.setattr(judges,'judge_clip',judge)
    monkeypatch.setattr(episode.cast_video,'enabled',lambda:False)
    monkeypatch.setattr(episode.review_evidence,'review_mode',lambda *a:'classic')
    monkeypatch.setattr(adjudication,'outdated',lambda *a:False)
    episode.review_episode(ep);episode.review_episode(ep);assert len(calls)==1
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope':'identity_portrait'}))
    episode.review_episode(ep);episode.review_episode(ep);assert len(calls)==2
    assert video.read_bytes()==b'existing video'
