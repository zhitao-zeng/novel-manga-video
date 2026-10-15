import copy
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from novel_manga.application.repair import agent, upstream
from novel_manga.util import atomic_write_json, read_json
from tests.test_repair_agent import configured


def selected_case(tmp_path):
    directory, clips, verdicts = configured(tmp_path)
    cards = directory.parent/'cards'; cards.mkdir()
    for name in ('body.png', 'portrait.png', 'alternative.png'):
        Image.new('RGB', (8, 8)).save(cards/name)
    clip = clips[0]
    clip['references'] = [
        {'tag': '@图片1', 'role': 'character', 'name': 'A', 'view': 'turnaround', 'path': 'cards/./body.png'},
        {'tag': '@图片3', 'role': 'character', 'name': 'A', 'view': 'expressions', 'path': str(cards/'portrait.png')}]
    clip['override'] = {'reference_views': {'A': {
        'body': str(cards/'body.png'), 'face': str(cards/'portrait.png'), 'asset_id': 'body'}}}
    assets = [{'id': name, 'role': 'wardrobe', 'name': name, 'mode': 'body_view',
               'binding': {'role': 'character', 'name': 'A'}, 'path': str(cards/f'{name}.png'),
               'source_quote': 'gray outfit', 'review': {'consistent': True, 'problems': []}}
              for name in ('body', 'alternative')]
    atomic_write_json(directory/'clip_plan.json', {'clips': clips})
    atomic_write_json(directory/'repair_candidates.json', {'assets': assets})
    return directory, clip, clips, verdicts


def test_catalog_marks_actual_path_binding_and_keeps_body_and_face_separate(tmp_path):
    directory, clip, _, _ = selected_case(tmp_path)
    before = copy.deepcopy(clip)
    stored = (directory/'repair_candidates.json').read_bytes()
    visible = upstream.catalog_for_clip(directory, clip)
    body, alternative = visible['assets']
    assert body['currently_selected'] is True and body['selected_reference_tags'] == ['@图片1']
    assert body['source_path'] == str((directory.parent/'cards/body.png').resolve())
    assert alternative['currently_selected'] is False and alternative['selected_reference_tags'] == []
    assert clip == before and clip['references'][1]['view'] == 'expressions'
    assert (directory/'repair_candidates.json').read_bytes() == stored


def test_sandbox_aliases_explicitly_identify_already_active_candidate(tmp_path, monkeypatch):
    directory, clip, _, verdicts = selected_case(tmp_path)
    config = {'runs_root': str(tmp_path/'runs')}
    def frames(d, cid, video, target):
        target.mkdir(parents=True); frame = target/'frame.jpg'; frame.write_bytes(b'frame')
        return [frame], 'fixture'
    monkeypatch.setattr(agent, 'provided_frames', frames)
    def execute(name, **kwargs):
        work = Path(config['runs_root'])/name
        selected = read_json(work/'input/upstream.json')['assets'][0]
        reference = read_json(work/'input/primary.json')['references'][0]
        assert selected['path'] != reference['path']
        assert selected['currently_selected'] and selected['selected_reference_tags'] == ['@图片1']
        assert selected['selected_reference_paths'] == [reference['path']]
        assert selected['source_path'] == reference['source_path']
        assert (work/selected['path']).read_bytes() == (work/reference['path']).read_bytes()
        atomic_write_json(work/'output/decision.json', {'action': 'stop', 'reason': 'active input verified; no video allowance', 'evidence': ['input/upstream.json']})
        return SimpleNamespace(ok=True, produced=['decision.json'], directory=work/'attempts/one')
    monkeypatch.setattr(agent, 'run_agent', execute)
    assert agent.propose(directory, clip, verdicts['a'], config=config)['action'] == 'stop'


def test_reselecting_active_body_has_specific_feedback_and_no_new_input(tmp_path, monkeypatch):
    directory, clip, clips, _ = selected_case(tmp_path)
    before = {name: (directory/name).read_bytes() for name in ('clip_plan.json', 'chapter_script.json')}
    monkeypatch.setattr(upstream, 'check_source', lambda *a: {'consistent': True, 'problems': []})
    monkeypatch.setattr(upstream, 'rebuild_clips', lambda *a, **kw: (copy.deepcopy(a[3]), []))
    monkeypatch.setattr(upstream, 'convert', lambda *a, **kw: pytest.fail('same active candidate must not be translated'))
    with pytest.raises(ValueError) as error:
        upstream.input_proposal(directory, {'clips': clips}, clip,
                                {'source_quote': 'gray outfit', 'assets': [{'candidate_id': 'body'}]})
    message = str(error.value)
    assert 'body' in message and '@图片1' in message and 'already selected' in message
    assert str(directory.parent/'cards/body.png') in message
    assert 'keep' in message and 'stop' in message and 'current frames' in message
    assert {name: (directory/name).read_bytes() for name in before} == before
    assert not (directory/'repair_history/history.json').exists()
