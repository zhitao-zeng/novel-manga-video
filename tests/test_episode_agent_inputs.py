import json
from pathlib import Path
from unittest.mock import patch

import pytest

from novel_manga.application.agents.episode import context
from novel_manga.application.agents.episode.inputs import prepare_inputs
from novel_manga.application.agents.episode.session import SKILL_SOURCE


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(context, 'configured_root', lambda: tmp_path / 'project')
    source = tmp_path / 'originals/another-book'
    episode = source / 'another-book_2'
    episode.mkdir(parents=True)
    raw = '第一章 过去\n旧事。\n\n第二章 相逢\n林舟在书房说：“快走。”'
    text = tmp_path / 'novel.txt'
    text.write_text(raw, encoding='utf-8')
    start = raw.index('第二章')
    write(source / 'novel.json', {'novel_id': source.name, 'title': '另一本书', 'source': str(text),
        'chapters': [{'index': 1, 'title': '第一章 过去', 'start': 0, 'end': start},
                     {'index': 2, 'title': '第二章 相逢', 'start': start, 'end': len(raw)}]})
    profile = {'frame': '9:16', 'resolution': '1080p', 'speech_gate': 'enforce',
               'inner_voice_delivery': 'native', 'qc_ignore': [], 'asr_threshold': .15}
    write(source / 'profile.json', profile)
    write(source / 'story_bible.json', {'novel_title': '另一本书', 'genre': '现代',
        'visual_style': '2D', 'palette': '蓝', 'style_fingerprint': 'existing',
        'characters': [{'name': '林舟', 'appearance': '黑发', 'wardrobe': '白衬衫'}],
        'locations': ['书房：旧窗']})
    write(source / 'bible_aliases.json', {'小林': '林舟'})
    character = 'series_assets/characters/character_009/turnaround.jpeg'
    setting = 'series_assets/locations/location_006/establishing.jpeg'
    for name in (character, setting, 'series_assets/voices/林舟.wav'):
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'original asset ' + name.encode())
    write(source / 'series_assets/manifest.json', {'characters': [{'asset_id': 'character_009',
        'name': '林舟', 'primary_image': character}], 'locations': [{'asset_id': 'location_006',
        'name': '书房：旧窗', 'primary_image': setting}]})
    write(source / 'series_assets/voices/voices.json', {'林舟': {'seconds': 12}})
    write(episode / 'segments.json', [{'segment_id': 'original-2-1', 'text': '林舟在书房说：“快走。”'}])
    write(episode / 'identity_source_reading.json', {'actors': [{'name': '林舟', 'quote': '林舟'}]})
    return source, episode, tmp_path


def saved_script(source, episode, *, count=1, seconds=12):
    script = {'video_title': '已定稿', 'shots': [{'index': i, 'characters': ['林舟'],
        'visual_prompt': '林舟坐在书房。', 'motion_prompt': '林舟转头。',
        'turns': [{'speaker_name': '林舟', 'text': '快走。', 'delivery_mode': 'visible_dialogue'}]}
        for i in range(1, count + 1)]}
    refs = [{'tag': '@图片1', 'role': 'location', 'name': '书房', 'asset_id': 'location_006',
             'path': 'series_assets/locations/location_006/establishing.jpeg'},
            {'tag': '@图片2', 'role': 'character', 'name': '林舟', 'asset_id': 'character_009',
             'path': 'series_assets/characters/character_009/turnaround.jpeg'}]
    plan = {'limits': {'max_clip_seconds': 15}, 'clips': [{'clip_id': f'clip_{i:02d}',
        'kind': 'video', 'shot_indexes': [i], 'request_seconds': seconds, 'references': refs,
        'prompt_h3': 'Existing final English.'} for i in range(1, count + 1)]}
    write(episode / 'chapter_script.json', script)
    write(episode / 'clip_plan.json', plan)
    return script, plan


