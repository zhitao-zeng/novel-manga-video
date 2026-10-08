"""Episode location variants use the existing supplier, scope and image-bound checks."""
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode.input_assets import build_locations
from novel_manga.application.packing.assets import build_references
from novel_manga.config import Settings
from novel_manga.models.bible import StoryBible
from novel_manga.util import atomic_write_json, read_json


def bible():
    return StoryBible(novel_title='测试', genre='fantasy', visual_style='唯美3D', palette='暖色',
                      style_fingerprint='test-3d', locations=['码头：石质岸边', '车厢：车窗与木制座椅'])


def test_location_phase_preserves_reference_order_and_is_scoped_to_its_chapter(tmp_path):
    book = bible()
    atomic_write_json(tmp_path/'series_assets/phases.json', {'locations': {'车厢': [{
        'from': 7, 'to': 7, 'asset_id': 'location_002-agent-book_7',
        'description': '封闭木质车厢，侧窗透入日光', 'label': '本章车厢内景'}]}})
    locations = {text.split('：')[0]: text for text in book.locations}
    refs, _, binding = build_references([], '车厢', book, locations, novel_dir=tmp_path, chapter=7)
    assert refs[0]['asset_id'] == 'location_002-agent-book_7' and refs[0]['tag'] == '@图片1'
    assert '封闭木质车厢' in binding
    other, _, _ = build_references([], '车厢', book, locations, novel_dir=tmp_path, chapter=8)
    assert other[0]['asset_id'] == 'location_002' and 'phase' not in other[0]


def test_real_recompiler_binds_new_scene_without_changing_saved_cutting_or_dialogue(tmp_path, monkeypatch):
    import copy
    from support.split_episode import split_episode
    from novel_manga.application.repair.flow import rebuild_clips
    directory, script, before = split_episode.__wrapped__(tmp_path, monkeypatch)
    book_path = directory.parent/'story_bible.json'
    book = read_json(book_path)
    book['locations'].append('车厢：封闭的木质车厢与侧窗')
    atomic_write_json(book_path, book)
    atomic_write_json(directory/'chapter_script.json', script)
    atomic_write_json(directory.parent/'series_assets/phases.json', {'locations': {'车厢': [{
        'from': 1, 'to': 1, 'asset_id': 'location_002-agent-scene', 'description': '封闭的木质车厢与侧窗'}]}})
    candidate = copy.deepcopy(script)
    candidate['shots'][0]['location'] = '车厢'
    targets = {c['clip_id'] for c in before['clips']}
    after, changed = rebuild_clips(directory, book_path, candidate, before, targets)
    assert set(changed) == targets
    for old, new in zip(before['clips'], after['clips']):
        assert [(k, new.get(k)) for k in ('clip_id', 'request_seconds', 'shot_indexes', 'shot_timing', 'lines')] == [
            (k, old.get(k)) for k in ('clip_id', 'request_seconds', 'shot_indexes', 'shot_timing', 'lines')]
        assert next(r for r in new['references'] if r['role']=='location')['asset_id'] == 'location_002-agent-scene'


def test_authored_rebuild_retains_three_second_request_and_explicit_cut_metadata(tmp_path, monkeypatch):
    import copy
    from support.split_episode import split_episode
    from novel_manga.application.repair.flow import rebuild_clips
    directory, script, before = split_episode.__wrapped__(tmp_path, monkeypatch)
    atomic_write_json(directory/'chapter_script.json', script)
    before['policy'] = 'episode-agent-authored-v1'
    old = before['clips'][0]
    assert len(old['shot_indexes']) == 1
    old['request_seconds'] = 3
    old['shot_timing'] = [{'seconds': 3, 'cut': 'hard cut'}]
    candidate = copy.deepcopy(script)
    candidate['shots'][0]['light'] = '侧窗透入的日光'
    after, changed = rebuild_clips(directory, directory.parent/'story_bible.json', candidate, before, {old['clip_id']})
    new = after['clips'][0]
    assert old['clip_id'] in changed
    assert new['request_seconds'] == 3 and new['shot_timing'] == old['shot_timing']
    assert new['shot_indexes'] == old['shot_indexes'] and new['lines'] == old['lines']


