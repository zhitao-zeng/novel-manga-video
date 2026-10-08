"""Draw episode-scoped location variants with the existing asset factory."""
from dataclasses import replace
import shutil

from novel_manga.util import atomic_write_json, read_json


def build_locations(ctx, names):
    from novel_manga.config import Settings
    from novel_manga.models.bible import StoryBible
    from novel_manga.application.profiles import (
        frame_spec, load_genre, load_profile, load_style, styled_bible)
    from novel_manga.media.adapters import FramedPhanRouter
    from novel_manga.media.asset_builder import FramedAssetFactory
    from novel_manga.media.asset_style import AssetStyle, image_backend
    from novel_manga.media.asset_specs import location_spec
    from novel_manga.media.card_check import people_in_card, record_people_check
    from novel_manga.media.common import sha256_file
    from novel_manga.application.assets.phase_cards import STYLE_MASTER_GUARD

    profile = load_profile(ctx.novel_dir)
    frame = frame_spec(profile)
    settings = Settings.from_env(provider='phanrouter', output_root=ctx.run_dir, admission_mode='preview')
    settings = replace(settings, width=frame['width'], height=frame['height'])
    style = AssetStyle.for_genre(load_genre(profile), frame_text=frame['text'],
                                style=load_style(profile, ctx.novel_dir), backend=image_backend(settings))
    bible = styled_bible(StoryBible.model_validate(read_json(ctx.novel_dir/'story_bible.json')),
                         profile, ctx.novel_dir)
    factory = FramedAssetFactory(settings, FramedPhanRouter(settings, frame), style=style)
    root = ctx.novel_dir/'series_assets'
    phases = read_json(root/'phases.json')['locations']
    for name in sorted(names):
        phase = phases[name][0]
        asset_id = phase['asset_id']
        directory = root/'locations'/asset_id
        directory.mkdir(parents=True, exist_ok=True)
        full = name + '：' + phase['description']
        prompt = (factory._location_prompt(bible, full)
                  + (STYLE_MASTER_GUARD if settings.style_master_path is not None else '')
                  + style.location_empty_suffix)
        spec = {**location_spec(asset_id, full, bible, prompt), 'base_asset': phase['base_asset'],
                'from_chapter': phase['from'], 'to_chapter': phase['to']}
        card = directory/'establishing.jpeg'
        if not card.is_file():
            # Equal prompts may already have a completed card from an interrupted repair.
            for old in sorted((root/'locations').glob('location_*-agent-*/spec.json')):
                prior = old.parent/'establishing.jpeg'
                if old.parent != directory and read_json(old, {}).get('prompt') == prompt and prior.is_file():
                    shutil.copy2(prior, card)
                    meta = prior.with_suffix('.jpeg.request.json')
                    if meta.is_file():
                        shutil.copy2(meta, card.with_suffix('.jpeg.request.json'))
                    spec['reused_from'] = str(old.parent)
                    break
        atomic_write_json(directory/'spec.json', spec)
        if not card.is_file():
            factory.ensure_card(prompt, card, reference=settings.style_master_path,
                                aspect_ratio='16:9' if '16:9' in frame['text'] else '9:16',
                                **({'route_local': False} if style.dialect_routes_subset else {}))
        # Use the existing picture-bound empty-scene check; an unchecked or occupied
        # card stays a candidate and is never silently treated as an accepted location.
        check = read_json(card.with_suffix('.jpeg.request.json'), {}).get('people_check') or {}
        if (check.get('artifact_sha256') != sha256_file(card)
                or type(check.get('people_in_scene')) is not int):
            check = people_in_card(card)
            record_people_check(card, check)
        if check.get('people_in_scene') != 0:
            raise ValueError(f'{name}: location card must show the empty setting; revise its description')


def local_phases(ctx):
    """Give this episode its own phase ledger, keeping shared image files reusable."""
    root = ctx.novel_dir/'series_assets'
    if root.is_symlink():
        shared = root.resolve()
        (shared/'locations').mkdir(exist_ok=True)
        phases = read_json(shared/'phases.json', {})
        root.unlink()
        root.mkdir()
        for child in shared.iterdir():
            if child.name != 'phases.json':
                (root/child.name).symlink_to(child, target_is_directory=child.is_dir())
        atomic_write_json(root/'phases.json', phases)
    root.mkdir(parents=True, exist_ok=True)
    return root/'phases.json'



def style_key(ctx):
    from novel_manga.application.profiles import load_style
    profile = read_json(ctx.novel_dir/'profile.json', {})
    return {'profile': profile, 'style': load_style(profile, ctx.novel_dir)}


def _canonical_card(ctx, asset, group, base_name, filename):
    local_phases(ctx)
    folder = ctx.novel_dir/'series_assets'/group
    if folder.is_symlink():
        old = folder.resolve(); folder.unlink(); folder.mkdir()
        for child in old.iterdir():
            (folder/child.name).symlink_to(child, target_is_directory=child.is_dir())
    folder.mkdir(exist_ok=True)
    base = folder/base_name
    if base.is_symlink(): base.unlink()
    base.mkdir(exist_ok=True)
    source = ctx.asset_snapshot_dir/asset['path']
    for suffix in ('', '.request.json', '.task.json'):
        path = source.with_name(source.name+suffix)
        if path.is_file():
            shutil.copy2(path, base/(filename+suffix))


def register_metadata(ctx, assets):
    """Register accepted draft actors/settings only in the episode snapshot."""
    from novel_manga.models.bible import Character
    bible = read_json(ctx.novel_dir/'story_bible.json')
    entities = read_json(ctx.novel_dir/'entity/entities.json', [])
    changed = False
    for asset in assets:
        name = asset['name']
        if asset['role'] == 'character':
            names = [c['name'] for c in bible.get('characters', [])]
            if name not in names:
                bible.setdefault('characters', []).append(Character(name=name, **asset['design']).model_dump())
                names.append(name); changed = True
            if not any(r.get('canonical') == name for r in entities):
                # Unregistered Bible rows already own implicit e001, e002, etc.
                number = max([len(names), *[int(r['id'][1:]) for r in entities
                    if str(r.get('id','')).startswith('e') and r['id'][1:].isdigit()]])+1
                entities.append({'id': f'e{number:03d}', 'canonical': name, 'asset_id': asset['asset_id']})
                changed = True
            _canonical_card(ctx, asset, 'characters', f'character_{names.index(name)+1:03d}', 'turnaround.jpeg')
        else:
            names = [s.split('：',1)[0] for s in bible.get('locations', [])]
            if name not in names:
                bible.setdefault('locations', []).append(name+'：'+asset['design'])
                names.append(name); changed = True
            _canonical_card(ctx, asset, 'locations', f'location_{names.index(name)+1:03d}', 'establishing.jpeg')
    if changed:
        atomic_write_json(ctx.novel_dir/'story_bible.json', bible)
        atomic_write_json(ctx.novel_dir/'entity/entities.json', entities)
        shutil.copy2(ctx.novel_dir/'story_bible.json', ctx.work_dir/'input/story_bible.json')
