from types import SimpleNamespace
from pathlib import Path

import pytest

from novel_manga.application.agents.episode import draft_assets
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.repair.history import generation_counts
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def setup(tmp_path, monkeypatch):
    run = tmp_path/'run'
    ctx = SimpleNamespace(input_mode='source', run_dir=run, work_dir=run/'sandbox',
        novel_dir=run/'book', episode_dir=run/'book/book_1', source_novel_dir=tmp_path/'original',
        asset_snapshot_dir=tmp_path/'shared')
    atomic_write_json(ctx.novel_dir/'story_bible.json', {'characters': [], 'locations': []})
    shared = ctx.asset_snapshot_dir/'series_assets'; shared.mkdir(parents=True)
    (ctx.novel_dir/'series_assets').symlink_to(shared, target_is_directory=True)
    (run/'source.txt').write_text('默先生是灰袍中年男子，在小屋开口说话。')
    atomic_write_json(run/'catalog.json', [])
    atomic_write_json(ctx.work_dir/'input/catalog.json', {'assets': []})
    draft = {'chapter_script': {'shots': [{'index': 1, 'characters': ['默先生'], 'location': '小屋'}]},
             'clips': [{'shot_indexes': [1], 'reference_ids': []}]}
    atomic_write_json(ctx.work_dir/'output/episode.json', draft)
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*': {'input_limit': 1}})
    draws, checks = [], []
    monkeypatch.setattr(draft_assets, 'source_check', lambda *a: {'consistent': True, 'problems': []})

    def build(ctx, directory, role, name, design):
        image = directory/'image.jpeg'
        if not image.exists():
            draws.append(name); image.write_bytes(('accepted '+name).encode())
        return image

    monkeypatch.setattr(draft_assets, 'build', build)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: checks.append(a[2]) or {'consistent': True, 'problems': []})
    row = {'clip_id': 'clip_01', 'action': 'input', 'reason': 'missing source actor and room',
           'source_quote': '默先生是灰袍中年男子',
           'characters': {'默先生': {'appearance': '中年男子', 'wardrobe': '灰袍'}},
           'locations': {'小屋': '无人小屋内景'}}
    return ctx, row, draws, checks


def test_check_draft_asset_request_matches_apply_without_models_or_receipts(setup):
    ctx,row,draws,checks=setup
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row]})
    result=EpisodeTools(ctx).dispatch('check',{'file':'output/repair.json'})
    assert result['ok'] and result['valid']
    assert not draws and not checks
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    bad={**row,'shots':[{'index':1,'location':'elsewhere'}]}
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[bad]})
    result=EpisodeTools(ctx).dispatch('check',{'file':'output/repair.json'})
    assert not result['valid'] and 'edit the provisional draft' in result['argument_errors']['clip_01']


def test_unadopted_draft_can_request_source_assets_without_bypassing_adoption(setup):
    ctx, row, draws, checks = setup
    before = (ctx.work_dir/'output/episode.json').read_bytes()
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [row]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] and len(result['assets_added']) == 2
    assert result['changed'] == result['input_repaired'] == []
    assert draws == checks == ['默先生', '小屋']
    assert generation_counts(ctx.episode_dir) == {}
    assert not (ctx.episode_dir/'clip_plan.json').exists() and not (ctx.run_dir/'prepared.json').exists()
    assert (ctx.work_dir/'output/episode.json').read_bytes() == before
    catalog = read_json(ctx.run_dir/'catalog.json')
    assert len(catalog) == 2 and all((ctx.work_dir/a['image']).is_file() for a in catalog)
    again = EpisodeTools(ctx).dispatch('apply')
    assert again['ok'] and again['assets_added'] == [] and len(draws) == 2


