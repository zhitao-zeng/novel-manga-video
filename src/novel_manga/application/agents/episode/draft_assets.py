"""Source-backed assets for a provisional episode, before any video is adopted."""
from __future__ import annotations

import copy
import fcntl
from pathlib import Path
import shutil

from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.review.endpoints import judge_settings
from novel_manga.models.bible import Character, StoryBible
from novel_manga.util import atomic_write_json, read_json
from .arguments import UnsupportedInput
from .input_records import input_receipt

IMAGE_REVIEW_POLICY = 2  # Scope appearance/style to humans, animals and empty settings.


def source_check(source, quote, shots, characters, locations):
    import json
    answer = ask_json([{'type': 'text', 'text':
        '核对尚未拍摄的小说分镜所需的缺失素材。只判断这些人物/地点确实在当前原文中，'
        '姓名、身份、年龄、性别、服装、室内外位置与原文明确事实一致。'
        '这是新增素材，不是新增剧情；原文没有详细写脸型、建筑材质等时允许合理美术设计。'
        '角色应是当前原文中的实际人物，不得借另一个角色的卡冒充。'
        '场景卡应无人，分镜有人不与无人场景卡冲突。仅检查本次申请，不审其他未修改镜头。\n'
        + json.dumps({'source': source, 'quote': quote, 'shots': shots,
                      'characters': characters, 'locations': locations}, ensure_ascii=False)}],
        obj({'consistent': {'type': 'boolean'}, 'problems': {'type': 'array', 'items': {'type': 'string'}}}),
        name='draft_asset_source', max_tokens=1200, timeout=180, settings=judge_settings())
    if answer.get('consistent') is not True or answer.get('problems'):
        raise UnsupportedInput('draft asset lacks source support: '+str(answer.get('problems')))
    return answer


def build(ctx, directory, role, name, design):
    """Use the existing supplier/style pipeline in this asset's isolated directory."""
    from dataclasses import replace
    from novel_manga.config import Settings
    from novel_manga.application.profiles import frame_spec, load_profile, load_genre, load_style, styled_bible
    from novel_manga.media.asset_style import AssetStyle, image_backend
    from novel_manga.media.asset_builder import FramedAssetFactory
    from novel_manga.media.adapters import FramedPhanRouter
    profile = load_profile(ctx.novel_dir)
    frame = frame_spec(profile)
    settings = Settings.from_env(provider='phanrouter', output_root=ctx.run_dir, admission_mode='preview')
    settings = replace(settings, width=frame['width'], height=frame['height'])
    bible = StoryBible.model_validate(read_json(ctx.novel_dir/'story_bible.json'))
    bible = bible.model_copy(update={'characters': [Character(name=name, **design)] if role == 'character' else [],
        'locations': [name+'：'+design] if role == 'location' else [], 'props': []})
    bible = styled_bible(bible, profile, ctx.novel_dir)
    style = AssetStyle.for_genre(load_genre(profile), frame_text=frame['text'],
                                style=load_style(profile, ctx.novel_dir), backend=image_backend(settings))
    factory = FramedAssetFactory(settings, FramedPhanRouter(settings, frame), style=style)
    factory.build_selected(directory, bible, {'character_001'} if role == 'character' else set(),
        {'location_001'} if role == 'location' else set(), expressions=False, prop_ids=set())
    return directory/('characters/character_001/turnaround.jpeg' if role == 'character'
                      else 'locations/location_001/establishing.jpeg')


def check_image(path, role, name, design):
    import json
    answer = ask_json([{'type': 'text', 'text':
        '检查这张实际参考图是否符合申请的素材与本书动画画风。'
        'character可指人类或动物；检查可见的物种、稳定外貌和服装，允许同一角色的多个视图。'
        '人类的瓷肌、鼻唇、发束等画风措辞只适用于人类，不能要求动物没有毛发或场景具有人脸材质；'
        '动物保留自身解剖结构，并符合所选三维/二维渲染风格。'
        '参考卡的姿势、表情、是否露齿和本镜临时持物不约束后续镜头，不能要求把情节动作拍进定妆照。'
        '单体图没有比较对象时不能断言相对身高/体型不符，未露出的部位不等于缺失。'
        'location检查建筑、场景设计和渲染风格；无人空景是没有人物，允许设计中的货箱、麻袋、绳索、'
        '布料和地面积水。按申请的时段与光源检查明暗，阴影仓库不必像人物棚拍一样明亮。'
        '不把人类皮肤/头发的风格要求套用到建筑。'
        '仅明确可见的矛盾算问题，不要求原文未描述的美术细节；每项问题说明实际看见了什么及其对应要求。\n'
        + json.dumps({'role': role, 'name': name, 'design': design}, ensure_ascii=False)}, image_part(path, 1024)],
        obj({'consistent': {'type': 'boolean'}, 'problems': {'type': 'array', 'items': {'type': 'string'}}}),
        name='draft_asset_image', max_tokens=1000, timeout=180, settings=judge_settings())
    if answer.get('consistent') is not True or answer.get('problems'):
        raise UnsupportedInput('generated asset failed image review: '+str(answer.get('problems')))
    return answer


