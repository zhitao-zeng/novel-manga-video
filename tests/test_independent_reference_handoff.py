from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import independent, final_cut
from novel_manga.util import atomic_write_json, read_json
from tests.test_independent_final_review import setup


def reference_case(tmp_path, monkeypatch):
    _, media, final, _ = setup(tmp_path, monkeypatch)
    paths = {name: tmp_path/f'{name}.png' for name in ('body', 'face', 'prop', 'voice')}
    for name, path in paths.items(): path.write_bytes(('original bytes '+name).encode())
    refs = [
        {'role': 'character', 'name': 'A', 'asset_id': 'body', 'tag': '@图片1', 'view': 'turnaround', 'path': str(paths['body'])},
        {'role': 'character', 'name': 'A', 'asset_id': 'face', 'tag': '@图片3', 'view': 'expressions', 'path': str(paths['face'])},
        {'role': 'prop', 'name': 'rod', 'asset_id': 'rod', 'tag': '@图片2', 'wearers': ['A'], 'path': str(paths['prop'])},
        {'role': 'voice', 'name': 'A', 'path': str(paths['voice'])}]
    atomic_write_json(paths['face'].with_suffix('.png.origin.json'), {'reference_scope': 'identity_portrait'})
    atomic_write_json(tmp_path/'clip_plan.json', {'clips': [
        {'clip_id': 'a', 'references': refs},
        {'clip_id': 'b', 'references': [{**refs[2], 'tag': '@图片8'},
                                      {**refs[1], 'view': 'portrait_only', 'tag': '@图片5'}]}]})
    final['context'] = final_cut.context(tmp_path, media, final['movie'])
    return final, paths


def test_actual_reference_bytes_are_shared_with_precise_per_clip_usage(tmp_path, monkeypatch):
    final, paths = reference_case(tmp_path, monkeypatch)
    config = {'runs_root': str(tmp_path/'runs')}
    def execute(run, *, config, timeout):
        work = Path(config['runs_root'])/run
        packet = read_json(work/'input/review.json'); refs = packet['approved_references']
        assert len(refs) == 3 and len(list((work/'input').glob('reference-*'))) == 3
        assert all(row['role'] != 'voice' for row in refs)
        for row in refs:
            assert (work/row['path']).read_bytes() == Path(row['source_path']).read_bytes()
        rod = next(row for row in refs if row['name'] == 'rod')
        assert rod['clip_ids'] == ['a', 'b']
        assert [(r['clip_id'], r['tag']) for r in rod['bindings']] == [('a', '@图片2'), ('b', '@图片8')]
        assert all(r['wearers'] == ['A'] for r in rod['bindings'])
        face = next(row for row in refs if row['asset_id'] == 'face')
        assert [r['view'] for r in face['bindings']] == ['expressions', 'portrait_only']
        assert all(r['reference_scope'] == 'identity_portrait' for r in face['bindings'])
        assert len(packet['primary_frames']) == 1  # reference pictures are not extra required movie frames
        frame = packet['primary_frames'][0]['path']
        atomic_write_json(work/'output/review.json', {'complete': True, 'passed': True,
            'observations': [{'frame_path': frame, 'observation': 'movie observed'}], 'issues': [], 'prior_findings': []})
        return SimpleNamespace(ok=True, produced=['review.json'], directory=work/'attempts/one')
    monkeypatch.setattr(independent, 'run_agent', execute)
    result = independent.observe(tmp_path, final, config=config)
    assert result['complete'] and result['passed']


@pytest.mark.parametrize('change', ['file', 'plan'])
def test_reference_handoff_rejects_material_different_from_review_context(tmp_path, monkeypatch, change):
    final, paths = reference_case(tmp_path, monkeypatch)
    if change == 'file': paths['prop'].write_bytes(b'changed prop image after reviewed context')
    else:
        plan = read_json(tmp_path/'clip_plan.json')
        plan['clips'][0]['references'][2]['path'] = str(tmp_path/'wrong-old-prop.png')
        (tmp_path/'wrong-old-prop.png').write_bytes(b'old image')
        atomic_write_json(tmp_path/'clip_plan.json', plan)
    monkeypatch.setattr(independent, 'run_agent', lambda *a, **k: pytest.fail('stale reference must not reach reviewer'))
    with pytest.raises(ValueError, match='reference.*context'):
        independent.observe(tmp_path, final, config={'runs_root': str(tmp_path/'runs')})