@pytest.mark.parametrize('interruption', [ConnectionError, KeyboardInterrupt])
def test_interrupted_picture_check_resumes_same_image_inside_input_budget(setup, monkeypatch, interruption):
    from novel_manga.application.agents.episode import input_repair
    ctx, row, draws, _ = setup
    row.pop('locations')
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: (_ for _ in ()).throw(interruption('offline')))
    with pytest.raises(interruption): draft_assets.repair(ctx, row)
    receipt = next((ctx.episode_dir/'work/input_repairs').glob('clip_01-*.json'))
    before = receipt.read_bytes()
    state = input_repair.repair_status(ctx)['clip_01']
    assert 'argument_error' not in state and state['resume_action']['resume'] is True
    assert receipt.read_bytes() == before
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: {'consistent': True, 'problems': []})
    result = draft_assets.repair(ctx, state['resume_action'])
    assert result['ok'] and draws == ['默先生']
    assert read_json(receipt)['resume_count'] == int(interruption is ConnectionError)


def test_false_source_and_unknown_clip_never_draw(setup, monkeypatch):
    ctx, row, draws, _ = setup
    with pytest.raises(ValueError, match='verbatim'):
        draft_assets.repair(ctx, {**row, 'source_quote': '不存在的原文'})
    with pytest.raises(ValueError, match='existing draft clip'):
        draft_assets.repair(ctx, {**row, 'clip_id': 'clip_99'})
    assert draws == []


def test_new_actor_registration_does_not_overwrite_shared_base_and_reuses_art_for_another_episode(setup):
    ctx, row, draws, _ = setup
    shared_base = ctx.asset_snapshot_dir/'series_assets/characters/character_001'
    shared_base.mkdir(parents=True)
    (shared_base/'turnaround.jpeg').write_bytes(b'another episode original')
    first = draft_assets.repair(ctx, row)
    assert (shared_base/'turnaround.jpeg').read_bytes() == b'another episode original'
    assert (ctx.novel_dir/'series_assets/characters/character_001/turnaround.jpeg').read_bytes() == 'accepted 默先生'.encode()
    assert read_json(ctx.novel_dir/'story_bible.json')['characters'][0]['name'] == '默先生'
    assert read_json(ctx.novel_dir/'entity/entities.json')[0]['asset_id'].startswith('draft_character_')
    accepted = read_json(ctx.run_dir/'draft-assets.json')
    other = SimpleNamespace(**ctx.__dict__)
    other.run_dir = ctx.run_dir.parent/'another'
    other.work_dir = other.run_dir/'sandbox'
    other.novel_dir = other.run_dir/'book'
    atomic_write_json(other.novel_dir/'story_bible.json', {'characters': [], 'locations': []})
    atomic_write_json(other.work_dir/'input/catalog.json', {'assets': []})
    (other.novel_dir/'series_assets').symlink_to(ctx.asset_snapshot_dir/'series_assets', target_is_directory=True)
    assert len(draft_assets.export(other, accepted)) == 2
    assert len(draws) == 2
    assert read_json(other.novel_dir/'story_bible.json')['characters'][0]['name'] == '默先生'


def test_new_entity_does_not_take_an_existing_implicit_bible_id(setup):
    from novel_manga.application.identity.store import load_catalog
    ctx, row, _, _ = setup
    atomic_write_json(ctx.novel_dir/'story_bible.json', {'characters': [
        {'name': '原有人物', 'appearance': '黑发', 'wardrobe': '黑衣'}], 'locations': []})
    draft_assets.repair(ctx, row)
    catalog = load_catalog(ctx.novel_dir)
    assert catalog.by_name['原有人物']['name'] == '原有人物'
    assert catalog.by_name['原有人物']['id'] != catalog.by_name['默先生']['id']


def test_registered_locations_have_a_canonical_card_for_later_recompilation(setup):
    ctx, row, _, _ = setup
    draft_assets.repair(ctx, row)
    assert (ctx.novel_dir/'series_assets/locations/location_001/establishing.jpeg').read_bytes() == 'accepted 小屋'.encode()


def test_review_resume_does_not_reinvoke_a_builder_that_would_replace_fallback_image(setup, monkeypatch):
    ctx, row, _, _ = setup
    row.pop('locations'); calls = []

    def fallback(ctx, directory, *args):
        calls.append(1); path = directory/'fallback.jpeg'; path.write_bytes(b'fallback'); return path

    monkeypatch.setattr(draft_assets, 'build', fallback)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: (_ for _ in ()).throw(ConnectionError('offline')))
    with pytest.raises(ConnectionError): draft_assets.repair(ctx, row)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: {'consistent': True, 'problems': []})
    draft_assets.repair(ctx, row)
    assert calls == [1]


