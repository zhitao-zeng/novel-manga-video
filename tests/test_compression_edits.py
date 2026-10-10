"""Dialogue compression changes actual speech while preserving source and camera intent."""
import copy

import pytest

from novel_manga.application.planning import compression
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.text import stage_seconds


def draft():
    return {'video_title': '来客', 'clips': [{'clip_id': 'clip_1', 'location': '庭院',
        'characters': ['主角'], 'stages': [{'segment_id': 'seg_1', 'source_quote': '主角决定先查清来客身份。',
            'start_state': '主角站在门口', 'event': '主角看向门外', 'end_state': '主角仍站在门口',
            'camera': '平视中景', 'light': '日光', 'actions': [], 'wears': {},
            'turns': [{'speaker_name': '主角', 'delivery_mode': 'offscreen_dialogue',
                       'text': '先查清他们的身份，不要贸然动手，我们暂时不能暴露自己。',
                       'inner_monologue': True, 'emotion': '谨慎', 'chat_target': ''}]}]}]}


def test_only_returned_words_change_and_real_duration_is_recomputed(monkeypatch):
    original = draft()
    context = PlannerContext.from_env()
    requests = []
    def reply(client, endpoints, headers, body):
        requests.append(body)
        return {'choices': [{'finish_reason': 'stop', 'message': {
            'content': '{"edits":[{"index":1,"text":"先查身份，别暴露。"}]}'}}]}
    monkeypatch.setattr(compression, 'post_any', reply)
    shot = original['clips'][0]['stages'][0]
    candidate, _ = compression.compress_plan(original, {}, {}, [shot], [],
        base_url='http://model.invalid/v1', model='test', max_tokens=2000, timeout=10, ctx=context)
    expected = copy.deepcopy(original)
    expected['clips'][0]['stages'][0]['turns'][0]['text'] = '先查身份，别暴露。'
    assert candidate == expected and original == draft()
    changed = candidate['clips'][0]['stages'][0]
    assert stage_seconds(changed['turns'], ctx=context) < stage_seconds(shot['turns'], ctx=context)
    assert requests[0]['response_format'] == {'type': 'json_object'}
    assert 'maxLength' not in requests[0]['messages'][-1]['content']
    assert 'max_chars' not in requests[0]['messages'][-1]['content']
    assert '不得切掉词尾' in requests[0]['messages'][0]['content']


def test_missing_line_index_is_a_failed_edit_not_an_unbound_partial_plan(monkeypatch):
    monkeypatch.setattr(compression, 'post_any', lambda *a: {'choices': [
        {'finish_reason': 'stop', 'message': {'content': '{"edits":[]}'}}]})
    original = draft()
    with pytest.raises(ValueError, match='each spoken line exactly once'):
        compression.compress_plan(original, {}, {}, original['clips'][0]['stages'], [],
            base_url='http://model.invalid/v1', model='test', max_tokens=2000,
            timeout=10, ctx=PlannerContext.from_env())


@pytest.mark.parametrize('text,match', [('保留这句。', 'retained dialogue'), ('', 'all source coverage')])
def test_stage_deletion_cannot_discard_retained_words_or_a_whole_source_segment(monkeypatch, text, match):
    import json
    monkeypatch.setattr(compression, 'post_any', lambda *a: {'choices': [
        {'finish_reason': 'stop', 'message': {'content': json.dumps({
            'edits': [{'index': 1, 'text': text}], 'dropped_stages': ['1:1']})}}]})
    original = draft()
    with pytest.raises(ValueError, match=match):
        compression.compress_plan(original, {}, {}, original['clips'][0]['stages'], [],
            base_url='http://model.invalid/v1', model='test', max_tokens=2000,
            timeout=10, ctx=PlannerContext.from_env())
    assert original == draft()


def test_deleted_stage_needs_no_redundant_empty_dialogue_edit(monkeypatch):
    import json
    original=draft();first=original['clips'][0]['stages'][0]
    original['clips'][0]['stages'].append(copy.deepcopy(first))
    monkeypatch.setattr(compression,'post_any',lambda *a:{'choices':[{'finish_reason':'stop','message':{
        'content':json.dumps({'edits':[{'index':2,'text':'先查身份，别暴露。'}],'dropped_stages':['1:1']})}}]})
    candidate,details=compression.compress_plan(original,{}, {},original['clips'][0]['stages'],[],
        base_url='http://model.invalid/v1',model='test',max_tokens=2000,timeout=10,ctx=PlannerContext.from_env())
    assert len(candidate['clips'][0]['stages'])==1
    assert candidate['clips'][0]['stages'][0]['turns'][0]['text']=='先查身份，别暴露。'
    assert len(original['clips'][0]['stages'])==2
    assert details['dropped_stages']==['1:1']
