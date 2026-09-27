import copy
import json
from pathlib import Path

from novel_manga.story.sources import segment_ids, restore_plan_sources
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair.history import accepted_clip_material


def test_authored_scene_id_does_not_erase_its_source_and_mixed_forms_combine():
    shots = [{'scene_id': 'scene_1', 'segment_id': 's1'},
             {'scene_id': 'scene_2', 'segment_id': 's2', 'source_refs': [{'segment_id': 's2'}, {'segment_id': 's3'}]},
             {'segment_id': 's4', 'source_refs': []}]
    assert segment_ids(shots) == ['s1', 's2', 's3', 's4']


def test_restoring_source_addresses_keeps_requests_and_generation_history_material():
    plan = {'clips': [{'clip_id': 'c1', 'kind': 'video', 'shot_indexes': [1], 'segment_ids': [],
                      'prompt': '中文', 'prompt_h3': 'English', 'references': [], 'request_seconds': 10}]}
    before = copy.deepcopy(plan)
    script = {'shots': [{'scene_id': 'scene_1', 'segment_id': 's1'}]}
    fixed, changed = restore_plan_sources(plan, script)
    assert changed == ['c1'] and fixed['clips'][0]['segment_ids'] == ['s1']
    assert plan == before
    assert plan_fingerprint(plan) == plan_fingerprint(fixed)
    assert h3_prompt_fingerprint(plan) == h3_prompt_fingerprint(fixed)
    assert accepted_clip_material(plan['clips'][0]) == accepted_clip_material(fixed['clips'][0])
    assert restore_plan_sources(fixed, script) == (fixed, [])


def test_restored_source_rechecks_once_but_reuses_current_video_observations(tmp_path, monkeypatch):
    from novel_manga.application.review import episode, judges, cast_video
    from novel_manga.review import contracts, storage
    from novel_manga.models.bible import StoryBible
    book = tmp_path/'book'; directory = book/'book_1';directory.mkdir(parents=True)
    bible = StoryBible(novel_title='测试', genre='g', visual_style='v', palette='p', style_fingerprint='f', characters=[], locations=[])
    (book/'story_bible.json').write_text(bible.model_dump_json())
    video = directory/'v.mp4';video.write_bytes(b'unchanged footage')
    clip = {'clip_id': 'c1', 'kind': 'video', 'shot_indexes': [1], 'segment_ids': []}
    (directory/'clip_plan.json').write_text(json.dumps({'clips': [clip]}))
    (directory/'chapter_script.json').write_text(json.dumps({'shots': [{'scene_id': 'scene_1', 'segment_id': 's1'}]}))
    (directory/'segments.json').write_text(json.dumps([{'segment_id': 's1', 'text': '原文有司机。'}]))
    (directory/'thin_media_report.json').write_text(json.dumps({'clips': [{'clip_id': 'c1', 'selected': {'video': str(video)}}]}))
    raw = {'policy': cast_video.POLICY, 'people': ['甲', '司机']}
    old = {'policy': contracts.POLICY, 'clips': {'c1': {'severity': 'pass', 'video': str(video),
           'take': storage.take_identity(video), 'verify': {'cast_video': raw}}}, 'feedback': {}}
    (directory/'episode_review.json').write_text(json.dumps(old))
    monkeypatch.setenv('NOVEL_REVIEW_CAST_VIDEO', '1')
    calls = []
    def judge(clip, *args, cached_cast=None):
        assert clip['segment_ids'] == ['s1'] and cached_cast == raw
        calls.append(1)
        return {'severity': 'pass', 'verify': {'cast_video': raw}}
    monkeypatch.setattr(judges, 'judge_clip', judge)
    first = episode.review_episode(directory)
    assert first['clips']['c1']['source_segment_ids'] == ['s1'] and calls == [1]
    assert episode.review_episode(directory) == first and calls == [1]
    assert video.read_bytes() == b'unchanged footage'
