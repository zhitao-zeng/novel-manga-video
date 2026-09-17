import copy

import pytest

import experiments.rebuild_source_book as rebuild
from novel_manga.util import atomic_write_json
from novel_manga.story.source_identity import active_cast_names
from novel_manga.application.identity.store import current_context, read


def actor(i, name, kind='person', named=True):
    return {'id': i, 'name': name, 'named': named, 'kind': kind, 'presence': 'on_stage',
            'forms': [{'text': name, 'kind': 'name' if named else 'role', 'paragraphs': [1]}], 'body_facts': []}


def facts():
    return {'source_readable': True, 'source_problem': '', 'actors': [actor(1, '甲'), actor(2, '宝壶', 'object')],
            'locations': [{'name': '门口', 'description': '院门前', 'paragraphs': [1]}],
            'events': [{'summary': '甲收壶并说话', 'paragraphs': [1], 'participants': [1, 2],
                        'speech': [{'actor': 1, 'quote': '走吧。', 'mode': 'spoken'}]}]}


def test_fact_contract_requires_source_speech_valid_owners_and_full_coverage():
    segments = [{'segment_id': 's', 'text': '甲收起宝壶，说：“走吧。”'}]
    assert rebuild.validate_facts(facts(), segments)['actors'][1]['kind'] == 'object'
    bad = facts(); bad['events'][0]['speech'][0]['actor'] = 2
    with pytest.raises(ValueError, match='non-actor'):
        rebuild.validate_facts(bad, segments)
    bad = facts(); bad['events'][0]['speech'][0]['quote'] = '不在原文的话'
    with pytest.raises(ValueError, match='not found'):
        rebuild.validate_facts(bad, segments)
    with pytest.raises(ValueError, match='omit source'):
        rebuild.validate_facts(facts(), segments + [{'segment_id': 's2', 'text': '乙进门。'}])


def test_interrupted_utterance_retains_separate_literal_source_pieces():
    pieces = rebuild.ground_speech_quote('哈哈，有意思！你是女的吧？', '“哈哈，有意思！”甲笑着问，“你是女的吧？”')
    assert pieces == ['哈哈，有意思！', '你是女的吧？']
    with pytest.raises(ValueError, match='not found'):
        rebuild.ground_speech_quote('哈哈，有意思！你是男的吧？', '“哈哈，有意思！”甲笑着问，“你是女的吧？”')


def test_name_suffix_is_not_source_evidence_for_an_alias():
    assert not rebuild.independent_form('问天', '南宫问天已死。', {'南宫问天'})
    assert rebuild.independent_form('问天', '南宫问天走近，问天开口。', {'南宫问天'})
    assert rebuild.independent_form('霜', '霜痕之龙，霜！', {'霜痕之龙'})
    f = facts(); f['actors'] = [actor(1, '南宫问天')]
    f['actors'][0]['forms'].append({'text': '问天', 'kind': 'name', 'paragraphs': [1]})
    f['events'] = [{'summary': '提到南宫问天', 'participants': [1], 'paragraphs': [1], 'speech': []}]
    rebuild.validate_facts(f, [{'text': '南宫问天已死。'}])
    assert [row['text'] for row in f['actors'][0]['forms']] == ['南宫问天']


def test_rebuilt_catalogue_keeps_objects_out_and_retains_scene_roles(tmp_path):
    old = tmp_path / 'old'; novel = tmp_path / 'new'
    atomic_write_json(old / 'story_bible.json', {'characters': [{'name': '甲', 'appearance': '没有来源的旧设计'}]})
    atomic_write_json(novel / 'rebuild/manifest.json', {'title': '测试', 'legacy_book': str(old)})
    segments = [{'segment_id': 's', 'text': '甲收起宝壶，对门卫说：“走吧。”'}]
    f = facts(); f['actors'].append({**actor(3, '门卫', named=False), 'presence': 'mentioned'}); f['events'][0]['participants'].append(3)
    rebuild.validate_facts(f, segments)
    atomic_write_json(novel / 'new_1/segments.json', segments)
    atomic_write_json(novel / 'rebuild/facts/1.json', {'chapter': 1, 'status': 'verified', 'segments': segments, 'facts': f})
    result = rebuild.compile_book(novel, [1])
    assert result['characters'] == 1
    assert [c['name'] for c in read(novel / 'story_bible.json')['characters']] == ['甲']
    local = read(novel / 'new_1/source_bible.json')
    assert {c['name'] for c in local['characters']} == {'甲', '门卫'}
    assert '没有来源的旧设计' not in str(local)
    assert active_cast_names(current_context(novel / 'new_1')) == {'甲', '门卫'}
    entry = read(novel / 'entity/entities.json')[0]
    assert entry['legacy_asset_candidates'] == [{'name': '甲', 'asset_id': 'character_001'}]
    assert not (novel / 'series_assets').exists()


