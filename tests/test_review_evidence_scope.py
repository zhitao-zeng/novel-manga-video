import copy
from pathlib import Path

import pytest

from novel_manga.models.bible import StoryBible
from novel_manga.review.policy import verify_to_verdict, fix_tier


def source_tail_case():
    tail = '林齐套上符文甲胄，拿起符文宝剑，向洞穴外走去。'
    clip = {'prompt': '', 'segment_ids': ['s1'], 'action_bindings': [
        {'stage': 1, 'source_stage': 6, 'actions': [
            {'actor': '林齐', 'action': 'puts on rune armor', 'target': '符文甲胄'}]}],
        'wears_bindings': [{'stage': 1, 'source_stage': 6,
                            'wears': {'林齐': '符文甲胄'}, 'in_frame': ['林齐']}]}
    return clip, {'s1': '林齐与伙伴在洞穴里交谈。' * 200 + tail}, tail


def test_both_video_judges_receive_late_source_event_and_structured_shot_scope():
    from novel_manga.review.evidence import ClipEvidence
    from novel_manga.review import prompts
    clip, segments, tail = source_tail_case()
    facts = ClipEvidence({}, [], [], [], [], [], [], segments, '', '', '', '', {})
    for text in (prompts.classic_prompt(clip, {}, '', facts), prompts.verify_prompt(clip, {}, facts)):
        assert tail in text
        assert 'puts on rune armor' in text
        assert '"wears": {"林齐": "符文甲胄"}' in text
        assert '其他镜头的事件不要求在本镜全部出现' in text


def test_scripted_check_keeps_late_source_and_action_evidence(monkeypatch):
    from novel_manga.application.review import judges
    clip, segments, tail = source_tail_case()
    calls = []
    def ask(parts, *args, **kwargs):
        calls.append(parts[0]['text'])
        return {'scripted': True, 'evidence': tail, 'note': '原文明确写到换装'}
    monkeypatch.setattr(judges.model_client, 'ask_json', ask)
    judges.script_check(clip, {'story_issue': '原文没有穿甲拿剑'}, segments)
    assert tail in calls[0]
    assert 'puts on rune armor' in calls[0]


def test_correction_instruction_keeps_late_source_event(monkeypatch):
    from novel_manga.application.review import judges
    _, segments, tail = source_tail_case()
    calls = []
    def ask(parts, *args, **kwargs):
        calls.append(parts[0]['text'])
        return {'instruction': '林齐穿着符文甲胄走出洞穴。'}
    monkeypatch.setattr(judges.model_client, 'ask_json', ask)
    judges.instruction_for('检查穿戴动作', '林齐穿甲拿剑', segments['s1'])
    assert tail in calls[0]


def test_scripted_exception_is_bound_to_current_shot_not_later_source_event(monkeypatch):
    from novel_manga.application.review import judges
    clip = {'prompt': '主要事件是甲喝茶。\n', 'prompt_h3': 'A man drinks tea with ordinary hands.',
            'segment_ids': ['whole-chapter']}
    source = {'whole-chapter': '甲喝茶，双手完好。' + '他等待来信。' * 300 + '夜深后，他的掌心裂开一张嘴。'}
    calls = []
    def ask(parts, *args, **kwargs):
        calls.append(parts[0]['text'])
        return {'scripted': False, 'evidence': '', 'note': '掌心长嘴属于后续场景'}
    monkeypatch.setattr(judges.model_client, 'ask_json', ask)
    answer = judges.script_check(clip, {'defect_issue': '喝茶时掌心出现嘴巴'}, source)
    assert answer['scripted'] is False
    assert clip['prompt_h3'] in calls[0]
    assert '后续或其他镜头的异象不能为当前镜头豁免' in calls[0]


