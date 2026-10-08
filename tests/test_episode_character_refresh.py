"""Selected input refresh stays local and never invokes production services."""
import json

import pytest

from novel_manga.application.agents.episode import cli, context
from novel_manga.application.agents.episode.inputs import prepare_inputs, refresh_character_inputs
from novel_manga.util import atomic_write_json, read_json
from support.episode_inputs import book, saved_script


@pytest.fixture
def frozen_run(book):
    source, episode, root = book
    bible = read_json(source / 'story_bible.json')
    bible['characters'].append({'name': '宁霜', 'appearance': '旧短发', 'wardrobe': '蓝衣'})
    atomic_write_json(source / 'story_bible.json', bible)
    manifest = read_json(source / 'series_assets/manifest.json')
    manifest['characters'].append({'asset_id': 'character_010', 'name': '宁霜',
                                  'primary_image': 'series_assets/characters/character_010/turnaround.jpeg'})
    atomic_write_json(source / 'series_assets/manifest.json', manifest)
    for ident in ('character_009', 'character_010'):
        folder = source / 'series_assets/characters' / ident
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'turnaround.jpeg').write_bytes(('old ' + ident).encode())
        (folder / 'expressions.jpeg').write_bytes(('old expressions ' + ident).encode())
        atomic_write_json(folder / 'spec.json', {'design': 'old ' + ident})
    script, plan = saved_script(source, episode)
    script['shots'][0]['characters'].append('宁霜')
    plan['clips'][0]['references'].append({'role': 'character', 'name': '宁霜',
        'asset_id': 'character_010', 'path': 'series_assets/characters/character_010/turnaround.jpeg'})
    atomic_write_json(episode / 'chapter_script.json', script)
    atomic_write_json(episode / 'clip_plan.json', plan)
    ctx = context.create_context(source, 2, output_root=root / 'isolated')
    prepare_inputs(ctx)
    return ctx


def update_source(ctx):
    source = ctx.source_novel_dir
    bible = read_json(source / 'story_bible.json')
    bible['genre'] = 'source changed globally'
    for row in bible['characters']:
        row['appearance'] = '最新外貌 ' + row['name']
        row['wardrobe'] = '最新服装 ' + row['name']
        row['sources'] = [{'chapter': 420, 'quote': '明确的人物证据'}]
    atomic_write_json(source / 'story_bible.json', bible)
    manifest = read_json(source / 'series_assets/manifest.json')
    for row in manifest['characters']:
        row['design_version'] = 'latest'
    manifest['locations'][0]['unrelated_change'] = True
    atomic_write_json(source / 'series_assets/manifest.json', manifest)
    for ident in ('character_009', 'character_010'):
        folder = source / 'series_assets/characters' / ident
        (folder / 'turnaround.jpeg').write_bytes(('new ' + ident).encode())
        (folder / 'expressions.jpeg').write_bytes(('new expressions ' + ident).encode())
        atomic_write_json(folder / 'spec.json', {'design': 'new ' + ident})
    (source / 'series_assets/voices/林舟.wav').write_bytes(b'new source voice')


def test_refresh_selected_actor_updates_bible_views_specs_and_manifest_without_other_changes(frozen_run):
    ctx = frozen_run
    bible = read_json(ctx.novel_dir / 'story_bible.json')
    manifest = read_json(ctx.novel_dir / 'series_assets/manifest.json')
    atomic_write_json(ctx.episode_dir / 'repair_history/history.json', {'trials': [{'clip_id': 'clip_01', 'count': 3}]})
    atomic_write_json(ctx.run_dir / 'prepared.json', {'clips': 1, 'ready': True})
    protected = [ctx.episode_dir / 'clip_plan.json', ctx.episode_dir / 'chapter_script.json',
                 ctx.episode_dir / 'repair_history/history.json', ctx.run_dir / 'prepared.json',
                 ctx.work_dir / 'input/clip_plan.json', ctx.work_dir / 'output/requests.json',
                 ctx.novel_dir / 'series_assets/characters/character_010/turnaround.jpeg',
                 ctx.novel_dir / 'series_assets/characters/character_010/spec.json',
                 ctx.novel_dir / 'series_assets/voices/林舟.wav']
    before = {path: path.read_bytes() for path in protected}
    update_source(ctx)
    # Normal preparation continues to use the frozen book and published views.
    prepare_inputs(ctx)
    assert read_json(ctx.novel_dir / 'story_bible.json') == bible
    assert all(path.read_bytes() == data for path, data in before.items())
    source_before = {path: path.read_bytes() for path in ctx.source_novel_dir.rglob('*') if path.is_file()}
    result = refresh_character_inputs(ctx, ['character_009', 'character_009'])
    assert result['refreshed_characters'] == ['character_009']
    changed = read_json(ctx.novel_dir / 'story_bible.json')
    assert changed['characters'][0]['appearance'] == '最新外貌 林舟'
    assert changed['characters'][0]['sources'][0]['chapter'] == 420
    assert changed['characters'][1] == bible['characters'][1]
    assert changed['genre'] == bible['genre']
    assert read_json(ctx.work_dir / 'input/story_bible.json') == changed
    current_manifest = read_json(ctx.novel_dir / 'series_assets/manifest.json')
    assert current_manifest['characters'][0]['design_version'] == 'latest'
    assert current_manifest['characters'][1] == manifest['characters'][1]
    assert current_manifest['locations'] == manifest['locations']
    assert read_json(ctx.novel_dir / 'series_assets/characters/character_009/spec.json')['design'] == 'new character_009'
    offered = read_json(ctx.work_dir / 'input/catalog.json')['assets']
    for row in offered:
        if row['asset_id'] == 'character_009':
            assert (ctx.work_dir / row['image']).read_bytes().startswith(b'new')
    assert all(path.read_bytes() == data for path, data in before.items())
    assert all(path.read_bytes() == data for path, data in source_before.items())


