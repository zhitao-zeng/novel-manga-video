from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


LOCAL_IMAGE_PROMPT_POLICY = "native-v5"
DEFAULT_IMAGE_STYLE_PROFILE = "cinematic-realism"
SUPPORTED_IMAGE_STYLE_PROFILES = {
    DEFAULT_IMAGE_STYLE_PROFILE,
    "premium-2d-cel",
    "painterly-donghua",
    "polished-manhua",
    "ink-fantasy",
    "dark-cinematic",
}
SUPPORTED_PROMPT_POLICIES = {
    "legacy",
    "native-v1",
    "native-v2",
    "native-v3",
    "native-v4",
    LOCAL_IMAGE_PROMPT_POLICY,
}
ZIMAGE_PROMPT_CHARACTER_BUDGET = 650

# Kept byte-for-byte for controlled comparisons and emergency rollback.  The
# old list is intentionally not used by the native policy because it bans both
# 3D and cel animation regardless of the requested series style.
LEGACY_NEGATIVE_IMAGE_PROMPT = (
    "文字，字幕，气泡，标题，Logo，水印，签名，二维码，真人照片，真人古装剧截图，3D，"
    "赛璐璐动画，水墨，宣纸，粗糙线稿，油画厚涂，低清晰度，模糊，重复人物，分身，"
    "多余肢体，畸形手指，错误面部，裁切头部，角色设定表，拼图，分格"
)


@dataclass(frozen=True)
class CompiledImagePrompt:
    positive_prompt: str
    negative_prompt: str
    policy: str
    style_family: str
    task_kind: str
    reference_mode: str | None

    def audit_payload(self) -> dict[str, str | None]:
        return asdict(self)


def _clean_source_prompt(prompt: str) -> str:
    # A hash is useful for cache identity, but it has no visual meaning and can
    # be misread as text that should appear inside an image.
    cleaned = re.sub(
        r"系列风格指纹\s+[^，。；;\s]+[，。；;]?",
        "",
        prompt,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" 。；;\n")


def _sentences(value: str) -> list[str]:
    return [part.strip(" 。；;\n") for part in re.split(r"[。\n]+", value) if part.strip()]


def _relevant_source(prompt: str, task_kind: str) -> str:
    """Keep current-image facts and remove repeated/global prose.

    Z-Image's bundled chat-template path becomes unstable after its 512-token
    budget.  Character-count limiting is a conservative pre-tokenizer guard;
    selection happens by semantic priority so identity is never tail-truncated.
    """
    cleaned = _clean_source_prompt(prompt)
    if task_kind == "character-asset":
        start = cleaned.find("角色资产：")
        current = cleaned[start:] if start >= 0 else cleaned
        clauses = [part.strip() for part in re.split(r"[。]+", current) if part.strip()]
        preferred = [
            part
            for part in clauses
            if any(
                marker in part
                for marker in (
                    "角色资产：",
                    "固定外貌：",
                    "固定服装：",
                    "只画一个人物",
                    "单人四分之三",
                    "纯色简洁背景",
                )
            )
        ]
        return "。".join(dict.fromkeys(preferred or clauses))
    if task_kind == "character-expression":
        return "。".join(
            sentence
            for sentence in _sentences(cleaned)
            if any(
                marker in sentence
                for marker in ("保持参考图", "只画这个人物", "生成四分之三", "简单背景")
            )
        ) or cleaned
    return cleaned


def _capture(prompt: str, pattern: str) -> str:
    match = re.search(pattern, prompt, flags=re.DOTALL)
    return match.group(1).strip(" 。；;\n") if match else ""


