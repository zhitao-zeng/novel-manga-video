"""Fixtures for this task's selected character input refresh."""
import json
from pathlib import Path
import pytest
from novel_manga.application.agents.episode import context


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
