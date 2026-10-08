"""Per-build asset appearance configuration; never mutates defaults for another novel."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CARD_STYLE_SUFFIX_3D = (
    "。整体必须是一眼可辨的风格化三维动画角色（国漫/皮克斯式概括造型）：眼睛略大、五官简化、皮肤光滑无毛孔、"
    "干净的三维建模材质与柔和体积光；绝不是真人照片、真实人物肖像或写实渲染"
)


LOCATION_EMPTY_SUFFIX = "。画面中绝对不出现任何人物、人影、人形剪影或车内乘客，只有空无一人的场景"


@dataclass(frozen=True)
class AssetStyle:
    frame_text: str = '竖屏9:16'
    card_style_suffix_3d: str = CARD_STYLE_SUFFIX_3D
    location_empty_suffix: str = LOCATION_EMPTY_SUFFIX
    # From the book's style package. Empty keeps the original behaviour of
    # reading the family out of the style text.
    render_family: str = ''
    render_direction: str = ''
    # The fingerprint is a cache key; a style package may keep it out of the
    # image prompt, where a hex string says nothing to the model.
    prompt_fingerprint: bool = True
    # True when the style stated card_suffix itself, so it applies whatever it
    # renders in; otherwise the wording is the 3D-only genre default.
    declares_card_suffix: bool = False
    # A room has no face, skin or hair: a style may say how its sets are drawn
    # separately from how its characters are.
    scene_style: str = ''
    scene_direction: str = ''
    # True when the style states its own render_direction, and so already says how it
    # is drawn: appending the genre's wording on top only repeats it.
    speaks_for_itself: bool = False
    # Strip the full stop a bible field already ends with before the template adds one.
    tidy_prompts: bool = False
    # Empty keeps the short 选角定妆照 wording; a package may spell the frame out.
    card_brief: str = ''
    # 模型方言层(configs/image_models/<backend>.json)提供的字段;空 = 默认(API)行为。
    prop_brief: str = ''
    negative_prompt: str = ''
    prop_negative_prompt: str = ''
    location_negative_prompt: str = ''
    # 方言声明接得住的资产类型(configs/image_models 的 local_kinds);只有列出的类型路由到本地。
    # 空表 = 不开本地时的一切如旧;方言不写 local_kinds 时是"一开全开"的旧语义。
    local_kinds: tuple = ()
    dialect_routes_subset: bool = False

    @classmethod
    def for_genre(cls, genre, *, frame_text='竖屏9:16', style=None, backend=''):
        style = dict(style or {})
        # 模型方言独立于画风包:同一画风对不同模型要说不同的话,但那是模型的耳朵,不是画风。
        # 方言文件的键覆盖画风包顶层;画风包只保留画风本身(纯美学,模型中立)。
        dialect = load_dialect(backend)
        subset = isinstance(dialect.get('local_kinds'), list)
        if dialect:
            if subset:
                # 按类型路由:方言只接管它声明接得住的类型(道具措辞/负向词),
                # 人物和场景维持画风包+API 措辞——方言里的话对 gpt-image 反而更差。
                style = {**style, **{k: v for k, v in dialect.items()
                                     if k in ('prop_brief', 'prop_negative_prompt', 'location_negative_prompt')}}
            else:
                card_background = style.get('card_background') or dialect.get('card_background_default', '')
                style = {**style, **{k: v for k, v in dialect.items() if not k.startswith('_')}}
                if card_background and style.get('card_brief'):
                    style['card_brief'] = style['card_brief'].replace('{background}', card_background)
        return cls(frame_text=frame_text,
            render_family=style.get('render_family', ''),
            render_direction=style.get('render_direction', ''),
            prompt_fingerprint=bool(style.get('prompt_fingerprint', True)),
            declares_card_suffix=bool(style.get('card_suffix')),
            scene_style=style.get('scene_style', ''),
            scene_direction=style.get('scene_direction', ''),
            speaks_for_itself=bool(style.get('render_direction')),
            tidy_prompts=bool(style.get('tidy_prompts', False)),
            card_brief=style.get('card_brief', ''),
            prop_brief=style.get('prop_brief', ''),
            negative_prompt=style.get('negative_prompt', ''),
            prop_negative_prompt=style.get('prop_negative_prompt', ''),
            location_negative_prompt=style.get('location_negative_prompt', ''),
            local_kinds=tuple(dialect.get('local_kinds') or ()),
            dialect_routes_subset=subset if dialect else False,
            # The style decides how it is drawn; the genre decides what may exist in that
            # world (scales and wing membranes in fantasy, European faces in gaslamp), so a
            # genre that states its own wording still wins.
            # The genre suffixes were all written for the 3D guoman look, so they are not
            # style-neutral: a style that renders differently states its own and wins.
            # 3d-guoman leaves card_suffix empty and defers to the genre, as before.
            card_style_suffix_3d=style.get('card_suffix') or genre.get('card_style_suffix_3d') or CARD_STYLE_SUFFIX_3D,
            location_empty_suffix=style.get('location_empty_suffix') or (
                '。主体空无一人：近景和中景不出现任何人物或人形剪影，远处允许少量模糊的背景行人'
                if genre.get('location_policy') == 'sparse' else LOCATION_EMPTY_SUFFIX))


QWEN = 'qwen'


def image_backend(settings) -> str:
    """Which image dialect applies, from where the cards come from."""
    return QWEN if getattr(settings, 'local_image_base_url', '') else ''


_DIALECTS = Path(__file__).resolve().parents[3] / 'configs' / 'image_models'


def load_dialect(backend: str) -> dict:
    """The prompt dialect of an image model (configs/image_models/<backend>.json); {} when none."""
    if not backend:
        return {}
    try:
        return json.loads((_DIALECTS / f'{backend}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def card_suffix(style: AssetStyle, bible) -> str:
    """What a card prompt appends. A style that states its own wording gets it in any
    render family - an ink-and-flat-colour look needs its suffix as much as a 3D one.
    Otherwise the genre/3D default stands, and only for a 3D look."""
    if style.declares_card_suffix:
        return style.card_style_suffix_3d
    if style.speaks_for_itself:
        return ""
    return style.card_style_suffix_3d if wants_3d_card(style, bible) else ""


def wants_3d_card(style: AssetStyle, bible) -> bool:
    """The 3D card suffix, by the book's declared family when it has one.
    Without a family, keep reading it out of the style text as before."""
    if style.render_family:
        return style.render_family == '3d'
    return "3D" in bible.visual_style or "三维" in bible.visual_style
