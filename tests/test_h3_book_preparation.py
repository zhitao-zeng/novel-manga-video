import json
from pathlib import Path

import pytest

import prepare_h3_book as prep


def episode(tmp_path):
    d = tmp_path / 'book/book_1'
    d.mkdir(parents=True)
    (d / 'chapter_script.json').write_text(json.dumps({'shots': [{'index': 1}]}))
    (d / 'segments.json').write_text(json.dumps([{'segment_id': 'seg_1', 'text': '李明转身告诉王刚，今天要下雨。'}]))
    (d / 'clip_plan.json').write_text(json.dumps({'clips': []}))
    return d


def test_existing_footage_is_not_rewritten(tmp_path, monkeypatch):
    d = episode(tmp_path)
    take = d / 'work/clips/clip_01/attempt_01/clip.mp4'
    take.parent.mkdir(parents=True)
    take.write_bytes(b'original footage')
    before = (d / 'chapter_script.json').read_bytes()
    monkeypatch.setattr(prep, 'audit', lambda *a: pytest.fail('must not audit production footage'))
    assert prep.prepare_one(d)['status'] == 'existing_video'
    assert (d / 'chapter_script.json').read_bytes() == before
    assert take.read_bytes() == b'original footage'


def test_production_admission_prevents_preparation_from_editing_queued_chapters(tmp_path,monkeypatch):
    from novel_manga.util import atomic_write_json
    d=episode(tmp_path)
    atomic_write_json(d.parent/'repair_manager/state.json',{'admitted_episodes':[1]})
    before=(d/'chapter_script.json').read_bytes()
    monkeypatch.setattr(prep,'audit',lambda *a:pytest.fail('production owns this episode'))
    assert prep.prepare_one(d)['status']=='production_owned'
    assert (d/'chapter_script.json').read_bytes()==before


def test_bad_source_is_held_without_model_calls(tmp_path, monkeypatch):
    d = episode(tmp_path)
    p = d.parent / 'h3_preparation/source_blocks.json'
    p.parent.mkdir()
    p.write_text(json.dumps({'1': 'source words are scrambled'}))
    monkeypatch.setattr(prep, 'audit', lambda *a: pytest.fail('must not invent source'))
    assert prep.prepare_one(d)['status'] == 'needs_source'


@pytest.mark.parametrize('quote,stage', [('这是模型编造出来的原文', 1), ('李明转身告诉王刚', 99)])
def test_ungrounded_audit_cannot_authorize_a_rewrite(quote, stage):
    answer = {'issues': [{'kind': 'speaker', 'source_quote': quote, 'stage': stage}]}
    with pytest.raises(ValueError):
        prep.grounded_issues(answer, {'shots': [{'index': 1}]}, [{'text': '李明转身告诉王刚，今天要下雨。'}])


def test_missing_event_can_reference_the_original_without_an_existing_stage():
    answer = {'issues': [{'kind': 'missing_event', 'source_quote': '李明转身告诉王刚', 'stage': 0}]}
    assert prep.grounded_issues(answer, {'shots': [{'index': 1}]}, [{'text': '李明转身告诉王刚，今天要下雨。'}]) == answer['issues']


def test_backup_is_the_original_not_the_latest_rewrite(tmp_path):
    d = episode(tmp_path)
    before = (d / 'chapter_script.json').read_bytes()
    prep.backup(d)
    (d / 'chapter_script.json').write_text('{}')
    prep.backup(d)
    assert (d.parent / 'h3_preparation/before/1/chapter_script.json').read_bytes() == before


def test_english_compilation_does_not_invalidate_source_audit(tmp_path):
    d = episode(tmp_path)
    plan = {'clips': [{'clip_id': 'clip_01', 'kind': 'video', 'prompt': '角色动作', 'references': []}]}
    (d / 'clip_plan.json').write_text(json.dumps(plan))
    before = prep.inputs(d)
    plan['clips'][0]['prompt_h3'] = 'The subject walks.'
    (d / 'clip_plan.json').write_text(json.dumps(plan))
    assert prep.inputs(d) == before


def test_known_identity_updates_invalidate_preparation(tmp_path):
    from novel_manga.util import atomic_write_json
    d = episode(tmp_path)
    before = prep.inputs(d)
    atomic_write_json(d.parent / 'entity/claims.json', [{'status': 'accepted'}])
    assert prep.inputs(d) != before


def test_audit_quote_is_taken_from_raw_source_instead_of_reading_annotations():
    source = [{'segment_id': 'seg_1', 'text': '旧名转身告诉同伴，今天要下雨。'}]
    answer = {'issues': [{'kind': 'speaker', 'stage': 1, 'source_segment': 'seg_1', 'source_quote': ''}]}
    prep.grounded_issues(answer, {'shots': [{'index': 1}]}, source)
    assert answer['issues'][0]['source_quote'] == source[0]['text']


def test_issue_label_cannot_expand_a_local_fix_to_the_whole_chapter():
    assert not prep.needs_full_replan([{'stage':3,'kind':'missing_event'}, {'stage':4,'kind':'location'}])
    assert prep.needs_full_replan([{'stage':0,'kind':'missing_event'}])


def test_failures_retry_with_a_bound_and_do_not_keep_stale_audits(tmp_path):
    d=episode(tmp_path)
    prep.record(d,'starting',attempts=1)
    row=prep.record(d,'error',reason='ReadTimeout',audit={'old':True})
    assert prep.eligible(row,d) and row['retry_after']>0
    clean=prep.record(d,'starting',attempts=2)
    assert 'audit' not in clean and 'reason' not in clean
    row=prep.record(d,'needs_repair',attempts=3)
    assert not prep.eligible(row,d) and 'retry_after' not in row
    row=prep.record(d,'needs_source',attempts=1)
    assert not prep.eligible(row,d)
