"""Which stages show the faceplate shut is asked of the model, one answer per stage, and only those clips get the
closed-faceplate view (ch12 part one, 2026-09-25: sent to every clip, H3 shut it on its own in 6 clips of 10)."""
import json

from novel_manga.application.identity.phases import POLICY
from novel_manga.application.packing import visor


def episode(tmp_path, closed_view=True):
    novel = tmp_path / 'book'
    card = novel / 'series_assets' / 'characters' / 'character_002-p1'
    card.mkdir(parents=True)
    (card / 'turnaround.jpeg').write_bytes(b'jpeg')
    if closed_view:
        (card / 'closed.jpeg').write_bytes(b'jpeg')
    (novel / 'series_assets' / 'phases.json').write_text(json.dumps({'policy': POLICY, 'characters': {'托尼': [
        {'from': 12, 'to': 12, 'asset_id': 'character_002-p1', 'label': '穿马克2机甲', 'wears': '马克2机甲', 'closed': '银白色'}]}}))
    directory = novel / 'book_12-1'
    directory.mkdir()
    shots = [{'origin_index': 1, 'in_frame': ['托尼', '席勒'], 'visual_prompt': '机甲落地，面罩紧闭', 'motion_prompt': '面罩打开露出托尼的脸',
              'end_state': '托尼站在桌前', 'turns': [{'speaker_name': '托尼', 'delivery_mode': 'visible_dialogue', 'text': '这地方真破。'}]},
             {'origin_index': 2, 'in_frame': ['席勒'], 'visual_prompt': '席勒喝咖啡', 'motion_prompt': '', 'end_state': '', 'turns': []},
             {'origin_index': 3, 'in_frame': ['托尼', '席勒'], 'visual_prompt': '托尼站在桌前', 'motion_prompt': '托尼抱臂',
              'end_state': '托尼抱臂', 'turns': []}]
    (directory / 'chapter_script.json').write_text(json.dumps({'shots': shots}, ensure_ascii=False))
    return directory, shots


def test_each_stage_he_is_in_is_answered_and_written(tmp_path):
    directory, shots = episode(tmp_path)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return {'stages': [{'index': 1, 'state': 'opening'}, {'index': 3, 'state': 'open'}]}

    result = visor.fill(directory, ask=ask)
    assert len(asked) == 1 and '1. 开始时：机甲落地，面罩紧闭' in asked[0] and '2. 开始时' not in asked[0]
    assert result['wearers']['托尼']['1']['state'] == 'opening' and result['wearers']['托尼']['3']['state'] == 'open'
    assert json.loads((directory / visor.FILE).read_text())['policy'] == visor.POLICY


def test_only_clips_with_the_faceplate_shut_need_the_closed_view(tmp_path):
    directory, shots = episode(tmp_path)
    visor.fill(directory, ask=lambda prompt: {'stages': [{'index': 1, 'state': 'opening'}, {'index': 3, 'state': 'open'}]})
    assert visor.closed_for(directory, {'shots': [shots[0]]}) == {'托尼'}
    assert visor.closed_for(directory, {'shots': [shots[2]]}) == set()


def test_an_unanswered_stage_or_a_changed_picture_counts_as_open(tmp_path):
    directory, shots = episode(tmp_path)
    result = visor.fill(directory, ask=lambda prompt: {'stages': [{'index': 3, 'state': 'closed'}]})
    assert result['wearers']['托尼']['1']['state'] == 'unknown' and result['warnings']
    changed = {**shots[2], 'visual_prompt': '托尼坐下'}
    assert visor.closed_for(directory, {'shots': [shots[2]]}) == {'托尼'}
    assert visor.closed_for(directory, {'shots': [changed]}) == set()


def test_no_closed_view_no_question_and_no_file_sends_nothing(tmp_path):
    directory, shots = episode(tmp_path, closed_view=False)
    assert visor.fill(directory, ask=lambda prompt: (_ for _ in ()).throw(AssertionError('asked')))['wearers'] == {}
    (directory / visor.FILE).unlink()
    assert visor.closed_for(directory, {'shots': shots}) == set()


