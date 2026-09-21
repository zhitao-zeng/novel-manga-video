"""Per-build asset appearance configuration; never mutates defaults for another novel."""
from __future__ import annotations

from dataclasses import dataclass

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

    @classmethod
    def for_genre(cls, genre, *, frame_text='竖屏9:16', style=None):
        style = style or {}
        return cls(frame_text=frame_text,
            render_family=style.get('render_family', ''),
            render_direction=style.get('render_direction', ''),
            prompt_fingerprint=bool(style.get('prompt_fingerprint', True)),
            declares_card_suffix=bool(style.get('card_suffix')),
            scene_style=style.get('scene_style', ''),
            scene_direction=style.get('scene_direction', ''),
            speaks_for_itself=bool(style.get('render_direction')),
            tidy_prompts=bool(style.get('tidy_prompts', False)),
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