@pytest.mark.parametrize('relocate',[False,True])
def test_input_rebuild_retains_saved_timing_for_a_standard_pipeline_plan(tmp_path,monkeypatch,relocate):
    import copy
    from support.split_episode import split_episode
    from novel_manga.application.repair.flow import rebuild_clips
    directory,script,before=split_episode.__wrapped__(tmp_path,monkeypatch)
    old=before['clips'][0]
    old['request_seconds']=8
    old['shot_timing']=[{'seconds':8,'cut':'hard cut'}]
    atomic_write_json(directory/'chapter_script.json',script)
    candidate=copy.deepcopy(script);candidate['shots'][0]['light']='侧窗日光'
    if relocate:
        book=read_json(directory.parent/'story_bible.json');book['locations'].append('车厢：木质封闭车厢')
        atomic_write_json(directory.parent/'story_bible.json',book)
        candidate['shots'][0]['location']='车厢'
    after,changed=rebuild_clips(directory,directory.parent/'story_bible.json',candidate,before,
        {c['clip_id'] for c in before['clips']},preserve_timing=True)
    assert old['clip_id'] in changed
    new=after['clips'][0]
    for key in ['request_seconds','shot_timing','shot_indexes','lines']:
        assert new[key]==old[key]


@pytest.mark.parametrize('mode', ['authored_without_card', 'registered_new_card',
                                  'normal_plan', 'authored_with_card', 'unknown_card', 'changed_location'])
def test_recompile_preserves_an_authored_scene_without_a_library_card(tmp_path, monkeypatch, mode):
    import copy
    from support.split_episode import split_episode
    from novel_manga.application.repair.flow import rebuild_clips
    directory, script, before = split_episode.__wrapped__(tmp_path, monkeypatch)
    book_path = directory.parent/'story_bible.json'
    scene = '原文中的无参考图广场'
    for shot in script['shots']:
        shot['location'] = scene
    for clip in before['clips']:
        clip['location'] = scene
        if mode not in {'authored_with_card','unknown_card'}:
            clip['references'] = [r for r in clip['references'] if r['role'] != 'location']
        elif mode == 'unknown_card':
            for ref in clip['references']:
                if ref['role']=='location':ref['name']='未登记的参考图'
    if mode != 'normal_plan':
        before['policy'] = 'episode-agent-authored-v1'
    if mode == 'registered_new_card':
        book = read_json(book_path)
        book['locations'].append(scene+'：开阔的石板广场')
        atomic_write_json(book_path, book)
        atomic_write_json(directory.parent/'series_assets/phases.json', {'locations': {scene: [{
            'from': 1, 'to': 1, 'asset_id': 'location_002-agent-scene', 'description': '空置石板广场'}]}})
    atomic_write_json(directory/'chapter_script.json', script)
    candidate = copy.deepcopy(script)
    candidate['shots'][0]['light'] = '侧方柔和日光'
    if mode == 'changed_location':
        candidate['shots'][0]['location'] = '未注册的新场景'
    old = before['clips'][0]
    if mode in {'normal_plan', 'unknown_card', 'changed_location'}:
        with pytest.raises(KeyError):
            rebuild_clips(directory, book_path, candidate, before, {old['clip_id']})
        return
    after, changed = rebuild_clips(directory, book_path, candidate, before, {old['clip_id']})
    new = after['clips'][0]
    assert old['clip_id'] in changed and new['location'] == scene
    assert after['clips'][1:] == before['clips'][1:]
    for key in ('request_seconds', 'shot_indexes', 'shot_timing', 'lines'):
        assert new[key] == old[key]
    places = [r for r in new['references'] if r['role'] == 'location']
    if mode == 'registered_new_card':
        assert [r['asset_id'] for r in places] == ['location_002-agent-scene']
    elif mode == 'authored_with_card':
        assert places == [r for r in old['references'] if r['role']=='location']
    else:
        assert places == []
        assert '侧方柔和日光' in new['prompt']
        assert [r for r in new['references'] if r['role'] == 'character'] == [
            r for r in old['references'] if r['role'] == 'character']


