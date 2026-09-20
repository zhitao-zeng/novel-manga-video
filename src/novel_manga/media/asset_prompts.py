"""Shared character and location prompt rules, without IO."""
from __future__ import annotations

from novel_manga.models.bible import StoryBible

DIRECTION_2D = (
    "二维国风卡通动画人物资产，清晰且有粗细变化的手绘线稿，"
    "明快平涂和两级赛璐璐阴影，概括但稳定的五官、发型和服装形状；"
    "表情动作清楚易读，保持自然人体比例但不做幼儿Q版；"
    "不要真人照片、半写实皮肤、2.5D厚涂、三维游戏CG或PBR塑料高光"
)
DIRECTION_25D = (
    "国风2.5D半写实动态漫人物资产，保留精致手绘轮廓与可控线条，"
    "同时用真实体块、柔和材质、电影体积光和分层景深塑造空间；"
    "东方审美面孔、自然人体比例、细腻发丝、克制皮肤质感，"
    "服装与器物具有稳定结构但不做塑料游戏建模感；"
    "不要纯二维平涂、全写实真人照片、全3D游戏CG、Q版或欧美卡通"
)
DIRECTION_3D = (
    "高精度半写实3D国漫CG人物资产，国产仙侠游戏过场动画质感，"
    "东方审美面孔、自然人体比例、细腻发丝、自然皮肤明暗，"
    "PBR丝绸、皮革、石材与金属材质，电影柔光和真实景深；"
    "不要二维插画、墨线赛璐璐、真人照片、Q版或欧美卡通"
)
DIRECTION_DEFAULT = (
    "抖音国风漫剧常见的精致二维赛璐璐人物资产，清晰墨线、平涂色块、"
    "适度电影光影；不要真人照片、写实短剧、3D、Q版或欧美卡通"
)
# A style package routes by name; a package whose family has no wording here
# (live action, say) carries its own render_direction.
BY_FAMILY = {"2d": DIRECTION_2D, "2.5d": DIRECTION_25D, "3d": DIRECTION_3D}


def rendering_direction(bible: StoryBible, *, family: str = "", direction: str = "") -> str:
    """A style package states its render family and may carry its own wording;
    without either, read the family out of the style text as before."""
    if direction:
        return direction
    if family:
        return BY_FAMILY.get(family, DIRECTION_DEFAULT)
    style = bible.visual_style.casefold()
    # Positive art-direction terms must win over exclusions such as
    # "禁止2.5D厚涂". Checking the bare ``2.5D`` token first incorrectly
    # routed an explicitly 2D cartoon bible into the semi-realistic branch.
    if any(token in style for token in ("二维", "卡通", "赛璐璐", "2d")):
        return DIRECTION_2D
    if any(token in style for token in ("2.5d", "2．5d", "二点五维")):
        return DIRECTION_25D
    if any(token in style for token in ("3d", "三维", "cg", "pbr")):
        return DIRECTION_3D
    return DIRECTION_DEFAULT


def character_prompt(
    bible: StoryBible,
    name: str,
    appearance: str,
    wardrobe: str,
    *,
    visual_archetype: str = "",
    face_anchors: list[str] | None = None,
    silhouette: str = "",
    hair: str = "",
    palette: str = "",
    motion_signature: str = "",
    family: str = "",
    direction: str = "",
    fingerprint: bool = True,
) -> str:
    identity = "；".join(
        item
        for item in (
            f"戏剧类型：{visual_archetype}" if visual_archetype else "",
            f"五官锚点：{'、'.join(face_anchors or [])}" if face_anchors else "",
            f"轮廓：{silhouette}" if silhouette else "",
            f"发型结构：{hair}" if hair else "",
            f"角色专属配色：{palette}" if palette else "",
            f"惯用姿态：{motion_signature}" if motion_signature else "",
        )
        if item
    )
    prefix = (
        f"{bible.visual_style}。" + (f"系列风格指纹 {bible.style_fingerprint}。" if fingerprint else "")
        + f"{bible.palette}。"
        f"角色资产：{name}；固定外貌：{appearance}；固定服装：{wardrobe}。"
    )
    return prefix + (f"{identity}。" if identity else "") + (
        "只画一个人物且只出现一次，单人四分之三正面、从头到脚的选角定妆照；"
        "脸部占比足够识别，头脚完整，轮廓和服装主色一眼可区分，身体比例自然。"
        "双手自然放松，不拿食物、纸袋、武器或任何剧情道具。"
        "纯色简洁背景，不要多视角设定表、分身、镜像人物、局部小头像或拼贴。"
        f"{rendering_direction(bible, family=family, direction=direction)}；"
        "不要文字、Logo或水印。"
    )


def expression_prompt(
    bible: StoryBible,
    name: str,
    expression_profile: str = "",
    *,
    family: str = "",
    direction: str = "",
    fingerprint: bool = True,
) -> str:
    return (
        f"保持参考图中{ name }的脸型、年龄、发型、服装和"
        f"{bible.style_fingerprint if fingerprint else '画风'}风格完全一致。"
        "只画这个人物且只出现一次，生成四分之三正面单人胸像身份与表情锚点；"
        f"角色表情幅度：{expression_profile or '克制自然、以眼神和眉形为主'}。"
        "选择该角色最有辨识度、但尚未到剧情高潮的基础表情，"
        "眼睛、眉形和嘴部清晰无遮挡，肩颈与服装领口完整。"
        "不要表情九宫格、多头像、分身、拼贴；双手不持任何物品，简单背景，"
        f"{rendering_direction(bible, family=family, direction=direction)}；不要文字、Logo或水印。"
    )


def location_prompt(bible: StoryBible, location: str, *, family: str = "", direction: str = "",
                    fingerprint: bool = True) -> str:
    return (
        f"{bible.visual_style}。" + (f"系列风格指纹 {bible.style_fingerprint}。" if fingerprint else "")
        + f"{bible.palette}。"
        f"场景资产：{location}。固定建筑结构、空间布局、关键物品、天气、时间和光线方向。"
        "严格空场，竖屏建立镜头与对话主角度可复用背景板；前景、中景、背景层次明确，"
        "预留一至两名人物站立、走动和视线交流的表演空间，避免把核心道具放在字幕安全区。"
        "不得出现人物、人体剪影、海报人物、照片人物或镜中人。"
        f"{rendering_direction(bible, family=family, direction=direction)}；不要文字、Logo或水印。"
    )