def test_six_character_review_includes_six_actual_cards_and_four_frames(tmp_path, monkeypatch):
    from PIL import Image
    from novel_manga.models.bible import Character
    from novel_manga.application.review import evidence
    book = tmp_path/'book'; work = book/'book_1/work/review/clip_01'
    work.mkdir(parents=True)
    names = list('甲乙丙丁戊己')
    refs = []
    for i, name in enumerate(names):
        path = f'card-{i}.jpg'; Image.new('RGB', (50, 80), (i*40, 0, 0)).save(book/path)
        refs.append({'name': name, 'role': 'character', 'path': path})
    frames_seen = []
    def frames(video, directory, count):
        frames_seen.append(count)
        paths = [directory/f'frame_{i}.jpg' for i in range(count)]
        for p in paths: Image.new('RGB', (80, 50)).save(p)
        return paths
    monkeypatch.setattr(evidence, 'clip_frames', frames)
    bible = StoryBible(novel_title='n', genre='g', visual_style='3d', palette='', style_fingerprint='x',
                       characters=[Character(name=n, appearance='青年', wardrobe='外套') for n in names])
    parts, info = evidence.collect_clip_evidence({'cast': names, 'references': refs}, book/'video.mp4', bible, work)
    assert frames_seen == [4]
    assert sum(p['type'] == 'image_url' for p in parts) <= 8
    assert all(name in ''.join(info.legend) for name in names)
    assert '己（Subject 6）' in info.legend[2]
    assert info.legend[3].startswith('图4=视频第1帧')
    assert len(list(work.glob('reference-sheet-*.jpg'))) == 3


@pytest.mark.parametrize('selection,expected', [
    ('base', 'blue coat'), ('old_phase', 'red coat'), ('current_phase', 'gold coat'),
    ('path_only', 'red coat'), ('missing_first', 'blue coat'), ('chapter_fallback', 'gold coat'),
    ('packing_base_override', 'blue coat'), ('closed_card', 'blue coat'),
])
def test_review_description_matches_the_card_actually_shown(tmp_path, monkeypatch, selection, expected):
    from novel_manga.models.bible import Character
    from novel_manga.application.review import evidence
    from novel_manga.review import prompts
    from novel_manga.util import atomic_write_json
    book = tmp_path/'book'; work = book/'book_7/work/review/clip_01'
    work.mkdir(parents=True)
    phases = [
        {'from': 7, 'to': 7, 'asset_id': 'character_001-new', 'base_costume': 'gold coat'},
        {'from': 7, 'to': 7, 'asset_id': 'character_001-old', 'base_costume': 'red coat'},
    ]
    atomic_write_json(book/'series_assets/phases.json', {'characters': {'甲': phases}})
    refs = {}
    for name in ('character_001', 'character_001-old', 'character_001-new'):
        path = f'series_assets/characters/{name}/turnaround.jpeg'
        (book/path).parent.mkdir(parents=True)
        (book/path).write_bytes(b'card')
        refs[name] = {'name': '甲', 'role': 'character', 'asset_id': name, 'path': path}
    if selection == 'base': selected = [refs['character_001']]
    elif selection == 'old_phase': selected = [refs['character_001-old']]
    elif selection == 'current_phase': selected = [refs['character_001-new']]
    elif selection == 'path_only':
        selected = [{k: v for k, v in refs['character_001-old'].items() if k != 'asset_id'}]
    elif selection == 'missing_first':
        selected = [{**refs['character_001-new'], 'path': 'missing/turnaround.jpeg'}, refs['character_001']]
    elif selection == 'closed_card':
        path = 'series_assets/characters/character_001/closed.jpeg'
        (book/path).write_bytes(b'closed card')
        selected = [{**refs['character_001'], 'path': path}]
    else: selected = []
    bible = StoryBible(novel_title='n', genre='g', visual_style='3d', palette='', style_fingerprint='x',
        characters=[Character(name='甲', appearance='same person', wardrobe='blue coat')], locations=['房间：木门'])
    original = copy.deepcopy(bible.model_dump())
    images = []
    monkeypatch.setattr(evidence.model_client, 'image_part',
                        lambda path, *args: images.append(path) or {'type': 'image_url', 'path': str(path)})
    monkeypatch.setattr(evidence, 'clip_frames', lambda *args: [])
    clip = {'cast': ['甲'], 'references': selected, 'prompt_h3': 'A person waits.'}
    if selection == 'packing_base_override':
        from novel_manga.application.packing.service import clip_entry
        shot = {'index': 1, 'characters': ['甲'], 'in_frame': ['甲'], 'location': '房间', 'turns': [],
                'visual_prompt': '甲在房间等待', 'motion_prompt': '甲抬眼', 'end_state': '甲仍在等待',
                'shot_scale': '中景', 'camera': '固定', 'light': '室内灯'}
        ctx = {'episode_dir': work.parents[2], 'bible': bible, 'location_map': {'房间': '房间：木门'},
               'overrides': {}, 'grammar': None, 'frame': None}
        clip = clip_entry({'kind': 'video', 'location': '房间', 'seconds': 10, 'shots': [shot]},
                          'clip_01', ctx, {'reference_paths': {'甲': refs['character_001']['path']}})
        ref = next(r for r in clip['references'] if r.get('role') == 'character')
        assert ref['asset_id'] == 'character_001-new' and ref['path'] == refs['character_001']['path']
    for scene_ref in clip.get('references', []):
        if scene_ref.get('role') == 'location':
            from PIL import Image
            image = book/scene_ref['path']; image.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (80, 50), 'gray').save(image)
    original_clip = copy.deepcopy(clip)
    for verify in (False, True):
        _, info = evidence.collect_clip_evidence(clip, book/'video.mp4', bible, work, verify=verify)
        assert info.by_name['甲'].wardrobe == expected
        text = prompts.verify_prompt(clip, {}, info) if verify else prompts.classic_prompt(clip, {}, '', info)
        assert expected in text
        if selection != 'packing_base_override':
            assert all(coat not in text for coat in ('blue coat', 'red coat', 'gold coat') if coat != expected)
    from novel_manga.application.review.verify import Verifier
    verifier = object.__new__(Verifier)
    verifier.novel = book
    verifier.bible = bible
    verifier.location_time = {}
    verifier._segments = {}
    cards, text = verifier.prompt_for(clip, work.parents[2], 7, '')
    assert expected in text
    if selection != 'packing_base_override':
        assert all(coat not in text for coat in ('blue coat', 'red coat', 'gold coat') if coat != expected)
    wanted = {'blue coat': 'character_001', 'red coat': 'character_001-old', 'gold coat': 'character_001-new'}[expected]
    wanted_path = selected[0]['path'] if selection == 'closed_card' else refs[wanted]['path']
    assert cards == [('甲', Path(wanted_path))]
    assert all(path == book/wanted_path or path.name == 'selected-scene-reference.jpg' for path in images)
    assert any(path == book/wanted_path for path in images)
    assert bible.model_dump() == original
    assert clip == original_clip