def test_source_reads_registered_offsets_and_preserves_source_and_profile(book):
    source, episode, _ = book
    metadata = read(source / 'novel.json')
    metadata['chapters'][0]['end'] = 999999  # unrelated chapter must not invalidate this chapter
    write(source / 'novel.json', metadata)
    before = {str(p): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    ctx = context.create_context(source, 2)
    result = prepare_inputs(ctx)
    assert ctx.input_mode == 'source' and result['target_seconds'] == [120, 150]
    assert result['source_complete'] and '旧事' not in (ctx.work_dir / 'input/source.txt').read_text()
    assert read(ctx.novel_dir / 'profile.json') == read(source / 'profile.json')
    assert result['reading'] == ['input/reading/identity_source_reading.json']
    assert before == {str(p): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    assert ctx.novel_dir != source and ctx.episode_dir.parent == ctx.novel_dir
    assert (ctx.novel_dir / 'series_assets').resolve() == ctx.asset_snapshot_dir / 'series_assets'


def test_manifest_resources_are_readable_in_the_episode_mount_and_use_current_code_skill(book):
    source, _, _ = book
    ctx = context.create_context(source, 2)
    outdated = ctx.project_root / 'skills/episode-production/SKILL.md'
    outdated.parent.mkdir(parents=True)
    outdated.write_text('outdated runtime-root skill', encoding='utf-8')
    result = prepare_inputs(ctx)
    installed = ctx.work_dir / result['skill']
    assert installed.read_bytes() == (SKILL_SOURCE / 'SKILL.md').read_bytes()
    assert result['format_reference'] == result['skill_references']['h3_format']
    assert result['runtime_rules'] == result['skill_references']['review_and_repair']
    for name in result['skill_references'].values():
        path = ctx.work_dir / name
        assert path.is_file() and path.stat().st_size > 0
        assert path.resolve().is_relative_to(ctx.work_dir.resolve())
    guide = ctx.work_dir / result['skill_references']['official_h3_guide']
    assert guide.read_bytes() == (SKILL_SOURCE / 'references/official/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md').read_bytes()
    assert (guide.parent / 'LICENSE').is_file() and (guide.parent / 'NOTICE').is_file()
    assert not any('production-lessons' in str(path) or path.suffix == '.log'
                   for path in installed.parent.rglob('*'))


def test_refreshing_skill_resources_preserves_frozen_inputs_output_and_budget(book):
    source, episode, _ = book
    saved_script(source, episode)
    ctx = context.create_context(source, 2)
    first = prepare_inputs(ctx)
    protected = {path: path.read_bytes() for path in (ctx.work_dir / 'input').rglob('*') if path.is_file()}
    write(ctx.work_dir / 'output/requests.json', {'prompts': {'clip_01': 'agent work'}})
    write(ctx.episode_dir / 'repair_history/history.json', {'trials': [{'id': 1, 'managed': True}]})
    core = ctx.work_dir / first['skill']
    core.write_text('earlier installed rules', encoding='utf-8')
    second = prepare_inputs(ctx)
    assert second['skill_references'] == first['skill_references']
    assert core.read_bytes() == (SKILL_SOURCE / 'SKILL.md').read_bytes()
    assert all(path.read_bytes() == value for path, value in protected.items())
    assert read(ctx.work_dir / 'output/requests.json') == {'prompts': {'clip_01': 'agent work'}}
    assert read(ctx.episode_dir / 'repair_history/history.json') == {'trials': [{'id': 1, 'managed': True}]}


def test_script_inputs_freeze_existing_words_groups_seconds_order_and_long_total(book):
    source, episode, _ = book
    script, plan = saved_script(source, episode, count=14)
    ctx = context.create_context(source, 'another-book_2')
    result = prepare_inputs(ctx)
    assert ctx.input_mode == 'script' and result['target_seconds'] == [168, 168]
    assert read(ctx.work_dir / 'input/chapter_script.json') == script
    assert read(ctx.work_dir / 'input/clip_plan.json') == plan
    assert read(ctx.episode_dir / 'clip_plan.json') == plan
    assert result['output'] == 'output/requests.json'
    assert result['authoritative_inputs'] == ['input/chapter_script.json', 'input/clip_plan.json']
    assert read(episode / 'chapter_script.json') == script and read(episode / 'clip_plan.json') == plan


def test_explicit_source_excludes_old_script_and_requests(book):
    source, episode, _ = book
    saved_script(source, episode)
    ctx = context.create_context(source, 2, input_mode='source', target_seconds=(90, 110))
    result = prepare_inputs(ctx)
    assert result['target_seconds'] == [90, 110] and not result['authoritative_inputs']
    assert not (ctx.work_dir / 'input/chapter_script.json').exists()
    assert not (ctx.work_dir / 'input/clip_plan.json').exists()
    assert not (ctx.episode_dir / 'chapter_script.json').exists()
    assert not (ctx.episode_dir / 'clip_plan.json').exists()
    assert all('prompt_h3' not in row for row in read(ctx.run_dir / 'catalog.json'))


def test_shared_assets_copied_once_and_resume_does_not_reset_budget(book):
    source, episode, _ = book
    ctx = context.create_context(source, 2)
    import shutil
    real_copy = shutil.copy2
    with patch('novel_manga.application.agents.episode.inputs.shutil.copy2', wraps=real_copy) as copies:
        prepare_inputs(ctx)
        write(ctx.episode_dir / 'repair_history/history.json', {'trials': [{'id': 1, 'managed': True}]})
        (source / 'another-book_3').mkdir()
        write(source / 'another-book_3/segments.json', [{'segment_id': 's3', 'text': '林舟来到书房。'}])
        other = context.create_context(source, 3)
        prepare_inputs(other)
        prepare_inputs(ctx)
    original = source / 'series_assets/characters/character_009/turnaround.jpeg'
    assert sum(Path(call.args[0]) == original for call in copies.call_args_list) == 1
    assert other.work_dir != ctx.work_dir and other.novel_dir != ctx.novel_dir
    assert other.asset_snapshot_dir == ctx.asset_snapshot_dir
    assert read(ctx.episode_dir / 'repair_history/history.json') == {'trials': [{'id': 1, 'managed': True}]}


def test_missing_declared_reference_is_reported_without_generation(book):
    source, episode, _ = book
    saved_script(source, episode)
    (source / 'series_assets/characters/character_009/turnaround.jpeg').unlink()
    result = prepare_inputs(context.create_context(source, 2))
    assert not result['ready']
    assert any(r['name'] == '林舟' and r['role'] == 'character' for r in result['missing_assets'])


def test_unavailable_raw_source_uses_explicit_segments_fallback(book):
    source, episode, _ = book
    data = read(source / 'novel.json'); data['source'] = str(source / 'missing.txt')
    write(source / 'novel.json', data)
    ctx = context.create_context(source, 2)
    result = prepare_inputs(ctx)
    assert result['source_kind'] == 'segments_fallback' and not result['source_complete']
    assert (ctx.work_dir / 'input/source.txt').read_text() == '林舟在书房说：“快走。”'
    assert result['source_error'].startswith('FileNotFoundError:')


def test_saved_thirty_second_clip_requires_repacking_without_changing_source(book):
    source, episode, _ = book
    script, plan = saved_script(source, episode, seconds=30)
    ctx = context.create_context(source, 2)
    with pytest.raises(ValueError, match='H3 requires repacking'):
        prepare_inputs(ctx)
    assert read(episode / 'chapter_script.json') == script
    assert read(episode / 'clip_plan.json') == plan


def test_missing_plan_uses_existing_pure_packer_at_h3_cap(tmp_path, monkeypatch):
    from support.split_episode import split_episode, explicitly_planned_shots
    source_episode, script, _ = split_episode.__wrapped__(tmp_path, monkeypatch)
    script = explicitly_planned_shots(script)
    write(source_episode / 'chapter_script.json', script)
    write(source_episode / 'segments.json', [{'segment_id': 'seg_1', 'text': '林凡说话。'}])
    monkeypatch.setattr(context, 'configured_root', lambda: tmp_path / 'project')
    ctx = context.create_context(source_episode.parent, 1)
    with patch('novel_manga.llm.client.ask_json', side_effect=AssertionError('unexpected model call')):
        result = prepare_inputs(ctx)
    packed = read(ctx.episode_dir / 'clip_plan.json')
    assert packed['limits']['max_clip_seconds'] == 15
    assert all(c['request_seconds'] <= 15 for c in packed['clips'])
    assert result['target_seconds'] != [120, 150]
    assert read(source_episode / 'chapter_script.json') == script
    assert not (source_episode / 'clip_plan.json').exists()


def test_absolute_scene_reference_is_frozen_and_remains_usable_on_resume(book):
    source, episode, _ = book
    _, plan = saved_script(source, episode)
    frame = episode / 'work/state/frame.jpg'
    frame.parent.mkdir(parents=True);frame.write_bytes(b'original scene frame')
    plan['clips'][0]['references'].append({'role': 'scene_state', 'name': '现状',
        'asset_id': 'scene_1', 'path': str(frame), 'subjects': ['林舟']})
    write(episode / 'clip_plan.json', plan)
    ctx = context.create_context(source, 2)
    assert prepare_inputs(ctx)['ready']
    frozen = read(ctx.episode_dir / 'clip_plan.json')['clips'][0]['references'][-1]['path']
    assert not Path(frozen).is_absolute() and (ctx.novel_dir / frozen).read_bytes() == b'original scene frame'
    frame.unlink()
    assert prepare_inputs(ctx)['ready']
    assert (ctx.novel_dir / frozen).read_bytes() == b'original scene frame'