def _positive_zimage_source(prompt: str, task_kind: str) -> str:
    """Translate constraints into positive-only Z-Image scene facts.

    Turbo has no classifier-free negative branch. Mentioning an unwanted
    concept such as a contact sheet can therefore activate it. Keep every
    sentence here drawable and desired.
    """
    cleaned = _clean_source_prompt(prompt)
    if task_kind == "character-asset":
        name = _capture(cleaned, r"角色资产：([^；。]+)") or "当前角色"
        appearance = _capture(cleaned, r"固定外貌：(.*?)(?=；固定服装：)")
        wardrobe = _capture(
            cleaned,
            r"固定服装：(.*?)(?=。只画一个人物|。纯色简洁背景|$)",
        )
        return (
            f"主体：{name}，{appearance}。服装：{wardrobe}。"
            "构图：画面内恰好一名人物，四分之三正面全身站姿，双手自然下垂，"
            "从发顶到靴底完整入镜，人物居中。背景：连续、均匀、无缝的冷灰色摄影棚背景"
        )
    if task_kind == "character-expression":
        name = _capture(cleaned, r"参考图中([^的，。]+)") or "参考角色"
        return (
            f"主体：参考图中的同一个{name}。构图：单人四分之三正面半身肖像，"
            "肩颈和服装领口完整，眼睛、眉形和自然闭合的嘴部清晰，神情克制中性；"
            "背景为连续、均匀、无缝的冷灰色"
        )
    if task_kind == "location-asset":
        name = _capture(cleaned, r"场景资产：([^。；]+)") or "当前故事的主要场景"
        place_words = tuple(
            word
            for word in (
                "广场",
                "庭院",
                "院落",
                "书店",
                "房间",
                "室内",
                "街道",
                "巷",
                "宫殿",
                "山",
                "森林",
                "学校",
                "办公室",
            )
            if word in name
        )
        environment_clauses = []
        for clause in re.split(r"[。；\n]+", cleaned):
            value = clause.strip(" ，,。；;\n")
            if not value or "场景资产：" in value:
                continue
            if place_words and not any(word in value for word in place_words):
                continue
            if any(
                marker in value
                for marker in (
                    "固定黑蓝",
                    "固定珊瑚",
                    "固定紫色",
                    "人物资产",
                    "角色资产",
                    "东方少年面孔",
                    "自然人体比例",
                    "发丝",
                )
            ):
                continue
            value = value.replace("黑金测试碑", "哑光黑晶测试装置")
            value = value.replace("测试碑", "晶石测试装置")
            value = value.replace("石碑", "晶石装置")
            environment_clauses.append(value)
        environment = "；".join(dict.fromkeys(environment_clauses[:2]))
        if environment:
            environment += "。"
        testing_device = (
            "核心道具：场地中央只有一座哑光黑晶测试装置，整体为圆角长方体，"
            "中心嵌一枚平滑圆形金色晶核，晶核周围保留宽阔完整的黑色材质面。"
            if "测试" in f"{name}{environment}"
            else "核心道具采用大块连续材质、简洁轮廓和均匀留白表面。"
        )
        return (
            f"地点：{name}。环境锚点：{environment}"
            "构图：一座空旷而完整的场景，前景以铺地、台阶或栏杆建立纵深，"
            "中景展示核心活动区域，远景由围合建筑或自然地貌收束；"
            "所有可见区域由连续建筑、铺地、植物、天空和场景道具组成。"
            f"{testing_device}"
            "建筑入口由连续纯木横梁收边，两侧布面保持完整单色；"
            "铺地、墙体和立柱采用大面积连续素面材料与简洁直线结构"
        )
    positive_clauses = []
    for clause in re.split(r"[。；\n]+", cleaned):
        value = clause.strip(" ，,。；;\n")
        if not value or any(token in value for token in ("不要", "禁止", "不得", "严禁")):
            continue
        positive_clauses.append(value)
    return "；".join(positive_clauses)


def _fit_character_budget(parts: list[str], budget: int) -> str:
    selected: list[str] = []
    used = 0
    for part in parts:
        normalized = part.strip()
        if not normalized:
            continue
        remaining = budget - used
        if remaining <= 0:
            break
        if len(normalized) > remaining:
            clauses = [
                clause.strip()
                for clause in re.split(r"(?<=[。；，,])", normalized)
                if clause.strip()
            ]
            clipped = ""
            for clause in clauses:
                if len(clipped) + len(clause) > remaining:
                    break
                clipped += clause
            if clipped:
                selected.append(clipped.rstrip("，,；;。") + "。")
            break
        selected.append(normalized)
        used += len(normalized) + 1
    return "\n".join(selected)


def _style_family(prompt: str) -> str:
    lowered = prompt.casefold()
    three_d_markers = (
        "半写实3d",
        "3d国漫",
        "3d动画",
        "三维国漫",
        "cg人物",
        "cg动画",
        "pbr",
        "游戏过场动画",
    )
    two_d_markers = (
        "二维赛璐璐",
        "二维国漫",
        "清晰墨线",
        "平涂色块",
        "2d donghua",
    )
    if any(marker in lowered for marker in three_d_markers):
        return "3d-donghua"
    if any(marker in lowered for marker in two_d_markers):
        return "2d-cel"
    return "generic-manga"


