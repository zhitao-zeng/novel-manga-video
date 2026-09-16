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

    @classmethod
    def for_genre(cls, genre, *, frame_text='竖屏9:16'):
        return cls(frame_text=frame_text,
            card_style_suffix_3d=genre.get('card_style_suffix_3d') or CARD_STYLE_SUFFIX_3D,
            location_empty_suffix=('。主体空无一人：近景和中景不出现任何人物或人形剪影，远处允许少量模糊的背景行人'
                                   if genre.get('location_policy') == 'sparse' else LOCATION_EMPTY_SUFFIX))