@pytest.mark.parametrize('cast_size,card_count', [(3,3),(4,2)])
def test_current_verifier_keeps_its_card_budget_and_crowd_scope(tmp_path, monkeypatch, cast_size, card_count):
    import json
    from novel_manga.models.bible import Character
    from novel_manga.application.review import evidence
    from novel_manga.application.review.verify import CurrentVerifier
    from novel_manga.review.contracts import POLICY, MAX_IMAGES
    book=tmp_path/'book'; ep=book/'book_1'; ep.mkdir(parents=True)
    names=list('甲乙丙丁')[:cast_size]
    refs=[]
    for name in names+['群众']:
        path=book/f'{name}/closed.jpeg';path.parent.mkdir();path.write_bytes(b'card')
        refs.append({'role':'character','name':name,'path':str(path.relative_to(book))})
    clip={'clip_id':'clip_01','kind':'video','cast':names+['群众'],'references':refs,
          'crowd_roles':{'群众':{'count':2}},'segment_ids':[]}
    video=ep/'selected.mp4';video.write_bytes(b'selected take')
    (ep/'clip_plan.json').write_text(json.dumps({'clips':[clip]}))
    (ep/'thin_media_report.json').write_text(json.dumps({'clips':[{'clip_id':'clip_01','selected':{'video':str(video)}}]}))
    verifier=object.__new__(CurrentVerifier)
    verifier.novel=book;verifier.prefix='book';verifier.frames=tmp_path/'private-frames'
    verifier.bible=StoryBible(novel_title='n',genre='g',visual_style='3d',palette='',style_fingerprint='x',
        characters=[Character(name=name,appearance='same person',wardrobe='blue coat') for name in names+['群众']])
    verifier.location_time={};verifier._segments={};verifier.repair_advice=False
    verifier.max_tokens=321;verifier.model_settings=object();verifier.judge_tag='test'
    images=[];frame_counts=[];requests=[]
    monkeypatch.setattr(evidence,'clip_frames',lambda v,work,count: frame_counts.append((v,count)) or [])
    monkeypatch.setattr(evidence.model_client,'image_part',lambda path,*a: images.append(path) or {'type':'image_url','path':str(path)})
    def ask(parts,schema,**kwargs):
        requests.append((parts,schema,kwargs))
        return {'verdict':'fine','people':[]}
    monkeypatch.setattr(evidence.model_client,'ask_json',ask)
    record=verifier.verify((1,'clip_01','original claim','managed'))
    assert 'error' not in record
    assert images==[book/f'{name}/closed.jpeg' for name in names[:card_count]]
    assert frame_counts==[(video,MAX_IMAGES-card_count)]
    assert len(requests)==1 and requests[0][2]['settings'] is verifier.model_settings
    assert requests[0][2]['max_tokens']==321
    assert '2名不同的群众' in requests[0][0][-1]['text']
    assert record['policy']==POLICY and record['review_mode']=='verify'
    assert record['video']==str(video) and record['claim']=='original claim'