def test_refresh_detaches_legacy_shared_cards_without_changing_other_runs(frozen_run):
    import shutil
    ctx = frozen_run
    shared = ctx.asset_snapshot_dir / 'series_assets'
    # Reproduce an existing production run whose asset root points at the
    # shared cache, including the episode-local metadata overlay.
    local = ctx.novel_dir / 'series_assets'
    shutil.copytree(local, shared, dirs_exist_ok=True)
    shutil.rmtree(local)
    local.symlink_to(shared, target_is_directory=True)
    other_root = ctx.run_dir.parent / 'other-run'
    other_root.mkdir()
    other_link = other_root / 'series_assets'
    other_link.symlink_to(shared, target_is_directory=True)
    old_card = shared / 'characters/character_009/turnaround.jpeg'
    before = {path: path.read_bytes() for path in shared.rglob('*') if path.is_file()}
    update_source(ctx)
    refresh_character_inputs(ctx, ['character_009'])
    assert not local.is_symlink()
    assert (local / 'characters/character_009/turnaround.jpeg').read_bytes() == b'new character_009'
    assert (other_link / 'characters/character_009/turnaround.jpeg').read_bytes() == b'old character_009'
    assert old_card.read_bytes() == b'old character_009'
    assert all(path.read_bytes() == data for path, data in before.items())


def test_new_run_uses_latest_source_bible_and_images_while_old_run_stays_frozen(frozen_run):
    ctx = frozen_run
    old_card = ctx.novel_dir / 'series_assets/characters/character_009/turnaround.jpeg'
    old_image = old_card.read_bytes()
    update_source(ctx)
    source_episode = ctx.source_novel_dir / 'another-book_3'
    source_episode.mkdir()
    for filename in ('segments.json', 'chapter_script.json', 'clip_plan.json'):
        atomic_write_json(source_episode / filename, read_json(ctx.source_episode_dir / filename))
    new = context.create_context(ctx.source_novel_dir, 3, output_root=ctx.run_dir.parent.parent)
    prepare_inputs(new)
    assert read_json(new.novel_dir / 'story_bible.json')['characters'][0]['appearance'] == '最新外貌 林舟'
    assert (new.novel_dir / 'series_assets/characters/character_009/turnaround.jpeg').read_bytes() == b'new character_009'
    assert read_json(new.novel_dir / 'series_assets/characters/character_009/spec.json')['design'] == 'new character_009'
    assert old_card.read_bytes() == old_image
    assert read_json(ctx.novel_dir / 'story_bible.json')['characters'][0]['appearance'] == '黑发'


@pytest.mark.parametrize('missing_image', [False, True])
def test_invalid_refresh_leaves_existing_inputs_unchanged(frozen_run, missing_image):
    ctx = frozen_run
    if missing_image:
        (ctx.source_novel_dir / 'series_assets/characters/character_009/turnaround.jpeg').unlink()
    before = {path: path.read_bytes() for path in ctx.run_dir.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='image is missing' if missing_image else 'unknown source character'):
        refresh_character_inputs(ctx, ['character_009' if missing_image else 'character_999'])
    assert all(path.read_bytes() == data for path, data in before.items())


def test_prepare_refresh_flag_does_not_reprepare_or_start_models(frozen_run, monkeypatch, capsys):
    ctx = frozen_run
    update_source(ctx)
    monkeypatch.setattr(cli, 'create_context', lambda *a, **kw: ctx)
    monkeypatch.setattr(cli, 'prepare_inputs', lambda *a: pytest.fail('refresh must not prepare the whole run'))
    monkeypatch.setattr(cli, 'run_episode', lambda *a, **kw: pytest.fail('refresh must not run an Agent'))
    assert cli.main(['prepare', '--novel-dir', str(ctx.source_novel_dir), '--episode', '2',
                     '--refresh-character', 'character_009']) == 0
    result = json.loads(capsys.readouterr().out)['episodes'][0]
    assert result['status'] == 'refreshed'
    assert result['refreshed_characters'] == ['character_009']


def test_refresh_retains_each_existing_reference_id_when_authored_aliases_share_a_picture(frozen_run):
    ctx = frozen_run
    catalog = read_json(ctx.run_dir / 'catalog.json')
    row = next(row for row in catalog if row['asset_id'] == 'character_009')
    alias = {**row, 'name': '小林', 'id': 'alias-reference', 'image': 'input/refs/alias.jpeg'}
    (ctx.work_dir / alias['image']).write_bytes(b'old alias reference')
    catalog.append(alias)
    atomic_write_json(ctx.run_dir / 'catalog.json', catalog)
    update_source(ctx)
    refresh_character_inputs(ctx, ['character_009'])
    refreshed = read_json(ctx.run_dir / 'catalog.json')
    assert [row['id'] for row in refreshed[:len(catalog)]] == [row['id'] for row in catalog]
    for row in refreshed:
        if row['asset_id'] == 'character_009':
            assert (ctx.work_dir / row['image']).read_bytes().startswith(b'new')
