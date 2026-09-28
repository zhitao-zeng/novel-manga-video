import copy
import json

from novel_manga.application.repair import reference_state


def test_wrong_closed_view_can_be_reselected_without_changing_the_scene(tmp_path, monkeypatch):
    d = tmp_path / 'book' / 'book_12'; d.mkdir(parents=True)
    script = {'shots': [{'index': 9, 'visual_prompt': '托尼的脸和眼皮清晰可见'}]}
    state = {'wearers': {'托尼': {'9': {'state': 'closed'}}}}
    corrected = {'wearers': {'托尼': {'9': {'state': 'open'}}}}
    clip = {'clip_id': 'c', 'shot_indexes': [9], 'references': [{'name': '托尼', 'role': 'character', 'view': 'closed'}]}
    plan = {'clips': [clip]}
    for name, value in [('chapter_script.json', script), ('visor_states.json', state), ('clip_plan.json', plan)]:
        (d / name).write_text(json.dumps(value))
    before = {p.name: p.read_bytes() for p in d.glob('*.json')}
    def fill(directory, **kwargs):
        assert kwargs['write'] is False and kwargs['script'] == script
        assert kwargs['reconsider'] == {'托尼': {9: '闭合图与露脸表演冲突'}}
        return corrected
    monkeypatch.setattr(reference_state, 'fill', fill)
    def rebuild(directory, bible, scene, old, targets, **kwargs):
        assert scene == script and targets == {'c'} and kwargs['visor_states'] == corrected
        updated = copy.deepcopy(old); updated['clips'][0]['references'][0]['view'] = 'turnaround'
        return updated, ['c']
    monkeypatch.setattr(reference_state, 'rebuild_clips', rebuild)
    monkeypatch.setattr(reference_state, 'convert', lambda *a, **k: True)
    monkeypatch.setattr(reference_state, 'h3_prompt_outdated', lambda *a: False)
    monkeypatch.setattr(reference_state, 'request_issues', lambda *a: [])
    monkeypatch.setattr(reference_state, 'request_consistency', lambda *a: {'consistent': True, 'problems': []})
    checked = {'findings': [{'aspect': 'visibility', 'picture': 1, 'relation': 'contradiction'}], 'problems': ['闭合图与露脸表演冲突']}
    proposal = reference_state.propose(d, clip, checked)
    assert proposal.script == script and proposal.visor_states == corrected and proposal.changed == ['c']
    assert {p.name: p.read_bytes() for p in d.glob('*.json')} == before


def test_sound_conflict_does_not_reopen_faceplate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_state, 'fill', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unrelated state')))
    clip = {'references': [{'role': 'character', 'name': '托尼', 'view': 'closed'}]}
    assert reference_state.propose(tmp_path, clip, {'findings': [{'aspect': 'sound', 'picture': 1, 'relation': 'contradiction'}]}) is None


def test_missing_visibility_detail_does_not_change_faceplate_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_state, 'fill', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no explicit conflict')))
    clip = {'references': [{'role': 'character', 'name': '托尼', 'view': 'closed'}]}
    checked = {'findings': [{'aspect': 'visibility', 'picture': 1, 'relation': 'underspecified'}]}
    assert reference_state.propose(tmp_path, clip, checked) is None
