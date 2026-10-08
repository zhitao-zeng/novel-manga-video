"""The same supported episode layouts feed the workbench and voice collector."""
from __future__ import annotations

import json
from pathlib import Path

from novel_manga.application.assets.voices import attributed_chunks
from novel_manga.application.dashboard import workbench
from novel_manga.application.episode_paths import episode_directories
from novel_manga.application import profiles
from novel_manga.util import atomic_write_json


def book(root):
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root/'profile.json', {'frame':'16:9', 'style':'2d'})
    return root


def voiced_episode(root, selector):
    directory=root/f'{root.name}_{selector}';directory.mkdir()
    video=directory/'work/clips/c/attempt_01/clip.mp4';video.parent.mkdir(parents=True)
    video.write_bytes(b'the same selected take')
    atomic_write_json(directory/'clip_plan.json', {'clips':[{'clip_id':'c','lines':[
        {'speaker_name':'甲','text':'这段台词可以作声线素材'}]}]})
    atomic_write_json(directory/'thin_media_report.json', {'clips':[{'clip_id':'c','selected':{
        'video':str(video),'chunks':[{'subtitle':'script_span','matched_lines':'这段台词可以作声线素材',
                                    'start':0,'end':10}]}}]})
    (directory/f'{directory.name}.mp4').write_bytes(b'final')
    return directory


def test_parts_are_listed_opened_and_in_recent_activity(tmp_path):
    (tmp_path/'configs').mkdir()
    atomic_write_json(tmp_path/'configs/pipeline.json',{'novels':[]})
    novel=book(tmp_path/'outputs/book')
    voiced_episode(novel,'12-2');voiced_episode(novel,2);voiced_episode(novel,'12-1')
    listing=workbench.episodes(tmp_path,'book')['episodes']
    assert [r['episode'] for r in listing]==[2,'12-1','12-2']
    assert workbench.episode(tmp_path,'book','12-2')['video']=='book_12-2/book_12-2.mp4'
    assert {r['episode'] for r in workbench.recent(tmp_path)['episodes']}=={2,'12-1','12-2'}


def test_nested_agent_book_can_be_opened_without_aggregating_symlinks(tmp_path):
    (tmp_path/'configs').mkdir()
    atomic_write_json(tmp_path/'configs/pipeline.json',{'novels':[]})
    nested=book(tmp_path/'outputs/episode-agent/book/book_12-source/book')
    directory=voiced_episode(nested,12)
    rows=workbench.books(tmp_path)['books']
    key='episode-agent:book:book_12-source:book'
    assert [r['id'] for r in rows]==[key]
    assert workbench.episodes(tmp_path,key)['episodes'][0]['episode']==12
    detail=workbench.episode(tmp_path,key,12)
    assert workbench.media_file(tmp_path,key,detail['video'])==directory/f'{directory.name}.mp4'


def test_voice_collector_reads_extra_agent_collection_and_deduplicates_takes(tmp_path):
    output=book(tmp_path/'canonical/book')
    runs=tmp_path/'agent/book'
    nested=book(runs/'book_12-source/book')
    directory=voiced_episode(nested,12)
    flat=output/directory.name;flat.symlink_to(directory,target_is_directory=True)
    chunks=attributed_chunks(output,source_dirs=[runs,runs])
    assert len(chunks['甲'])==1 and chunks['甲'][0][0]==10
    assert episode_directories(runs,novel_id='book')==[directory]


def test_voice_collector_does_not_walk_a_books_cache_directories(tmp_path):
    output=book(tmp_path/'book');real=voiced_episode(output,1)
    nested=book(output/'work/old/book');voiced_episode(nested,2)
    assert episode_directories(output)==[real]
    assert len(attributed_chunks(output)['甲'])==1


def test_scoped_speech_policy_applies_to_all_parts_of_its_chapter(tmp_path):
    novel=book(tmp_path/'book')
    atomic_write_json(novel/'profile.json',{'speech_gate':'enforce'})
    atomic_write_json(novel/'repair_manager/state.json',{'scope':{'speech_gate':'observe','episodes':[12]}})
    assert profiles.speech_gate_policy(novel,novel/'book_12-2')=='observe'
    assert profiles.speech_gate_policy(novel,novel/'book_13-1')=='enforce'