def test_changed_book_style_does_not_reuse_an_accepted_image(setup):
    ctx, row, draws, _ = setup
    design = row['characters']['默先生']
    atomic_write_json(ctx.novel_dir/'style.json', {'name':'first', 'render_direction':'soft warm light'})
    first = draft_assets._asset(ctx, 'character', '默先生', design)
    atomic_write_json(ctx.novel_dir/'style.json', {'name':'second', 'render_direction':'cool flat illustration'})
    second = draft_assets._asset(ctx, 'character', '默先生', design)
    assert first['id'] != second['id'] and len(draws) == 2


def test_corrected_review_reuses_old_rejected_image_but_does_not_repeat_current_rejection(setup, monkeypatch):
    ctx, row, draws, _ = setup
    design = row['characters']['默先生']
    checks = []

    def reject(*args):
        checks.append(1)
        raise draft_assets.UnsupportedInput('review rejected image')

    monkeypatch.setattr(draft_assets, 'check_image', reject)
    with pytest.raises(draft_assets.UnsupportedInput):
        draft_assets._asset(ctx, 'character', '默先生', design)
    with pytest.raises(draft_assets.UnsupportedInput):
        draft_assets._asset(ctx, 'character', '默先生', design)
    assert len(checks) == len(draws) == 1
    saved = next((ctx.asset_snapshot_dir/'series_assets/draft-assets').glob('*/rejected.json'))
    atomic_write_json(saved, {'error': 'old review applied human skin requirements to an animal'})
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: checks.append(1) or
                        {'consistent': True, 'problems': []})
    accepted = draft_assets._asset(ctx, 'character', '默先生', design)
    assert accepted['image_review']['consistent'] and len(checks) == 2 and len(draws) == 1
    assert draft_assets._asset(ctx, 'character', '默先生', design) == accepted
    assert len(checks) == 2


def test_public_repair_rechecks_stale_image_failure_inside_the_original_input_allowance(setup, monkeypatch):
    ctx, row, draws, _ = setup
    row.pop('locations')
    monkeypatch.setattr(draft_assets, 'IMAGE_REVIEW_POLICY', 1)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: (_ for _ in ()).throw(
        draft_assets.UnsupportedInput('old scope rejected a valid card')))
    with pytest.raises(draft_assets.UnsupportedInput):
        draft_assets.repair(ctx, row)
    receipt = next((ctx.episode_dir/'work/input_repairs').glob('*.json'))
    before = read_json(receipt)
    monkeypatch.setattr(draft_assets, 'IMAGE_REVIEW_POLICY', 2)
    monkeypatch.setattr(draft_assets, 'check_image', lambda *a: {'consistent': True, 'problems': []})
    result = draft_assets.repair(ctx, row)
    after = read_json(receipt)
    assert result['ok'] and draws == ['默先生']
    assert after['resume_count'] == before['resume_count']
    assert after.get('revision_count', 0) == before.get('revision_count', 0)
    assert len(list(receipt.parent.glob('*.json'))) == 1
    assert generation_counts(ctx.episode_dir) == {}


def test_changed_picture_review_does_not_reopen_a_rejected_source_decision(setup, monkeypatch):
    ctx, row, draws, _ = setup
    monkeypatch.setattr(draft_assets, 'source_check', lambda *a: (_ for _ in ()).throw(
        draft_assets.UnsupportedInput('unsupported source identity')))
    with pytest.raises(draft_assets.UnsupportedInput):
        draft_assets.repair(ctx, row)
    monkeypatch.setattr(draft_assets, 'IMAGE_REVIEW_POLICY', 99)
    with pytest.raises(ValueError, match='rejected by source review'):
        draft_assets.repair(ctx, row)
    assert draws == []
