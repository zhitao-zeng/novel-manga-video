"""The no-change input check must honor an already accepted render risk."""
from types import SimpleNamespace

from novel_manga.application.agents.episode import input_compilation
from novel_manga.util import atomic_write_json


def test_existing_input_check_honors_accepted_render_risk(tmp_path, monkeypatch):
    episode = tmp_path/'book/book_1'
    episode.mkdir(parents=True)
    ctx = SimpleNamespace(episode_dir=episode, novel_dir=tmp_path/'book',
                          run_dir=tmp_path/'run', work_dir=tmp_path/'run/sandbox')
    clip = {'clip_id': 'clip_04', 'kind': 'video',
            'references': [{'name': '人龙', 'role': 'character',
                            'path': 'series_assets/characters/character_019/turnaround.jpeg'}]}
    plan = {'clips': [clip]}
    script = {'shots': []}
    atomic_write_json(episode/'render_risks_accepted.json', {'clips': {
        'clip_04': {'wearer_card': ['人龙'], 'reason': 'accepted for this clip'}}})
    case = SimpleNamespace(ctx=ctx, row={}, plan=plan, script=script, candidate_script=script,
                           shots={}, patches=[], target=clip, targets={'clip_04'}, counts={},
                           scope=None, phases={}, locations={}, characters={}, cid='clip_04')
    record = {}
    receipt = tmp_path/'run/receipt.json'
    atomic_write_json(receipt, {})
    seen = {}

    import novel_manga.application.repair.flow as repair_flow
    import novel_manga.application.preparation.readiness as readiness
    import novel_manga.application.preparation.request_check as request_check
    import novel_manga.application.preparation.wearer_cards as wearer_cards

    monkeypatch.setattr(repair_flow, 'rebuild_clips', lambda *a, **k: (plan, []))
    def fake_render(clip, novel_dir, accepted=None, **kwargs):
        seen['accepted'] = accepted
        if not accepted or not accepted.get('wearer_card'):
            return (['risk: wearer card differs'], [])
        return ([], [])
    monkeypatch.setattr(readiness, 'render_risks', fake_render)
    monkeypatch.setattr(request_check, 'request_consistency',
                        lambda *a, **k: {'consistent': True, 'problems': []})
    monkeypatch.setattr(wearer_cards, 'check_references', lambda *a, **k: None)

    result = input_compilation.compile_input(case, receipt, record, lambda *a: None)
    assert seen['accepted'].get('wearer_card') == ['人龙']
    assert result.plan is None and result.changed is None
    assert record.get('status') == 'checked_existing_wearing'