def answer(**changes):
    return dict(people=[], same_person_twice=False, species_or_gender_wrong=False,
                action_by_wrong_person=False, actor_missing=False, lead_face_swapped=False,
                ghost_text=False, verdict='fine', evidence='', instruction='', **changes)


def test_unchecked_dimensions_do_not_become_passed():
    verdict = verify_to_verdict(answer())
    for field in ['location_ok', 'time_of_day_ok', 'chat_text_ok', 'visual_defects', 'story_ok']:
        assert verdict[field] is None
    assert verdict['severity'] == 'pass'


def test_extra_body_is_not_reported_as_wrong_action_ownership():
    raw = answer(); raw.update(same_person_twice=True, verdict='obvious', evidence='出现两个相同的人')
    verdict = verify_to_verdict(raw)
    assert verdict['identity_ok'] is False
    assert verdict['story_kind'] != '动作落在错误的人物身上'
    bible = StoryBible(novel_title='t', genre='g', visual_style='2d', palette='', style_fingerprint='t')
    assert fix_tier(verdict, bible) == 'must_fix'
    assert fix_tier({**verdict, 'scripted': {'evidence': '原文明确分身'}}, bible) == 'optional'


def test_verify_receives_the_selected_scene_card_without_losing_character_or_frame_evidence(tmp_path,monkeypatch):
    from PIL import Image
    from novel_manga.models.bible import StoryBible,Character
    from novel_manga.application.review import evidence
    book=tmp_path/'book';work=book/'book_1/work/review/c';work.mkdir(parents=True)
    for name in ['person.jpg','street.jpg']:Image.new('RGB',(50,80),'white').save(book/name)
    requested=[]
    def frames(video,directory,count):
        requested.append(count);paths=[directory/f'f{i}.jpg'for i in range(count)]
        for p in paths:Image.new('RGB',(80,50)).save(p)
        return paths
    monkeypatch.setattr(evidence,'clip_frames',frames)
    bible=StoryBible(novel_title='n',genre='g',visual_style='3d',palette='',style_fingerprint='x',characters=[Character(name='甲',appearance='青年',wardrobe='外套')])
    clip={'cast':['甲'],'location':'街道','references':[{'name':'甲','role':'character','path':'person.jpg'},
          {'name':'街道','role':'location','path':'street.jpg'}]}
    parts,info=evidence.collect_clip_evidence(clip,book/'v.mp4',bible,work,verify=True)
    assert any('地点参考' in x and '街道' in x for x in info.legend)
    assert any('角色卡' in x and '甲' in x for x in info.legend)
    assert requested==[6] and len([p for p in parts if p['type']=='image_url'])<=8
    assert len([x for x in info.legend if '视频第' in x])==6