def _asset(ctx, role, name, design, *, base_asset=None):
    """One shared image per accepted design, retaining unfinished supplier receipts."""
    root = ctx.asset_snapshot_dir/'series_assets/draft-assets'
    root.mkdir(parents=True, exist_ok=True)
    index = root/'index.json'
    from .input_assets import style_key
    style = style_key(ctx)
    from novel_manga.resource_waits import QueueWait
    with QueueWait('shared_asset_index') as queue, (root/'index.lock').open('a') as lock:
        queue.begin(); fcntl.flock(lock, fcntl.LOCK_EX); queue.finish()
        entries = read_json(index, [])
        row = next((r for r in entries if (r['role'], r['name'], r['design'], r.get('style_key'))
                    == (role, name, design, style) and r.get('base_asset')==base_asset), None)
        if row is None:
            row = {'id': f'draft_{role}_{len(entries)+1:04d}', 'role': role, 'name': name,
                   'design': design, 'style_key': style, **({'base_asset':base_asset} if base_asset else {})}
            entries.append(row)
            atomic_write_json(index, entries)
    directory = root/row['id']
    directory.mkdir(exist_ok=True)
    with QueueWait('shared_asset_candidate') as queue, (directory/'build.lock').open('a') as lock:
        queue.begin(); fcntl.flock(lock, fcntl.LOCK_EX); queue.finish()
        receipt = directory/'accepted.json'
        accepted = read_json(receipt, {})
        if accepted:
            return accepted
        rejected = read_json(directory/'rejected.json', {})
        if rejected.get('image_review_policy') == IMAGE_REVIEW_POLICY:
            raise UnsupportedInput(rejected['error'])
        built = read_json(directory/'built.json', {})
        image = ctx.asset_snapshot_dir/built['path'] if built else None
        if image is None or not image.is_file():
            image = build(ctx, directory, role, name, design)
            atomic_write_json(directory/'built.json', {'path': str(image.relative_to(ctx.asset_snapshot_dir))})
        try:
            review = check_image(image, role, name, {'source_design': design, 'production_style': style})
        except UnsupportedInput as error:
            atomic_write_json(directory/'rejected.json', {'error': str(error),
                'image_review_policy': IMAGE_REVIEW_POLICY,
                **({'previous_error': rejected['error']} if rejected else {})})
            raise
        accepted = {**row, 'asset_id': row['id'], 'path': str(image.relative_to(ctx.asset_snapshot_dir)),
                    'image_review': review, 'image_review_policy': IMAGE_REVIEW_POLICY}
        atomic_write_json(receipt, accepted)
        return accepted


def export(ctx, assets):
    """Make accepted assets discoverable without adopting or rewriting the draft."""
    from .inputs import _catalog
    from .input_assets import register_metadata
    # A prior input repair may already have localized the phase ledger.
    shared = ctx.asset_snapshot_dir/'series_assets/draft-assets'
    local = ctx.novel_dir/'series_assets/draft-assets'
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        local.symlink_to(shared, target_is_directory=True)
    register_metadata(ctx, assets)
    current = read_json(ctx.run_dir/'catalog.json', [])
    before = {r['id'] for r in current}
    added, missing, _ = _catalog(ctx, assets)
    if missing:
        raise ValueError('accepted draft asset file is missing')
    by_id = {r['id']: r for r in current}
    by_id.update({r['id']: r for r in added})
    atomic_write_json(ctx.run_dir/'catalog.json', list(by_id.values()))
    atomic_write_json(ctx.work_dir/'input/catalog.json', {'assets': list(by_id.values())})
    return sorted(set(by_id)-before)


def _has_reviewable_picture(ctx, characters, locations):
    """An older verdict can be revisited only when its current-design image exists."""
    from .input_assets import style_key
    root = ctx.asset_snapshot_dir/'series_assets/draft-assets'
    entries, style = read_json(root/'index.json', []), style_key(ctx)
    for role, group in (('character', characters), ('location', locations)):
        for name, design in group.items():
            for asset in entries:
                if (asset['role'], asset['name'], asset['design'], asset.get('style_key')) != (role, name, design, style):
                    continue
                folder = root/asset['id']
                image = read_json(folder/'built.json', {}) or read_json(folder/'accepted.json', {})
                if image.get('path') and (ctx.asset_snapshot_dir/image['path']).is_file():
                    return True
    return False


