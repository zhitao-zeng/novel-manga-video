from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "runtime" / "image_prompting.py"
SPEC = importlib.util.spec_from_file_location("image_prompting", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
image_prompting = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = image_prompting
SPEC.loader.exec_module(image_prompting)


def test_default_local_prompt_policy_is_native_v5() -> None:
    assert image_prompting.LOCAL_IMAGE_PROMPT_POLICY == "native-v5"


def test_native_zimage_compiles_long_3d_prompt_without_opaque_fingerprint() -> None:
    compiled = image_prompting.compile_image_prompt(
        "高精度半写实3D国漫CG，PBR丝绸。系列风格指纹 deadbeef1234。"
        "角色资产：萧炎；黑发东方少年。只画一个人物。",
        stage="image-base",
        policy="native-v4",
    )

    assert compiled.style_family == "3d-donghua"
    assert compiled.task_kind == "character-asset"
    assert "高端国产半写实3D国漫动画正片" in compiled.positive_prompt
    assert "deadbeef1234" not in compiled.positive_prompt
    assert "画面内恰好一名人物" in compiled.positive_prompt
    assert "九宫格" not in compiled.positive_prompt
    assert "拼图" not in compiled.positive_prompt
    assert "不要" not in compiled.positive_prompt
    assert "禁止" not in compiled.positive_prompt
    assert len(compiled.positive_prompt) <= image_prompting.ZIMAGE_PROMPT_CHARACTER_BUDGET
    assert compiled.negative_prompt == ""


def test_native_qwen_edit_uses_direct_board_roles_and_blank_negative() -> None:
    compiled = image_prompting.compile_image_prompt(
        "高精度半写实3D国漫CG，PBR材质。当前叙事信息是萧炎转身。"
        "这是动作发生前一瞬。",
        stage="image-edit",
        policy="native-v4",
        reference_mode="narration_scene_and_cast",
    )

    assert compiled.task_kind == "story-keyframe"
    assert "第一栏锁定场景结构与光线" in compiled.positive_prompt
    assert "同一个透视与光照统一的连续场景" in compiled.positive_prompt
    assert "重绘一张图生视频的单幅剧情起始帧" in compiled.positive_prompt
    assert len(compiled.positive_prompt) < 700
    assert compiled.negative_prompt == " "


def test_native_2d_policy_does_not_ban_its_requested_style() -> None:
    compiled = image_prompting.compile_image_prompt(
        "精品二维赛璐璐国漫，清晰墨线、平涂色块。角色资产：少女。",
        stage="image-edit",
        policy="native-v4",
    )

    assert compiled.style_family == "2d-cel"
    assert "精品国产二维赛璐璐" in compiled.positive_prompt
    assert compiled.negative_prompt == " "


def test_legacy_policy_remains_exactly_available_for_controlled_ab() -> None:
    source = "半写实3D国漫CG角色资产"
    compiled = image_prompting.compile_image_prompt(
        source, stage="image-edit", policy="legacy"
    )

    assert compiled.positive_prompt == source
    assert "3D" in compiled.negative_prompt
    assert "赛璐璐动画" in compiled.negative_prompt


def test_native_v1_remains_reproducible_for_prior_probe() -> None:
    compiled = image_prompting.compile_image_prompt(
        "半写实3D国漫CG。角色资产：萧炎；固定外貌：黑发少年；固定服装：蓝衣。",
        stage="image-base",
        policy="native-v1",
    )

    assert "不是三视图、九宫格、设定表或拼图" in compiled.positive_prompt
    assert compiled.policy == "native-v1"


def test_native_location_prompt_is_empty_environment_and_drops_cast_palette() -> None:
    compiled = image_prompting.compile_image_prompt(
        "高精度半写实3D国漫CG。现实广场以灰白石材、深木、黑金测试碑和阴天冷灰为基底；"
        "萧炎固定黑蓝，萧媚固定珊瑚色，萧薰儿固定紫色。"
        "场景资产：乌坦城萧家广场。固定建筑结构、空间布局和光线方向。",
        stage="image-base",
        policy="native-v4",
    )

    assert compiled.policy == "native-v4"
    assert compiled.task_kind == "location-asset"
    assert "地点：乌坦城萧家广场" in compiled.positive_prompt
    assert "空场建立镜头" in compiled.positive_prompt
    assert "萧炎固定" not in compiled.positive_prompt
    assert "萧媚固定" not in compiled.positive_prompt
    assert "萧薰儿固定" not in compiled.positive_prompt
    assert "东方少年面孔" not in compiled.positive_prompt
    assert "自然人体比例" not in compiled.positive_prompt
    assert "抽象几何纹样" not in compiled.positive_prompt
    assert "哑光黑晶测试装置" in compiled.positive_prompt
    assert "平滑圆形金色晶核" in compiled.positive_prompt
    assert "测试碑" not in compiled.positive_prompt
    assert "石碑" not in compiled.positive_prompt
    assert "牌面" not in compiled.positive_prompt
    assert "旗帜" not in compiled.positive_prompt
    assert len(compiled.positive_prompt) <= image_prompting.ZIMAGE_PROMPT_CHARACTER_BUDGET


def test_native_v5_overrides_stale_game_cg_with_cinematic_realism() -> None:
    compiled = image_prompting.compile_image_prompt(
        "高精度半写实3D国漫CG，国产仙侠游戏过场，PBR皮革。"
        "角色资产：萧炎；固定外貌：黑发东方少年；固定服装：深蓝劲装。",
        stage="image-base",
        policy="native-v5",
    )

    assert compiled.style_family == "cinematic-realism"
    assert "高预算中国国漫动画电影" in compiled.positive_prompt
    assert "东方青年骨相" in compiled.positive_prompt
    assert "真实大小的虹膜与瞳孔" in compiled.positive_prompt
    assert "旧化低反光皮革" in compiled.positive_prompt
    assert "游戏过场" not in compiled.positive_prompt
    assert "PBR" not in compiled.positive_prompt
    assert len(compiled.positive_prompt) <= image_prompting.ZIMAGE_PROMPT_CHARACTER_BUDGET


def test_native_v5_qwen_repaints_reference_instead_of_preserving_plastic_style() -> None:
    compiled = image_prompting.compile_image_prompt(
        "视觉风格：高精度半写实3D国漫CG，PBR石材。"
        "这是连续长镜头的唯一动作起始帧。"
        "剧情画面：萧炎在庭院回头。"
        "本镜唯一构图要求：右前方四分之三胸像。"
        "可见说话者萧炎必须清楚位于竖屏安全区。",
        stage="image-edit",
        policy="native-v5",
        reference_mode="visible_speaker_identity_only",
    )

    assert compiled.style_family == "cinematic-realism"
    assert "艺术指导优先于参考图原有渲染" in compiled.positive_prompt
    assert "重新塑造脸部平面" in compiled.positive_prompt
    assert "整幅画必须统一重绘" in compiled.positive_prompt
    assert "高预算中国国漫动画电影" in compiled.positive_prompt
    assert "35至50mm剧情镜头" in compiled.positive_prompt
    assert "测试装置" not in compiled.positive_prompt
    assert compiled.negative_prompt == " "


@pytest.mark.parametrize(
    ("profile", "expected_anchor"),
    (
        ("cinematic-realism", "高预算中国国漫动画电影"),
        ("premium-2d-cel", "精品二维国漫番剧正片"),
        ("painterly-donghua", "高级半厚涂国漫动画"),
        ("polished-manhua", "高完成度东方彩色漫画"),
        ("ink-fantasy", "现代数字水墨东方幻想"),
        ("dark-cinematic", "暗黑东方奇幻国漫动画电影"),
    ),
)
def test_native_v5_accepts_explicit_auditable_style_profiles(
    profile: str, expected_anchor: str
) -> None:
    compiled = image_prompting.compile_image_prompt(
        "这是连续长镜头的唯一动作起始帧。剧情画面：萧炎在庭院抬眼。"
        "摄影机起始位置：右前方四分之三中近景。只画萧炎单人。",
        stage="image-edit",
        policy="native-v5",
        reference_mode="visible_speaker_identity_only",
        style_profile=profile,
    )

    assert compiled.style_family == profile
    assert expected_anchor in compiled.positive_prompt
    assert "整幅画必须统一重绘" in compiled.positive_prompt
    assert compiled.negative_prompt == " "


def test_native_v5_rejects_unknown_style_profile() -> None:
    with pytest.raises(ValueError, match="unsupported local image style profile"):
        image_prompting.compile_image_prompt(
            "剧情画面：萧炎在庭院抬眼。",
            stage="image-edit",
            policy="native-v5",
            style_profile="unknown-style",
        )


def test_style_profile_does_not_silently_change_legacy_native_policies() -> None:
    with pytest.raises(ValueError, match="require prompt policy 'native-v5'"):
        image_prompting.compile_image_prompt(
            "剧情画面：萧炎在庭院抬眼。",
            stage="image-edit",
            policy="native-v4",
            style_profile="ink-fantasy",
        )


def test_native_recognizes_continuous_visual_group_as_story_keyframe() -> None:
    compiled = image_prompting.compile_image_prompt(
        "视觉风格：高精度半写实3D国漫CG，PBR石材。"
        "这是连续长镜头的唯一动作起始帧。"
        "剧情画面：萧炎在测试区低头，黑金测试碑在左后方。"
        "本镜唯一构图要求：左前方四分之三胸像。"
        "可见说话者萧炎必须清楚位于竖屏安全区。",
        stage="image-edit",
        policy="native-v4",
        reference_mode="narration_scene_and_cast",
    )

    assert compiled.task_kind == "story-keyframe"
    assert compiled.style_family == "3d-donghua"
    assert "哑光黑晶测试装置" in compiled.positive_prompt
    assert "测试碑" not in compiled.positive_prompt
    assert "左前方四分之三胸像" in compiled.positive_prompt
    assert "最终画面只呈现萧炎" in compiled.positive_prompt


def test_native_qwen_prefers_stable_screen_direction_over_stale_camera_prose() -> None:
    compiled = image_prompting.compile_image_prompt(
        "视觉风格：高精度半写实3D国漫CG。"
        "这是连续长镜头的唯一动作起始帧。"
        "剧情画面：萧炎在广场低头。"
        "分镜视觉约束：萧炎始终在画面左侧看向右。"
        "本镜唯一构图要求：人物位于画面右侧三分线，目光朝画面左侧。"
        "可见说话者萧炎必须清楚位于竖屏安全区。",
        stage="image-edit",
        policy="native-v4",
        reference_mode="visible_speaker_identity_only",
    )

    assert "人物位于画面左侧三分线" in compiled.positive_prompt
    assert "目光朝画面右侧" in compiled.positive_prompt
    assert "人物位于画面右侧三分线" not in compiled.positive_prompt


def test_native_qwen_does_not_forward_locked_dialogue_as_drawable_text() -> None:
    compiled = image_prompting.compile_image_prompt(
        "高精度半写实3D国漫CG。"
        "分镜视觉约束：林晚在旧书店门口侧头。"
        "人物即将表达的剧情信息是“不要开门”，只把语义转化为动作。"
        "这是动作发生前一瞬的可运动起始帧：她听到脚步后肩膀绷紧。"
        "摄影机起始位置：竖屏胸像。只画林晚单人。",
        stage="image-edit",
        policy="native-v4",
        reference_mode="visible_speaker_identity_only",
    )

    assert "不要开门" not in compiled.positive_prompt
    assert "画面信息" not in compiled.positive_prompt


def test_rejects_unknown_prompt_policy() -> None:
    with pytest.raises(ValueError, match="unsupported local image prompt policy"):
        image_prompting.compile_image_prompt(
            "角色资产", stage="image-base", policy="experimental"
        )
