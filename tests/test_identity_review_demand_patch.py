"""A corrected candidate gets a normal final review; rejection still blocks it."""
from copy import deepcopy

import pytest

from novel_manga.application.identity import semantic_review as review
from novel_manga.llm import client


@pytest.mark.parametrize('final_approved', [False, True])
@pytest.mark.parametrize('protocol_repair', [False, True])
def test_final_review_drops_stale_demand_after_valid_correction(
        monkeypatch, final_approved, protocol_repair):
    original = {'actors': [{'source_id': 1, 'name': '旧称'}]}
    before = deepcopy(original)
    valid = {'actors': [{'source_id': 1, 'name': '甲'}]}
    proposal = {'actors': [{'source_id': 1, 'name': '无原文依据'}]} if protocol_repair else valid
    answers = [
        {'approved': False, 'reason': '绑定需要修改', 'correction': None},
        {'approved': False, 'reason': '修订绑定', 'correction': proposal},
    ]
    if protocol_repair:
        answers.append(valid)
    answers.append({'approved': final_approved, 'reason': '最终原文判断', 'correction': None})
    prompts = []

    def ask(parts, *args, **kwargs):
        prompts.append((kwargs['name'], parts[0]['text']))
        if len(prompts) == len(answers):
            assert '这次必须给出 correction' not in prompts[-1][1]
            assert '"name": "甲"' in prompts[-1][1]
        return deepcopy(answers[len(prompts) - 1])

    def validate(candidate):
        if candidate['actors'][0]['name'] == '无原文依据':
            raise ValueError('ungrounded source name')
        return candidate

    monkeypatch.setattr(client, 'ask_json', ask)
    if final_approved:
        value, verdict = review.close(original, {}, {}, name='review', validate=validate)
        assert value == valid and verdict['approved']
    else:
        with pytest.raises(ValueError, match='identity semantic review unresolved'):
            review.close(original, {}, {}, name='review', validate=validate)
    assert original == before
    assert len(prompts) == (4 if protocol_repair else 3)
    assert '这次必须给出 correction' in prompts[1][1]
    assert [name for name, _ in prompts] == (
        ['review', 'review', 'review_correction', 'review'] if protocol_repair
        else ['review', 'review', 'review'])
