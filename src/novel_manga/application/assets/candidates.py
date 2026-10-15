"""Shared immutable source-reviewed image candidates, using the existing API factory."""
import fcntl
from pathlib import Path
from novel_manga.util import atomic_write_json, read_json
from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.models.bible import StoryBible, Character
from novel_manga.review.endpoints import judge_settings
from novel_manga.application.agents.episode.arguments import UnsupportedInput
IMAGE_REVIEW_POLICY = 2


def style_key(ctx):
    from novel_manga.application.profiles import load_style
    profile=read_json(ctx.novel_dir/'profile.json',{})
    return {'profile':profile,'style':load_style(profile,ctx.novel_dir)}


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


def _asset(ctx, role, name, design, *, base_asset=None, asset_root=None, builder=None, checker=None,
           image_review_policy=IMAGE_REVIEW_POLICY):
    """One shared image per accepted design, retaining unfinished supplier receipts."""
    builder = builder or build
    checker = checker or check_image
    asset_root = ctx.asset_snapshot_dir if asset_root is None else Path(asset_root)
    root = asset_root/'series_assets/draft-assets'
    root.mkdir(parents=True, exist_ok=True)
    index = root/'index.json'
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
        if rejected.get('image_review_policy') == image_review_policy:
            raise UnsupportedInput(rejected['error'])
        built = read_json(directory/'built.json', {})
        image = asset_root/built['path'] if built else None
        if image is None or not image.is_file():
            image = builder(ctx, directory, role, name, design)
            atomic_write_json(directory/'built.json', {'path': str(image.relative_to(asset_root))})
        try:
            review = checker(image, role, name, {'source_design': design, 'production_style': style})
        except UnsupportedInput as error:
            atomic_write_json(directory/'rejected.json', {'error': str(error),
                'image_review_policy': image_review_policy,
                **({'previous_error': rejected['error']} if rejected else {})})
            raise
        accepted = {**row, 'asset_id': row['id'], 'path': str(image.relative_to(asset_root)),
                    'image_review': review, 'image_review_policy': image_review_policy}
        atomic_write_json(receipt, accepted)
        return accepted