def _task_kind(prompt: str) -> str:
    if "表情锚点" in prompt:
        return "character-expression"
    if "角色资产" in prompt or "固定外貌" in prompt:
        return "character-asset"
    if "场景资产" in prompt or "建立镜头" in prompt:
        return "location-asset"
    if any(
        marker in prompt
        for marker in (
            "动作发生前一瞬",
            "分镜视觉约束",
            "摄影机起始位置",
            "当前叙事信息",
            "即将表达的剧情信息",
            "连续长镜头的唯一动作起始帧",
            "剧情画面：",
            "本镜唯一构图要求：",
        )
    ):
        return "story-keyframe"
    if "封面" in prompt:
        return "cover"
    return "general-image"


def _style_anchor(style_family: str) -> str:
    if style_family == "cinematic-realism":
        return (
            "高预算中国国漫动画电影正片的写实风格化CG剧照；成熟自然的东方青年骨相，"
            "窄长自然眼裂、真实大小的虹膜与瞳孔、清楚的眼睑厚度，鼻梁、鼻翼、嘴唇和下颌"
            "具有可信体积；皮肤保留细小毛孔、轻微色差与柔和半影；发丝成束且层次清楚；"
            "衣料可见细密织纹，皮革旧化低反光，金属只作细窄包边；中性灰蓝电影调色，"
            "自然肤色与少量暖琥珀点光，柔和侧向主光、空气透视和有层次的电影景深"
        )
    if style_family == "premium-2d-cel":
        return (
            "精品二维国漫番剧正片的赛璐璐动画画格；稳定纤细的线稿，自然不过分幼态的东方五官，"
            "清楚的发束色块与少量发丝高光，哑光布料平涂和克制纹理，二至三级受控阴影；"
            "低饱和蓝灰、暖棕和少量朱红点色，背景采用精细二维厚涂，电影构图、空气透视和清楚景深"
        )
    if style_family == "painterly-donghua":
        return (
            "高级半厚涂国漫动画概念正片画格；人物轮廓柔硬结合，面部以克制细腻笔触塑造可信体积，"
            "不依赖粗黑线稿，发丝、布料和木石保留可见但精修的画笔纹理；低饱和土色、墨蓝与暖灰，"
            "柔和天光和边缘虚实变化，背景厚涂完整，空间纵深和电影叙事感明确"
        )
    if style_family == "polished-manhua":
        return (
            "高完成度东方彩色漫画剧情格；精致稳定线稿，细腻平涂与柔和渐变结合，俊秀但比例自然的"
            "东方人物五官，发型和服装轮廓清楚；克制宝石色、清晰视觉中心、富有层次的漫画光影，"
            "背景适度简化但透视和前中远景完整，像优质竖屏连载彩漫而不是宣传海报"
        )
    if style_family == "ink-fantasy":
        return (
            "现代数字水墨东方幻想国漫画格；清晰白描人物线条与细腻水墨晕染结合，脸部五官仍准确可辨，"
            "衣料、木构和山石由深浅墨色塑造层次，只用少量靛蓝与暖金点色；轻微纸本肌理、雾层与留白，"
            "摄影机透视可信、叙事主体明确，是精修数字动画水墨而不是传统书画临摹"
        )
    if style_family == "dark-cinematic":
        return (
            "暗黑东方奇幻国漫动画电影正片画格；成熟写实风格化人物，深墨蓝、炭黑和旧铜色体系，"
            "低调侧光与少量暖色轮廓光形成强明暗关系，但暗部保留脸、服装和环境细节；"
            "雨雾、尘埃和粗粝哑光材质营造悬疑压迫感，构图克制，空间纵深明确"
        )
    if style_family == "3d-donghua":
        return (
            "高端国产半写实3D国漫动画正片（high-end Chinese 3D donghua CG）；"
            "年龄合宜的东方审美面孔，自然皮肤明暗而非塑料蜡像；细密独立发丝；"
            "PBR丝绸、皮革和金属，电影柔光、可信全局照明、克制体积光与真实景深；"
            "不是二维插画、真人摄影或游戏角色创建界面"
        )
    if style_family == "2d-cel":
        return (
            "精品国产二维赛璐璐漫剧正片质感（polished 2D donghua cel animation），"
            "稳定干净的轮廓线、精细五官、受控平涂阴影和电影光影，"
            "画面有手绘动画层次但不是粗糙草稿；不是3D渲染，不是真人摄影，不是Q版"
        )
    return (
        "统一的高品质国漫画风，东方人物设计、干净轮廓、电影光影和明确空间层次；"
        "保持同系列人物、材质、色彩和画面质感稳定"
    )


