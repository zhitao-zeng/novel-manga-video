"""Confirmed handoff failures; each strict xfail is removed by its dedicated fix."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import novel_manga.application.packing.assets as packing
import novel_manga.application.rendering.flow as flow
from novel_manga.story.h3 import request_issues
from novel_manga.config import Settings
from novel_manga.media.adapters import FramedPhanRouter


def test_person_declaration_is_a_valid_subject_definition():
    clip = {'prompt_h3': 'subject_definitions:\n<Subject 1> is the person shown in <Picture 1>.\n'
                        'detailed_description:\n[Shot 1] <Subject 1> opens the door.'}
    assert request_issues(clip) == []


def test_subject_mentions_without_declarations_still_fail():
    clip = {'prompt_h3': 'subject_definitions:\n<Picture 1> shows a room.\nsummary:\n'
                        'detailed_description:\n<Subject 1> is the character walking inside.'}
    assert request_issues(clip) == ['subject 1 has no identity definition']


def test_moderation_edit_updates_persisted_dialogue_fields(monkeypatch):
    runner = object.__new__(flow.ThinMediaRunner)
    runner.context = SimpleNamespace(bible=SimpleNamespace(characters=[]), settings=SimpleNamespace())
    clip = {'clip_id': 'clip_01', 'prompt': '旧提示', 'lines': [{'speaker_name': '甲', 'text': '原来台词'}],
            'spoken_text': '原来台词', 'dialogue_bindings': [{'stage': 1, 'speaker_name': '甲', 'text': '原来台词'}]}
    saved = []
    runner.save_clip_plan = lambda: saved.append(copy.deepcopy(clip))
    monkeypatch.setattr(flow.moderation_repair, 'repair', lambda *a, **k:
                        ('新提示', [{'old': '原来台词', 'new': '新的对白'}]))
    assert runner.repair_refused_prompt(clip, 2)
    assert saved[0]['spoken_text'] == '新的对白'
    assert saved[0]['dialogue_bindings'][0]['text'] == '新的对白'


def test_dialogue_rewrite_preserves_ownership_and_noop_inputs():
    from novel_manga.story.dialogue import rewritten_dialogue
    clip = {'lines': [{'speaker_name': '甲', 'text': '开门。'}, {'speaker_name': '乙', 'text': '请进。'}],
            'spoken_text': '开门。请进。', 'dialogue_bindings': [
                {'stage': 1, 'source_stage': 7, 'speaker_name': '甲', 'text': '开门。'},
                {'stage': 2, 'source_stage': 9, 'speaker_name': '乙', 'text': '请进。'}]}
    before = copy.deepcopy(clip)
    fields = rewritten_dialogue(clip, [{'old': '开门。', 'new': '开一下门。'}])
    assert clip == before
    assert fields['spoken_text'] == '开一下门。请进。'
    assert fields['dialogue_bindings'][0] == {**before['dialogue_bindings'][0], 'text': '开一下门。'}
    assert fields['dialogue_bindings'][1] == before['dialogue_bindings'][1]
    assert rewritten_dialogue(clip, []) == {}
    assert rewritten_dialogue(clip, [{'old': '不在台词里', 'new': '不新增'}]) == {}
    assert rewritten_dialogue({**clip, **fields}, [{'old': '开门。', 'new': '开一下门。'}]) == {}


def test_seedream_scene_request_uses_landscape_size(tmp_path):
    requests = []
    def post(url, **kwargs):
        requests.append(kwargs['json'])
        return httpx.Response(200, json={'data': [{'url': 'http://unused.invalid/card'}]},
                              request=httpx.Request('POST', url))
    with patch.object(httpx.Client, 'post', side_effect=post), patch('novel_manga.providers.phanrouter_images.download_file'):
        provider = FramedPhanRouter(Settings(image_model='doubao-seedream-4.5'),
                                   {'image_ratio': '16:9', 'video_ratio': '16:9'})
        try:
            provider.create_image('scene', tmp_path / 'establishing.jpeg', aspect_ratio='16:9')
        finally:
            provider.client.close()
    assert requests[0]['size'] == '1920x1080'


def test_body_mapping_refreshes_after_ledger_changes(tmp_path, monkeypatch):
    (tmp_path / 'entity').mkdir()
    path = tmp_path / 'entity/entities.json'
    path.write_text(json.dumps({'body': {'canonical': '旧身体'}}))
    sheet = {'cast': [{'name': '甲', 'body': 'body', 'card': 'old', 'acts_through_other_body': True}]}
    monkeypatch.setattr('novel_manga.application.identity.ledger_views.snapshot', lambda *a: sheet)
    assert packing.bodies_for(tmp_path, 1) == {'甲': ('旧身体', 'old')}
    path.write_text(json.dumps({'body': {'canonical': '新身体'}}))
    sheet['cast'][0]['card'] = 'new'
    assert packing.bodies_for(tmp_path, 1) == {'甲': ('新身体', 'new')}


def test_usage_counts_generated_material_outside_latest_report(tmp_path):
    from cost_report_thin import episode_rows
    novel = tmp_path / 'book'; episode = novel / 'book_1'
    for n in (1, 2):
        attempt = episode / 'work/clips/clip_01' / f'attempt_{n:02d}'
        attempt.mkdir(parents=True)
        video = attempt / 'clip.mp4'; video.write_bytes(b'fixed material')
        (attempt / 'request.json').write_text(json.dumps({'duration': 15}))
        (attempt / 'clip.mp4.task.json').write_text(json.dumps({
            'task_id': f'task-{n}', 'model': 'doubao-seedance-2-5', 'status': 'succeeded'}))
    selected = {'video': str(video), 'duration': 15}
    (episode / 'thin_media_report.json').write_text(json.dumps({
        'clips': [{'attempts': [selected], 'selected': selected}], 'assembly': {'duration': 15}}))
    assert episode_rows(novel, None)[0]['video_seconds'] == 30