@pytest.mark.parametrize('mode',['authored','normal','selected_card','speaking_extra'])
def test_authored_unpictured_extras_do_not_require_an_invented_bible_actor(tmp_path,monkeypatch,mode):
    import copy
    from support.split_episode import split_episode
    from novel_manga.application.repair.flow import rebuild_clips
    directory,script,before=split_episode.__wrapped__(tmp_path,monkeypatch)
    extra='三名护卫'
    script['shots'][0]['characters'].append(extra)
    script['shots'][0].setdefault('in_frame',[]).append(extra)
    script['shots'][0]['visual_prompt']+='。三名护卫站在后方。'
    if mode=='speaking_extra':
        for turn in script['shots'][0]['turns']:turn['speaker_name']=extra
    if mode!='normal':before['policy']='episode-agent-authored-v1'
    before['clips'][0].setdefault('cast',[]).append(extra)
    if mode=='selected_card':
        before['clips'][0]['references'].append({'role':'character','name':extra,'asset_id':'guards','path':'guards.jpeg'})
    atomic_write_json(directory/'chapter_script.json',script)
    candidate=copy.deepcopy(script);candidate['shots'][0]['light']='侧窗日光'
    target=before['clips'][0]['clip_id']
    if mode!='authored':
        with pytest.raises(KeyError):rebuild_clips(directory,directory.parent/'story_bible.json',candidate,before,{target})
        return
    after,changed=rebuild_clips(directory,directory.parent/'story_bible.json',candidate,before,{target})
    clip=after['clips'][0]
    assert target in changed and extra in clip['prompt'] and extra in clip['cast']
    assert all(r.get('name')!=extra for r in clip['references'])
    for key in ['lines','shot_indexes','shot_timing','request_seconds']:
        assert clip[key]==before['clips'][0][key]
    assert after['clips'][1:]==before['clips'][1:]


def test_registered_missing_speaker_reaches_real_recompiler_without_old_cast(tmp_path, monkeypatch):
    import copy
    from support.split_episode import split_episode
    from novel_manga.application.repair.flow import rebuild_clips
    from novel_manga.application.agents.episode.input_assets import register_metadata
    directory, script, before = split_episode.__wrapped__(tmp_path, monkeypatch)
    before['policy'] = 'episode-agent-authored-v1'
    candidate = copy.deepcopy(script)
    actor = '守卫队长'
    candidate['shots'][0].update(characters=[actor], in_frame=[actor], extras=['三名护卫'],
                                visual_prompt='守卫队长站在窗边，三名护卫站在后方')
    for turn in candidate['shots'][0]['turns']:
        turn['speaker_name'] = actor
    atomic_write_json(directory/'chapter_script.json', script)
    target = before['clips'][0]['clip_id']
    with pytest.raises(KeyError):
        rebuild_clips(directory, directory.parent/'story_bible.json', candidate, before, {target}, preserve_timing=True)
    ctx = SimpleNamespace(novel_dir=directory.parent, asset_snapshot_dir=tmp_path/'shared',
                          work_dir=tmp_path/'sandbox')
    image = ctx.asset_snapshot_dir/'series_assets/draft-assets/actor/image.jpeg'
    image.parent.mkdir(parents=True); image.write_bytes(b'accepted test card')
    (ctx.work_dir/'input').mkdir(parents=True)
    register_metadata(ctx, [{'role':'character', 'name':actor, 'asset_id':'draft_character_0001',
        'path':str(image.relative_to(ctx.asset_snapshot_dir)),
        'design':{'appearance':'成年男子', 'wardrobe':'守卫服'}}])
    after, changed = rebuild_clips(directory, directory.parent/'story_bible.json', candidate, before, {target}, preserve_timing=True)
    new = after['clips'][0]
    assert changed == [target]
    assert [r['name'] for r in new['references'] if r['role']=='character'] == [actor]
    assert all(r['speaker_name']==actor for r in new['lines'])
    assert [r['text'] for r in new['lines']] == [r['text'] for r in before['clips'][0]['lines']]
    for field in ('request_seconds','shot_indexes','shot_timing'):
        assert new[field] == before['clips'][0][field]
    assert after['clips'][1:] == before['clips'][1:]


