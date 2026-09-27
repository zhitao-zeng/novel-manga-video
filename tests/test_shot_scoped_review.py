import pytest

from novel_manga.application.review import cast_video
from novel_manga.review.prompts import shot_contract


@pytest.mark.parametrize('changes,wrong', [
    ({'same_color_family': True}, False),
    ({'visibility': 'outside_frame'}, False),
    ({'visibility': 'uncertain'}, False),
    ({'same_entity': False}, False),
    ({}, True),
    ({'kind': 'costume', 'same_color_family': True}, True),
    ({'kind': 'state'}, True),
])
def test_only_visible_matching_evidence_can_request_a_retake(changes, wrong):
    # Same-color illumination, cropped helmets and an empty suit compared to its wearer all occurred in ch12.
    row = {'name': '穿甲者', 'kind': 'color', 'visibility': 'visible', 'same_entity': True,
           'same_color_family': False, 'observed': '金色面甲', 'expected': '银白面甲', **changes}
    result = cast_video.look_result(row)
    assert bool(cast_video.wrong_looks({'looks': [result]})) is wrong


def test_the_actual_ending_state_is_not_cut_off_and_guides_the_correction(monkeypatch):
    clip = {'cast': ['穿甲者'], 'prompt': '腿部特写；另一套无人空甲随后飞入。',
            'prompt_h3': 'detailed_description:\n' + 'A long shot. ' * 300 + 'The silver visor closes at the end.'}
    seen = [{'who': '穿甲者', 'wears': '银色盔甲，结尾合上面罩'}]
    def ask(parts, schema, name):
        text = parts[0]['text']
        assert 'The silver visor closes at the end.' in text and '腿部特写' in text
        return {'checks': [{'name': '穿甲者', 'kind': 'state', 'same_entity': True, 'visibility': 'visible',
                            'same_color_family': True, 'instruction': '结尾合上银白面甲。'}]}
    monkeypatch.setattr(cast_video, 'ask', ask)
    draw = {'穿甲者': '银色盔甲，参考卡的面罩打开'}
    checks = cast_video.compare(draw, [], seen, clip)
    assert cast_video.instruction(clip, {'looks': checks}, draw) == '结尾合上银白面甲。'
    assert '腿部特写' in shot_contract(clip)


def test_closed_only_card_is_available_for_review(tmp_path):
    card = tmp_path / 'closed.jpeg'; card.write_bytes(b'card')
    clip = {'cast': ['穿甲者'], 'references': [{'role': 'character', 'name': '穿甲者',
            'view': 'closed', 'path': 'closed.jpeg'}]}
    assert cast_video.cast_cards(clip, tmp_path) == {'穿甲者': card}