def test_fill_keeps_current_answers_and_only_asks_for_missing_states(tmp_path):
    directory, shots = episode(tmp_path)
    visor.fill(directory, ask=lambda prompt: {'stages': [{'index': 1, 'state': 'opening'}, {'index': 3, 'state': 'open'}]})
    second = visor.fill(directory, ask=lambda prompt: (_ for _ in ()).throw(AssertionError('already filled')))
    assert second['wearers']['托尼']['3']['state'] == 'open'
    script = json.loads((directory / 'chapter_script.json').read_text())
    script['shots'][2]['motion_prompt'] = '托尼合上面罩'
    (directory / 'chapter_script.json').write_text(json.dumps(script))
    prompts = []
    visor.fill(directory, ask=lambda prompt: prompts.append(prompt) or {'stages': [{'index': 3, 'state': 'closing'}]})
    assert len(prompts) == 1 and '只需补这些编号：[3]' in prompts[0]


def test_duplicate_origin_indices_do_not_merge_different_stages(tmp_path):
    directory, shots = episode(tmp_path)
    shots[0]['origin_index'] = shots[2]['origin_index'] = 99
    (directory / 'chapter_script.json').write_text(json.dumps({'shots': shots}))
    result = visor.fill(directory, ask=lambda prompt: {'stages': [{'index': 1, 'state': 'closed'}, {'index': 3, 'state': 'open'}]})
    assert set(result['wearers']['托尼']) == {'1', '3'}
    assert visor.closed_for(directory, {'shots': [{**shots[0], 'index': 1}]}) == {'托尼'}
    assert visor.closed_for(directory, {'shots': [{**shots[2], 'index': 3}]}) == set()


def test_only_whole_clip_closed_state_uses_a_single_closed_card(tmp_path):
    directory,shots=episode(tmp_path)
    visor.fill(directory,ask=lambda prompt:{'stages':[{'index':1,'state':'opening'},{'index':3,'state':'closed'}]})
    assert visor.closed_for(directory,{'shots':[shots[0]]},throughout=True)==set()
    assert visor.closed_for(directory,{'shots':[shots[2]]},throughout=True)=={'托尼'}
    assert visor.closed_for(directory,{'shots':[shots[0],shots[2]]},throughout=True)==set()


def test_review_reads_current_actual_stage_states_and_split_position(tmp_path):
    directory, shots = episode(tmp_path)
    visor.fill(directory, ask=lambda q: {'stages': [{'index': 1, 'state': 'closing'}, {'index': 3, 'state': 'closed'}]})
    assert visor.recorded_clip_states(directory, {'shot_parts': [{'index': 1, 'part': [1, 2]}]}) == {'托尼': {'1': 'open'}}
    assert visor.recorded_clip_states(directory, {'shot_parts': [{'index': 1, 'part': [2, 2]}]}) == {'托尼': {'1': 'closing'}}
    assert visor.recorded_clip_states(directory, {'shot_indexes': [3]}) == {'托尼': {'3': 'closed'}}
    shots[2]['motion_prompt'] = '托尼掀开面罩'
    (directory / 'chapter_script.json').write_text(json.dumps({'shots': shots}))
    assert visor.recorded_clip_states(directory, {'shot_indexes': [3]}) == {}


def test_questioned_model_state_can_be_re_read_without_rewriting_the_script_or_other_records(tmp_path):
    directory, shots = episode(tmp_path)
    original = (directory / 'chapter_script.json').read_bytes()
    before = visor.fill(directory, ask=lambda q: {'stages': [{'index': 1, 'state': 'closed'}, {'index': 3, 'state': 'closed'}]})
    asked = []
    candidate = visor.fill(directory, write=False, reconsider={'托尼': {1: '本镜需要露出脸部表演，闭合图与之冲突'}},
                           ask=lambda q: asked.append(q) or {'stages': [{'index': 1, 'state': 'opening'}]})
    assert '只需补这些编号：[1]' in asked[0] and '不能把旧判断或参考图本身当成证据' in asked[0]
    assert candidate['wearers']['托尼']['1']['state'] == 'opening'
    assert candidate['wearers']['托尼']['3'] == before['wearers']['托尼']['3']
    assert json.loads((directory / visor.FILE).read_text()) == before
    assert (directory / 'chapter_script.json').read_bytes() == original