def _task_instruction(task_kind: str) -> str:
    return {
        "character-asset": (
            "生成一张单人物定妆资产成片。人物只出现一次，头脚完整，面部、发型、服装结构和"
            "材质都清晰；这是单幅角色肖像，不是三视图、九宫格、设定表或拼图"
        ),
        "character-expression": (
            "进行身份保持的人像编辑。只按要求改变表情、取景和轻微姿态；严格保持同一张脸、"
            "年龄、发型、服装剪裁、配色和渲染画风，输出一个人物的一张单幅半身肖像"
        ),
        "location-asset": (
            "生成一张无人场景建立镜头，建筑结构、空间布局、关键物品、天气、时间和主光方向"
            "清楚可复用；画面必须有前景、中景、远景和可信透视"
        ),
        "story-keyframe": (
            "重新绘制一张可直接送入图生视频的单幅剧情起始帧。只呈现动作开始前一瞬，"
            "人物站位、视线、道具关系和摄影机角度必须符合描述，并给后续动作保留空间"
        ),
        "cover": "生成一张竖屏漫剧封面底图，主角、冲突信息和视觉中心明确，保留标题安全区",
        "general-image": "生成一张完整、连续、可直接用于竖屏漫剧制作的单幅成片",
    }[task_kind]


def _zimage_prompt_v1(source: str, style_family: str, task_kind: str) -> str:
    return _fit_character_budget(
        [
            "任务：" + _task_instruction(task_kind) + "。",
            "画风：" + _style_anchor(style_family) + "。",
            "主体与构图：" + source + "。",
            (
                "完成度：竖屏9:16，视觉中心明确，透视、人体比例、双手和五官自然，"
                "头脚完整，边缘干净，明暗层次清楚。"
            ),
            (
                "输出：一张连续画面；无文字、字幕、气泡、Logo、水印、边框、分栏、拼贴、"
                "重复人物或多余肢体。"
            ),
        ],
        ZIMAGE_PROMPT_CHARACTER_BUDGET,
    )


def _positive_style_anchor(style_family: str) -> str:
    if style_family == "cinematic-realism":
        return (
            "高预算中国国漫动画电影正片的写实风格化CG剧照，成熟自然的东方青年骨相，"
            "窄长自然眼裂、真实大小的虹膜与瞳孔、清楚的眼睑厚度，鼻梁、鼻翼、嘴唇和下颌"
            "具有可信体积，皮肤保留细小毛孔、轻微色差与柔和半影，成束分层发丝，"
            "细密织纹衣料、旧化低反光皮革和细窄金属包边，中性灰蓝电影调色，"
            "自然肤色、少量暖琥珀点光、柔和侧向主光、空气透视和电影景深"
        )
    if style_family in SUPPORTED_IMAGE_STYLE_PROFILES:
        return _style_anchor(style_family)
    if style_family == "3d-donghua":
        return (
            "高端国产半写实3D国漫动画正片，风格化写实的东方审美，年龄合宜的自然五官，"
            "细腻皮肤明暗与微纹理，清楚的自然发际线和独立发丝，PBR丝绸、皮革与金属，"
            "电影柔光、可信全局照明、克制体积光、真实景深，精修动画剧照完成度"
        )
    if style_family == "2d-cel":
        return (
            "精品国产二维赛璐璐漫剧正片，稳定干净轮廓线，东方人物五官，精细平涂阴影，"
            "电影光影与丰富层次，高完成度手绘动画剧照"
        )
    return "统一高品质国漫画风，东方人物设计，干净轮廓，电影光影和明确空间层次"