def validate_request(ctx, row):
    """Validate the same draft request for check/apply, without spending attempts."""
    if (ctx.run_dir/'prepared.json').exists() or (ctx.episode_dir/'clip_plan.json').exists():
        raise ValueError('draft assets apply only before plan adoption')
    if row.get('resume') is True:
        records = sorted((ctx.episode_dir/'work/input_repairs').glob(f"{row['clip_id']}-*.json"),
                         key=lambda p: p.stat().st_mtime)
        previous = next((read_json(p) for p in reversed(records)
                         if read_json(p, {}).get('status') in {'checking', 'incomplete'}), None)
        if not previous or not previous.get('decision'):
            raise ValueError('no unfinished draft asset request to resume')
        row = {**previous['decision'], 'reason': row.get('reason') or previous['reason']}
    source = (ctx.run_dir/'source.txt').read_text()
    quote = str(row.get('source_quote') or '').strip()
    if not quote or quote not in source or not row.get('reason'):
        raise ValueError('draft assets need a reason and verbatim source_quote')
    if set(row)-{'action', 'clip_id', 'reason', 'source_quote', 'characters', 'locations', 'resume'}:
        raise ValueError('edit the provisional draft directly; request only characters/locations here')
    draft = read_json(ctx.work_dir/'output/episode.json')
    clips = {f'clip_{i:02d}': c for i, c in enumerate(draft.get('clips', []), 1)}
    if row['clip_id'] not in clips:
        raise ValueError('draft asset request must name an existing draft clip')
    indexes = clips[row['clip_id']].get('shot_indexes', [])
    shots = [s for s in draft['chapter_script']['shots'] if s.get('index') in indexes]
    visible = {n for s in shots for n in s.get('characters', [])}
    places = {s.get('location') for s in shots}
    characters, locations = row.get('characters') or {}, row.get('locations') or {}
    if not isinstance(characters, dict) or not isinstance(locations, dict) or not (characters or locations):
        raise ValueError('request nonempty characters or locations maps')
    for name, design in characters.items():
        if name not in visible or not isinstance(design, dict) or set(design)-{'gender','age','appearance','wardrobe'}:
            raise ValueError('character request must describe a character in the target draft shots')
        Character(name=name, **design)
        if any(r.get('name') == name and r.get('role') == 'character' and not r['asset_id'].startswith('draft_')
               for r in read_json(ctx.run_dir/'catalog.json', [])):
            raise ValueError('character already has a card; use the existing card or adopted-plan phase repair')
    if any(name not in places or not isinstance(value, str) or not value.strip() for name, value in locations.items()):
        raise ValueError('location request must describe a target draft location')
    return row, source, quote, shots, characters, locations


def repair(ctx, row):
    row, source, quote, shots, characters, locations = validate_request(ctx, row)
    policy = IMAGE_REVIEW_POLICY if _has_reviewable_picture(ctx, characters, locations) else None
    receipt, record = input_receipt(ctx, row['clip_id'], row, image_review_policy=policy)
    saved = read_json(ctx.run_dir/'draft-assets.json', [])
    try:
        if not record.get('source'):
            record['source'] = source_check(source, quote, shots, characters, locations)
        record['image_review_policy'] = IMAGE_REVIEW_POLICY
        atomic_write_json(receipt, record)
        assets = [_asset(ctx, role, name, design) for role, group in
                  (('character', characters), ('location', locations)) for name, design in group.items()]
        by_id = {r['id']: r for r in saved}
        by_id.update({r['id']: r for r in assets})
        atomic_write_json(ctx.run_dir/'draft-assets.json', list(by_id.values()))
        added = export(ctx, assets)
        record.update(status='prepared', step='draft_assets', assets=[r['id'] for r in assets])
        atomic_write_json(receipt, record)
        return {'ok': True, 'changed': [], 'input_repaired': [], 'assets_added': added,
                'assets': [{k: r[k] for k in ('id','name','role','path')} for r in assets],
                'next': 'Read the actual new images in input/catalog.json, update draft reference_ids/H3 bodies, then check/render.'}
    except Exception as error:
        record.update(status='rejected' if isinstance(error, UnsupportedInput) else 'incomplete',
                      step='draft_assets', error=f'{type(error).__name__}: {error}')
        atomic_write_json(receipt, record)
        raise
