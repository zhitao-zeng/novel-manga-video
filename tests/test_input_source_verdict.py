"""Source checks distinguish actual contradictions from positive judge commentary."""
import pytest

from novel_manga.application.agents.episode import input_repair


SOURCE = '甲穿黑披风。甲说：小心，有人修复了防御系统，我们已进入防御区域！'
PATCHES = [{'index': 1, 'turns': [{'speaker_name': '甲', 'text': '小心，我们已进入防御区域！'}]}]


def invoke(monkeypatch, answers):
    calls = []
    def ask(content, schema, **kwargs):
        calls.append((content, schema, kwargs))
        assert len(calls) <= len(answers), 'source checker repeated its bounded confirmation'
        answer = answers[len(calls)-1]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings', lambda: {})
    return calls


@pytest.mark.parametrize('consistent', [True, False])
def test_positive_problem_text_is_confirmed_before_rejecting(monkeypatch, consistent):
    initial = {'consistent': consistent, 'problems': ['符合允许缩写对白的要求，所有修改均有原文依据。']}
    calls = invoke(monkeypatch, [initial, {'conflicts': []}])
    answer = input_repair.source_check(SOURCE, '甲说', PATCHES, {}, scoped_shots=PATCHES)
    assert answer['consistent'] is True and answer['problems'] == []
    assert answer['initial'] == initial and answer['confirmation'] == {'conflicts': []}
    assert len(calls) == 2
    confirmation_prompt = str(calls[-1][0])
    assert SOURCE in confirmation_prompt and 'speaker_name' in confirmation_prompt


def test_supported_input_does_not_add_another_model_call(monkeypatch):
    from novel_manga.review.contracts import WATCHABLE_CRITERIA
    calls = invoke(monkeypatch, [{'consistent': True, 'problems': []}])
    assert input_repair.source_check(SOURCE, '甲说', PATCHES, {})['consistent'] is True
    assert len(calls) == 1
    assert WATCHABLE_CRITERIA in calls[0][0][0]['text']


def test_real_source_conflict_remains_blocked_with_evidence(monkeypatch):
    conflict = {'field': 'shots[1].turns', 'source_quote': '我们已进入防御区域', 'problem': '候选改成尚未进入防御区域，意思反转'}
    calls = invoke(monkeypatch, [{'consistent': False, 'problems': ['意思反转']}, {'conflicts': [conflict]}])
    with pytest.raises(input_repair.UnsupportedInput, match='意思反转') as caught:
        input_repair.source_check(SOURCE, '甲说', [{'index': 1, 'turns': [
            {'speaker_name': '甲', 'text': '我们尚未进入防御区域！'}]}], {})
    assert caught.value.review['confirmation']['conflicts'] == [conflict]
    assert len(calls) == 2


@pytest.mark.parametrize('confirmation', [TimeoutError('judge unavailable'),
    {'conflicts': [{'source_quote': '不存在的原文', 'problem': '衣服矛盾'}]}])
def test_missing_confirmation_or_false_quote_cannot_approve(monkeypatch, confirmation):
    invoke(monkeypatch, [{'consistent': True, 'problems': ['矛盾']}, confirmation])
    with pytest.raises((TimeoutError, ValueError)):
        input_repair.source_check(SOURCE, '甲说', PATCHES, {})


def test_unchanged_dialogue_cannot_be_reported_as_the_wearing_patch(monkeypatch):
    confirmation = {'conflicts': [{'field': 'shots[1].turns', 'source_quote': '甲说', 'problem': '旧对白问题'}]}
    calls = invoke(monkeypatch, [{'consistent': False, 'problems': ['旧对白问题']}, confirmation])
    with pytest.raises(input_repair.SourceCheckIncomplete) as caught:
        input_repair.source_check(SOURCE, '甲说', [{'index': 1, 'wears': {}}], {}, scoped_shots=PATCHES)
    assert caught.value.review['consistent'] is None
    fields = calls[1][1]['properties']['conflicts']['items']['properties']['field']['enum']
    assert fields == ['shots[1].wears']
    assert caught.value.review['confirmation'] == confirmation


def test_incomplete_initial_source_reply_is_not_a_semantic_rejection(monkeypatch):
    calls = invoke(monkeypatch, [{}])
    with pytest.raises(input_repair.SourceCheckIncomplete) as caught:
        input_repair.source_check(SOURCE, '甲说', PATCHES, {})
    assert caught.value.review['consistent'] is None and len(calls) == 1


def test_source_judges_share_planner_turn_encoding(monkeypatch):
    import json
    from novel_manga.story.fields import turn_field
    from novel_manga.planning.constants import DELIVERY_MODES
    from novel_manga.story.field_validation import delivery_issue
    patch = [{'index': 1, 'turns': [{'speaker_name': '甲', 'delivery_mode': 'offscreen_dialogue',
               'inner_monologue': True, 'text': '小心，我们已进入防御区域！'}]}]
    assert delivery_issue(patch[0]['turns'][0]) is None
    calls = invoke(monkeypatch, [{'consistent': False, 'problems': ['待核发声方式']}, {'conflicts': []}])
    assert input_repair.source_check(SOURCE, '甲说', patch, {})['consistent']
    expected = turn_field([], [], DELIVERY_MODES)['properties']
    for content, _, _ in calls:
        payload = json.loads(content[0]['text'].split('\n', 1)[1].split('\n现在独立核实', 1)[0])
        assert payload['shots'] == patch
        assert payload['turn_encoding'] == {k: expected[k] for k in ('delivery_mode', 'inner_monologue')}


@pytest.mark.parametrize('step,patch,expected', [
    ('source', {'index': 1, 'turns': []}, True),
    ('source', {'index': 1, 'wears': {}}, False),
    ('compile', {'index': 1, 'turns': []}, False)])
def test_turn_contract_recovery_does_not_reopen_other_failures(step, patch, expected):
    from novel_manga.application.agents.episode.input_records import source_review_recoverable, SOURCE_REVIEW_POLICY
    record = {'status': 'rejected', 'step': step, 'source_review_policy': 2,
              'decision': {'shots': [patch]}}
    assert source_review_recoverable(record) is expected
    assert not source_review_recoverable({**record, 'source_review_policy': SOURCE_REVIEW_POLICY})