def _zimage_style_anchor(style_family: str, task_kind: str) -> str:
    if task_kind != "location-asset":
        return _positive_style_anchor(style_family)
    if style_family == "cinematic-realism":
        return (
            "高预算中国国漫动画电影正片的写实风格化环境CG，东方建筑结构和尺度准确，"
            "石材有细微颗粒，木材有自然纹理，金属低反光；中性灰蓝电影调色与少量暖琥珀点光，"
            "柔和侧向主光、空气透视、清楚的前中远景和电影景深"
        )
    if style_family == "3d-donghua":
        return (
            "高端国产半写实3D国漫环境镜头，东方建筑美术，PBR石材、木材和金属，"
            "可信全局照明、电影柔光、克制体积光、空气透视和真实景深，"
            "精修动画正片的环境完成度"
        )
    if style_family == "2d-cel":
        return (
            "精品国产二维赛璐璐国漫环境镜头，稳定干净轮廓、精细平涂阴影、"
            "东方建筑美术、电影光影和清楚的前中远景层次"
        )
    if style_family in SUPPORTED_IMAGE_STYLE_PROFILES:
        return _positive_style_anchor(style_family)
    return "统一高品质国漫环境美术，可信透视，电影光影和清楚的前中远景层次"


def _positive_task_instruction(task_kind: str) -> str:
    return {
        "character-asset": "制作单一人物的系列定妆母版，身份、发型、服装结构和材质清晰稳定",
        "character-expression": "制作同一人物的单幅表情母版，脸、发型和服装身份稳定",
        "location-asset": "制作可复用的空场建立镜头，结构、布局、光线和空间层次清楚",
        "story-keyframe": "制作图生视频所需的单幅剧情起始帧，呈现动作开始前一瞬",
        "cover": "制作竖屏漫剧封面底图，主体、冲突和视觉中心明确",
        "general-image": "制作一张完整连续的竖屏漫剧画面",
    }[task_kind]


def _zimage_prompt_v2(source: str, style_family: str, task_kind: str) -> str:
    completion = (
        "成片：竖屏9:16单幅空场画面，建筑轮廓与视觉中心明确，透视可信，"
        "材质边缘干净，前景、中景、远景和明暗层次完整。"
        if task_kind == "location-asset"
        else (
            "成片：竖屏9:16单幅画面，主体轮廓清楚，视觉中心明确，透视、人体比例、"
            "双手和五官自然，边缘干净，明暗层次完整。"
        )
    )
    return _fit_character_budget(
        [
            "任务：" + _positive_task_instruction(task_kind) + "。",
            "画风：" + _zimage_style_anchor(style_family, task_kind) + "。",
            "画面：" + source + "。",
            completion,
        ],
        ZIMAGE_PROMPT_CHARACTER_BUDGET,
    )


def _cinematic_composition(task_kind: str) -> str:
    if task_kind == "character-asset":
        return (
            "用50mm人像镜头般的自然比例，人物重心放松，肩颈与双手不僵硬；"
            "定妆资产背景克制，不使用角色海报式夸张光效"
        )
    if task_kind == "character-expression":
        return "用50mm胸像比例，脸部透视自然，眼神克制，肩颈姿态有轻微不对称"
    if task_kind == "location-asset":
        return (
            "用35mm建立镜头，前景遮挡、中景活动区和远景建筑形成三层纵深，"
            "建筑尺度可信，画面保留可供人物行动的空间"
        )
    return (
        "用35至50mm剧情镜头，主体落在三分线并为视线和动作留空间；"
        "前景轻微遮挡、中景人物和远景环境形成明确纵深，构图像动画正片截帧"
    )


def _reference_instruction(task_kind: str, reference_mode: str | None) -> str:
    if reference_mode == "narration_scene_and_cast":
        return (
            "输入图是一张分栏参考板：第一栏提供场景结构和光线，其余栏提供各角色身份与服装。"
            "分栏、金色边框、裁切和面板排版只是索引，不属于最终画面；必须把这些锚点融合为"
            "一个透视统一、光照统一的连续场景，绝不能照抄成拼图"
        )
    if reference_mode == "visible_speaker_identity_only":
        return (
            "输入图只提供唯一说话角色的脸、发型、年龄、身材、服装和画风身份。"
            "保留这个人的身份，但不要保留定妆照的纯色背景、站姿、画面位置和摄影机角度"
        )
    if task_kind == "character-expression":
        return (
            "输入图是唯一人物的身份母版。严格锁定脸型、五官比例、年龄、发型、服装结构、"
            "配色和渲染方式；只执行目标中明确要求的表情、姿态和取景变化"
        )
    return (
        "输入图只用于锁定其中明确描述的身份、服装、场景结构、色彩和光线锚点。"
        "不要把输入图的边框、分栏、文字、静态姿势或原摄影机构图复制到最终画面"
    )