@pytest.fixture
def scene_factory(tmp_path, monkeypatch):
    from novel_manga.application import profiles
    from novel_manga.media import adapters, asset_builder, asset_style, card_check
    ctx = SimpleNamespace(novel_dir=tmp_path/'book', run_dir=tmp_path)
    atomic_write_json(ctx.novel_dir/'story_bible.json', bible().model_dump())
    phase = {'from': 7, 'to': 7, 'asset_id': 'location_002-agent-book_7-a', 'base_asset': 'location_002',
             'description': '封闭木质车厢，侧窗透入日光'}
    ledger = ctx.novel_dir/'series_assets/phases.json'
    atomic_write_json(ledger, {'locations': {'车厢': [phase]}})
    monkeypatch.setattr(Settings, 'from_env', lambda **kwargs: Settings())
    monkeypatch.setattr(profiles, 'load_profile', lambda *a: {'frame': '16:9'})
    monkeypatch.setattr(profiles, 'load_genre', lambda *a: {})
    monkeypatch.setattr(profiles, 'load_style', lambda *a: {})
    monkeypatch.setattr(profiles, 'styled_bible', lambda book, *a: book)
    monkeypatch.setattr(asset_style.AssetStyle, 'for_genre', lambda *a, **k: SimpleNamespace(
        location_empty_suffix=' empty scene', dialect_routes_subset=False))
    monkeypatch.setattr(adapters, 'FramedPhanRouter', lambda *a: None)
    draws, checks = [], []
    class Factory:
        def __init__(self, settings, provider, **kwargs):
            assert (settings.width, settings.height) == (1920, 1080)
        def _location_prompt(self, book, location):
            return book.visual_style + ' ' + location
        def ensure_card(self, prompt, output, **kwargs):
            assert '唯美3D' in prompt and kwargs['aspect_ratio'] == '16:9'
            draws.append(str(output)); output.write_bytes(b'simulated empty scene')
    monkeypatch.setattr(asset_builder, 'FramedAssetFactory', Factory)
    monkeypatch.setattr(card_check, 'people_in_card', lambda path: checks.append(str(path)) or {'people_in_scene': 0})
    return ctx, phase, ledger, draws, checks


def test_same_location_picture_and_check_are_reused_after_resume_or_new_reference_id(scene_factory):
    ctx, phase, ledger, draws, checks = scene_factory
    build_locations(ctx, {'车厢'})
    build_locations(ctx, {'车厢'})
    phase['asset_id'] = 'location_002-agent-book_7-b'
    atomic_write_json(ledger, {'locations': {'车厢': [phase]}})
    build_locations(ctx, {'车厢'})
    assert len(draws) == len(checks) == 1
    spec = read_json(ctx.novel_dir/'series_assets/locations'/phase['asset_id']/'spec.json')
    assert spec['reused_from'].endswith('location_002-agent-book_7-a')


def test_scene_check_connection_error_keeps_the_generated_image_for_continuation(scene_factory, monkeypatch):
    from novel_manga.media import card_check
    ctx, _, _, draws, _ = scene_factory
    monkeypatch.setattr(card_check, 'people_in_card', lambda *a: (_ for _ in ()).throw(ConnectionError('offline')))
    with pytest.raises(ConnectionError):
        build_locations(ctx, {'车厢'})
    monkeypatch.setattr(card_check, 'people_in_card', lambda *a: {'people_in_scene': 0})
    build_locations(ctx, {'车厢'})
    assert len(draws) == 1