def test_shared_name_relation_cannot_merge_independent_bodies(tmp_path, monkeypatch):
    novel = tmp_path / 'new'
    f = facts(); f['actors'] = [actor(1, '本体'), actor(2, '化身')]
    first = copy.deepcopy(f); first['actors'] = [actor(1, '本体')]
    first['actors'][0]['forms'].append({'text': '化身', 'kind': 'name', 'paragraphs': [1]})
    monkeypatch.setattr(rebuild, 'judge_json', lambda *a, **k: {
        'verdict': 'different', 'reason': '同时在场的独立身体', 'paragraphs': [1]})
    for n, value in [(1, first), (2, f)]:
        atomic_write_json(novel / f'rebuild/facts/{n}.json', {'chapter': n, 'status': 'verified', 'facts': value,
                          'segments': [{'text': '本体站在化身对面。'}]})
    with pytest.raises(ValueError, match='collapse distinct'):
        rebuild.compile_book(novel, [1, 2])


def test_failed_fact_batch_cannot_silently_become_a_bible(tmp_path):
    with pytest.raises(ValueError, match='unverified'):
        rebuild.compile_book(tmp_path, [1])


def test_judge_sees_names_and_cannot_reject_an_already_correct_speaker(monkeypatch):
    def judge(parts, *a, **k):
        assert '"actor": "甲"' in parts[0]['text']
        return {'issues': [{'kind': 'speaker', 'event_number': 1, 'speech_number': 1,
                            'correct_actor': '甲', 'problem': '误读编号', 'source_paragraphs': [1]}]}
    monkeypatch.setattr(rebuild, 'judge_json', judge)
    result = rebuild.check_facts(facts(), [{'text': '甲说：“走吧。”'}])
    assert result['issues'] == [] and len(result['already_correct']) == 1


def test_speaker_correction_requires_evidence_naming_the_proposed_owner(monkeypatch):
    monkeypatch.setattr(rebuild, 'judge_json', lambda *a, **k: {'issues': [{
        'kind': 'speaker', 'event_number': 1, 'speech_number': 1, 'correct_actor': '乙',
        'problem': '甲说的话要改给乙', 'source_paragraphs': [1]}]})
    with pytest.raises(ValueError, match='proposed owner'):
        rebuild.check_facts(facts(), [{'text': '甲说：“走吧。”'}])


def test_whole_story_reader_cannot_use_hidden_source_or_summary(monkeypatch):
    calls = []
    def judge(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'episode_story_cold_read':
            assert '隐藏的真实原因' not in parts[0]['text']
            assert '甲夺走宝物' in parts[0]['text']
            return {'story': '甲夺走宝物，但不知道是谁的。', 'beats': [], 'unclear_connections': ['失主缺失']}
        return {'story_ok': False, 'issues': [{'stage': 1, 'problem': '没有交代失主', 'source_paragraphs': [1]}]}
    monkeypatch.setattr(rebuild, 'judge_json', judge)
    result = rebuild.check_episode_story({'summary': '隐藏的真实原因', 'shots': [{
        'source_quote': '隐藏的真实原因', 'motion_prompt': '甲夺走宝物', 'turns': []}]},
        [{'text': '乙向甲讨回宝物。'}], {})
    assert not result['story_ok'] and calls == ['episode_story_cold_read', 'episode_story_source_compare']