def _qwen_edit_prompt(
    source: str,
    style_family: str,
    task_kind: str,
    reference_mode: str | None,
) -> str:
    if task_kind == "character-expression":
        change_scope = (
            "只改变：目标表情、半身取景和为该表情所需的轻微头肩姿态。"
            "其余身份与服装信息全部不变，背景保持简洁"
        )
    elif task_kind == "story-keyframe":
        change_scope = (
            "允许改变：人物姿势、身体朝向、画面位置、景别、摄影机角度和剧情背景。"
            "必须保持：每个角色各自的脸、年龄、发型、服装设计、主色和系列渲染画风"
        )
    else:
        change_scope = (
            "只修改目标描述明确要求改变的内容；没有要求改变的角色身份、服装设计、场景锚点"
            "和系列画风全部保持不变"
        )
    return "\n".join(
        (
            "【编辑任务】" + _task_instruction(task_kind) + "。",
            "【参考图角色】" + _reference_instruction(task_kind, reference_mode) + "。",
            "【最高优先级画风】" + _style_anchor(style_family) + "。",
            "【修改范围】" + change_scope + "。",
            "【最终画面要求】" + source + "。",
            (
                "【输出检查】最终只能是一张无边框、无分栏的连续成片；人物数量必须与描述一致，"
                "不得把同一人物重复画出，不得混入参考图外的具名角色。五官、双手、人体比例和"
                "透视自然；不得出现文字、字幕、对白、气泡、Logo或水印。"
            ),
        )
    )


def _compact_identity(prompt: str) -> str:
    identity = _capture(
        prompt,
        r"参考图只锁定唯一角色身份、服装和画风：(.*?)(?=；不要复制参考图)",
    )
    if identity:
        return identity
    name = _capture(prompt, r"保持参考图中([^的，。]+)")
    return f"{name}的脸、年龄、发型、服装剪裁与配色" if name else "参考图中的角色身份与服装"


def _normalize_test_device(value: str) -> str:
    normalized = value
    for source in (
        "不带文字的黑金测试石碑或石质立柱",
        "无字的黑金测试碑",
        "黑金测试碑",
        "测试石碑",
        "测试碑",
        "石碑",
    ):
        normalized = normalized.replace(
            source,
            "哑光黑晶测试装置，中心只有一枚平滑圆形金色晶核",
        )
    return normalized


