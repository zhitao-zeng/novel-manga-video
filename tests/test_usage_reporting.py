import json
import shutil
from pathlib import Path
from novel_manga.reporting.usage import summarize, episode_rows
from novel_manga.util import atomic_write_json


def take(directory, cid, number, seconds, task):
    folder = directory / 'work/clips' / cid / f'attempt_{number:02}'
    folder.mkdir(parents=True)
    video = folder / 'clip.mp4'; video.write_bytes(b'fixed video')
    atomic_write_json(folder / 'request.json', {'duration': seconds})
    atomic_write_json(folder / 'clip.mp4.task.json', task)
    return {'video': str(video), 'duration': seconds}


def test_mixed_backends_and_retries_are_counted_once_and_priced_separately(tmp_path):
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    a = take(directory, 'a', 1, 15, {'task_id': 'a', 'model': 'sd2.0', 'resolution': '480p', 'usage': {'total_tokens': 100}})
    b = take(directory, 'b', 1, 10, {'task_id': 'b', 'model': 'sd2.5', 'resolution': '720p', 'usage': {'total_tokens': 200}})
    c = take(directory, 'c', 1, 15, {'task_id': 'c', 'model': 'h3', 'local': True, 'seconds': 15})
    atomic_write_json(directory / 'thin_media_report.json', {'clips': [
        {'attempts': [a, a], 'selected': a}, {'attempts': [b], 'selected': b}, {'attempts': [c], 'selected': c}],
        'assembly': {'duration': 45}})
    rates = {'video_per_second': {'sd2.0': {'480p': 2}, 'sd2.5': {'720p': 3}}}
    result = summarize(novel, rates)
    assert result['video_seconds'] == 40 and result['final_seconds'] == 45
    assert result['video_tokens'] == 300 and result['cost'] == 60
    assert sum(g['attempts'] for g in result['groups']) == 3
    assert next(g for g in result['groups'] if g['provider'] == 'local_h3')['api_cost'] == 0
    assert result['local_compute_cost'] is None and not result['history_complete']


def test_archived_copy_is_not_a_second_generation(tmp_path):
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    selected = take(directory, 'a', 1, 15, {'task_id': 'a', 'model': 'sd2.5', 'resolution': '720p'})
    original = Path(selected['video']); archive = directory / 'repair_history/takes/a/saved/clip.mp4'
    archive.parent.mkdir(parents=True); shutil.copy2(original, archive)
    shutil.copy2(original.parent / 'request.json', archive.parent / 'request.json')
    atomic_write_json(directory / 'thin_media_report.json', {'clips': [{'attempts': [selected], 'selected': selected}]})
    atomic_write_json(directory / 'repair_history/history.json', {'trials': [{'before': {'a': {
        'selected': selected, 'archive': {'video': str(archive), 'original_video': str(original)}}}}]})
    result = summarize(novel)
    assert result['video_seconds'] == 15 and sum(g['attempts'] for g in result['groups']) == 1


def test_pending_tasks_and_unknown_resolution_are_not_invented_costs(tmp_path):
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    selected = take(directory, 'a', 1, 15, {'task_id': 'a', 'model': 'sd2.5'})
    atomic_write_json(directory / 'work/clips/b/attempt_01/clip.mp4.task.json', {'task_id': 'pending', 'model': 'sd2.5'})
    atomic_write_json(directory / 'thin_media_report.json', {'clips': [{'attempts': [selected], 'selected': selected}]})
    atomic_write_json(novel / 'profile.json', {'tier': 'quality'})
    result = summarize(novel, {'video_per_second': {'720p': 1}})
    assert result['cost'] is None and result['unpriced_groups'] == 1
    assert result['groups'][0]['resolution'] == 'unknown'
    assert result['coverage']['missing_tokens'] == 1
    assert sum(g['attempts'] for g in result['groups']) == 1


def test_history_take_does_not_inherit_replacement_task(tmp_path):
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    selected = take(directory, 'a', 1, 15, {'task_id': 'replacement', 'model': 'h3', 'local': True})
    old = {'video': selected['video'], 'take': [1, 1]}
    atomic_write_json(directory / 'repair_history/history.json', {'trials': [{'renders': [
        {'clips': {'a': {'generated_takes': [old]}}}]}]})
    result = summarize(novel)
    assert sum(g['attempts'] for g in result['groups']) == 2
    unknown = next(g for g in result['groups'] if g['provider'] == 'unknown')
    assert unknown['missing_seconds'] == 1 and unknown['without_task_id'] == 1


def test_chapter_selection_and_shared_images_have_explicit_scope(tmp_path):
    novel = tmp_path / 'book'
    take(novel / 'book_1', 'a', 1, 15, {'task_id': 'a', 'model': 'sd2.5'})
    take(novel / 'book_2', 'b', 1, 10, {'task_id': 'b', 'model': 'sd2.0'})
    assets = tmp_path / 'owner/series_assets'; assets.mkdir(parents=True)
    (novel / 'series_assets').symlink_to(assets, target_is_directory=True)
    result = summarize(novel, chapters={2})
    assert result['episodes'] == 1 and result['video_seconds'] == 10
    assert result['shared_assets'] == 'owner' and result['images'] == 0
    assert result['image_scope'] == 'whole_novel'