def _qwen_edit_prompt_v3(
    prompt: str,
    style_family: str,
    task_kind: str,
    reference_mode: str | None,
) -> str:
    """Compile a short direct edit command following Qwen's official recipe."""
    style = _positive_style_anchor(style_family)
    if task_kind == "character-expression":
        name = _capture(prompt, r"保持参考图中([^的，。]+)") or "人物"
        expression = _capture(prompt, r"表情锚点：(.*?)(?=，眼睛|。眼睛)") or "克制的中性神情"
        return (
            f"把图1中的{name}编辑为一张单人半身表情母版。严格保持图1的同一张脸、年龄、"
            f"发型、服装结构、配色和画风。神情：{expression}；眼睛、眉形和自然闭合的嘴部清晰。"
            f"构图：四分之三正面半身，肩颈与领口完整，连续冷灰背景。渲染：{style}。"
            "最终画面恰好一名人物。"
        )
    if task_kind == "story-keyframe":
        identity = _compact_identity(prompt)
        scene = _capture(prompt, r"场景明确为(.*?)(?=，背景|。背景)")
        visual = _capture(prompt, r"分镜视觉约束：(.*?)。") or _capture(
            prompt, r"剧情画面：(.*?)。"
        )
        director_visual = _capture(prompt, r"剧情画面：(.*?)。") or visual
        story = _capture(prompt, r"(?:人物即将表达的剧情信息|当前叙事信息)是“(.*?)”")
        start = _capture(
            prompt,
            r"这是动作发生前一瞬的(?:可运动)?起始帧：(.*?)。",
        )
        camera = _capture(prompt, r"摄影机起始位置：(.*?)。") or _capture(
            prompt, r"本镜唯一构图要求：(.*?)。"
        )
        emotion = _capture(prompt, r"情绪为(.*?)(?=，脸|。脸)")
        name = _capture(prompt, r"只画(.*?)单人") or _capture(
            prompt, r"可见说话者(.*?)必须"
        )
        if not name and "身份门禁" in prompt:
            name = _capture(prompt, r"具名角色严格限定为：(.*?)(?=。每个角色)")
        subject = name or "参考板中与剧情描述对应的角色"
        if reference_mode == "narration_scene_and_cast":
            reference = (
                "图1是分栏参考板：第一栏锁定场景结构与光线，其余栏分别锁定角色的脸、发型和服装；"
                "将这些身份锚点融合到同一个透视与光照统一的连续场景中"
            )
        else:
            reference = (
                f"图1只锁定{identity}；保持同一身份，重新安排剧情背景、姿势、景别和摄影机角度"
            )
        scene_text = _normalize_test_device(
            scene or director_visual or "原文指定的当前场景"
        )
        action_text = _normalize_test_device(
            start or director_visual or "动作开始前一瞬"
        )
        camera_text = camera or "竖屏中近景，主体和动作方向清楚"
        # Group-level screen direction is the stable continuity contract.  A
        # stale per-shot prose camera phrase must not mirror the actor.
        if "始终在画面左侧看向右" in visual:
            camera_text = (
                "行动轴同侧的右前方四分之三胸像，人物位于画面左侧三分线，"
                "目光朝画面右侧，背景锚点保留在人物右后方"
            )
        elif "始终在画面右侧看向左" in visual:
            camera_text = (
                "行动轴同侧的左前方四分之三胸像，人物位于画面右侧三分线，"
                "目光朝画面左侧，背景锚点保留在人物左后方"
            )
        camera_text = _normalize_test_device(
            camera_text.replace(
                "场景标志物",
                "哑光黑晶测试装置，中心只有一枚平滑圆形金色晶核",
            )
        )
        normalized_visual = _normalize_test_device(visual)
        if "测试装置" in normalized_visual and "测试装置" not in scene_text:
            scene_text = f"{scene_text}；{normalized_visual}"
        emotion_text = f"情绪：{emotion}。" if emotion else ""
        return (
            f"{reference}。重绘一张图生视频的单幅剧情起始帧。"
            f"场景：{scene_text}。动作时刻：{action_text}。{emotion_text}"
            f"摄影机：{camera_text}。"
            f"渲染：{style}。最终画面只呈现{subject}，人物数量与描述一致，嘴巴自然闭合，"
            "是一张无边框的连续画面；背景入口是连续纯木横梁，布面保持完整单色，"
            "测试装置、墙体和立柱都呈现大面积连续素面材质，"
            "画面内没有汉字、字母、数字或其他可读符号。"
        )
    if task_kind == "cover":
        return (
            "将图1重绘为一张竖屏漫剧封面，保持角色身份、服装与场景世界观，"
            "重新构图以强化主角、冲突和视觉中心。严格执行以下封面要求："
            f"{_clean_source_prompt(prompt)}。渲染：{style}。"
        )
    source = _relevant_source(prompt, task_kind)
    return (
        f"按以下要求直接编辑图1，只改变明确指定的内容，保持其余身份、服装、场景锚点和画风："
        f"{source}。渲染：{style}。输出一张连续画面。"
    )


def _qwen_edit_prompt_v5(
    prompt: str,
    style_family: str,
    task_kind: str,
    reference_mode: str | None,
) -> str:
    """Repaint legacy local references instead of inheriting their game-CG look."""
    direct = _qwen_edit_prompt_v3(prompt, style_family, task_kind, reference_mode)
    if task_kind == "story-keyframe" and "测试" not in prompt:
        direct = direct.replace(
            "；背景入口是连续纯木横梁，布面保持完整单色，"
            "测试装置、墙体和立柱都呈现大面积连续素面材质，",
            "；",
        )
    repaint = (
        "艺术指导优先于参考图原有渲染。图1只锁定性别、年龄段、发型轮廓、身份识别点、"
        "服装结构与必要的场景结构；允许依照下述艺术指导重新塑造脸部平面、眼鼻唇体积和皮肤质感，"
        "不要像素级锁定参考图的大眼比例与光滑脸型。"
        "不要继承图1的塑料皮肤、镜面皮衣、橡胶布料、手游PBR高光、过饱和蓝金配色、"
        "超大玻璃眼、圆润幼童脸、尖细假鼻、过曝天空、平铺空广场、站桩海报构图或伪文字。"
        "整幅画必须统一重绘，不可只套滤镜。"
    )
    profile_guard = {
        "cinematic-realism": "保持写实风格化动画质感，不要退化成真人照片、塑料游戏CG或大眼偶像脸。",
        "premium-2d-cel": "必须是二维手绘赛璐璐，不要照片、三维渲染、塑料材质、粗糙草稿或Q版大眼。",
        "painterly-donghua": "必须体现精修半厚涂笔触，不要照片、塑料三维、硬边赛璐璐色块或未完成草稿。",
        "polished-manhua": "必须是连贯剧情中的精致彩漫画格，不要三维游戏CG、真人照片、Q版或站桩海报。",
        "ink-fantasy": "必须保持人物面部可读和空间透视，不要真人照片、三维塑料感、平面日漫色块或可读书法文字。",
        "dark-cinematic": "暗部必须保留五官、服装和场景细节，不要纯黑死影、恐怖血腥、塑料游戏CG或荧光高饱和。",
    }.get(style_family, "")
    return (
        repaint
        + direct
        + ("画风边界：" + profile_guard if profile_guard else "")
        + "构图补充："
        + _cinematic_composition(task_kind)
        + "。人物肤色自然，暗部有细节，金色只作少量局部点缀。"
    )


def compile_image_prompt(
    prompt: str,
    *,
    stage: str,
    policy: str = LOCAL_IMAGE_PROMPT_POLICY,
    reference_mode: str | None = None,
    style_profile: str | None = None,
) -> CompiledImagePrompt:
    if stage not in {"image-base", "image-edit"}:
        raise ValueError(f"unsupported image stage: {stage}")
    if policy not in SUPPORTED_PROMPT_POLICIES:
        raise ValueError(
            f"unsupported local image prompt policy {policy!r}; "
            f"expected one of {sorted(SUPPORTED_PROMPT_POLICIES)}"
        )
    if style_profile is not None and style_profile not in SUPPORTED_IMAGE_STYLE_PROFILES:
        raise ValueError(
            f"unsupported local image style profile {style_profile!r}; "
            f"expected one of {sorted(SUPPORTED_IMAGE_STYLE_PROFILES)}"
        )
    if style_profile is not None and policy != "native-v5":
        raise ValueError("local image style profiles require prompt policy 'native-v5'")
    # Native-v5 is an intentional art-direction migration. Old bibles and
    # cached source prompts can still contain v4 PBR/game-CG wording, so the
    # selected policy—not stale prose—must win the style decision.
    style_family = (
        style_profile or DEFAULT_IMAGE_STYLE_PROFILE
        if policy == "native-v5"
        else _style_family(prompt)
    )
    task_kind = _task_kind(prompt)
    if policy == "legacy":
        return CompiledImagePrompt(
            positive_prompt=prompt,
            negative_prompt=LEGACY_NEGATIVE_IMAGE_PROMPT if stage == "image-edit" else "",
            policy=policy,
            style_family=style_family,
            task_kind=task_kind,
            reference_mode=reference_mode,
        )
    source = _relevant_source(prompt, task_kind)
    if stage == "image-base":
        positive = (
            _zimage_prompt_v1(source, style_family, task_kind)
            if policy == "native-v1"
            else _zimage_prompt_v2(
                _positive_zimage_source(prompt, task_kind),
                style_family,
                task_kind,
            )
        )
    else:
        positive = (
            _qwen_edit_prompt(source, style_family, task_kind, reference_mode)
            if policy in {"native-v1", "native-v2"}
            else (
                _qwen_edit_prompt_v5(
                    prompt, style_family, task_kind, reference_mode
                )
                if policy == "native-v5"
                else _qwen_edit_prompt_v3(
                    prompt, style_family, task_kind, reference_mode
                )
            )
        )
    return CompiledImagePrompt(
        positive_prompt=positive,
        # Qwen's official 2511 recipe recommends a blank negative prompt.  All
        # style exclusions therefore live in the direct edit instruction and
        # cannot silently contradict the requested style family.
        negative_prompt=" " if stage == "image-edit" else "",
        policy=policy,
        style_family=style_family,
        task_kind=task_kind,
        reference_mode=reference_mode,
    )


def infer_reference_mode(reference: str | Path | None) -> str | None:
    if not reference:
        return None
    path = Path(reference)
    metadata = path.with_suffix(path.suffix + ".request.json")
    if not metadata.is_file():
        return None
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mode = payload.get("mode")
    return str(mode) if mode else None
