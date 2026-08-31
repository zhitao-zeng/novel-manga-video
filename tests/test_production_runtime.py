import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.admission import admission_backend_identity
from novel_manga.models import (
    Character,
    Episode,
    EpisodePlan,
    MotionBeat,
    PerformancePlan,
    ScriptTurn,
    Shot,
    ShotIntent,
    StoryBible,
    TurnDelivery,
    VisualStrategy,
)
from novel_manga.production import SeriesAssetFactory, compile_production_plan
from novel_manga.production_models import (
    AssetRecord,
    ProductionPlan,
    RuntimeUnit,
    RuntimeVisualGroup,
    SeriesAssetManifest,
)
from novel_manga.production_runtime import (
    EpisodeProductionRuntime,
    build_visual_groups,
    compile_phanrouter_runtime_motion_prompt,
    compile_seedance_native_audio_prompt,
    copy_keyframe,
    keyframe_brightness_report,
)
from novel_manga.providers.base import ImageResult


def _bible() -> StoryBible:
    return StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="二维国漫",
        palette="青蓝",
        characters=[Character(name="林晚", appearance="黑发少女", wardrobe="蓝色风衣")],
        locations=["旧书店"],
        style_fingerprint="style-1",
    )


def _assets() -> SeriesAssetManifest:
    return SeriesAssetManifest(
        style_fingerprint="style-1",
        characters=[
            AssetRecord(
                asset_id="character_001",
                kind="character",
                name="林晚",
                spec_path="series_assets/characters/character_001/spec.json",
                primary_image="series_assets/characters/character_001/turnaround.jpeg",
                prompt_sha256="a",
            )
        ],
        locations=[
            AssetRecord(
                asset_id="location_001",
                kind="location",
                name="旧书店",
                spec_path="series_assets/locations/location_001/spec.json",
                primary_image="series_assets/locations/location_001/establishing.jpeg",
                prompt_sha256="b",
            )
        ],
        voice_assignments={"narrator": "alloy", "林晚": "coral"},
    )


def test_api_visual_groups_keep_each_shot_as_one_generation_unit() -> None:
    source = "第一句。第二句。第三句。第四句。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    plan = EpisodePlan(
        video_title="第一章",
        hook="开场",
        summary="测试连续镜头",
        shots=[
            Shot(
                index=1,
                narration="第一句。",
                subtitle="第一句。",
                visual_prompt="旧书店门口",
                motion_prompt="林晚抬头",
                location="旧书店",
                source_quote="第一句。第二句。",
                turns=[
                    ScriptTurn(text="第一句。", source_quote="第一句。"),
                    ScriptTurn(text="第二句。", source_quote="第二句。"),
                ],
            ),
            Shot(
                index=2,
                narration="第三句。",
                subtitle="第三句。",
                visual_prompt="旧书店门口",
                motion_prompt="林晚转身",
                location="旧书店",
                source_quote="第三句。",
            ),
            Shot(
                index=3,
                narration="第四句。",
                subtitle="第四句。",
                visual_prompt="旧书店门口",
                motion_prompt="林晚停下",
                location="旧书店",
                source_quote="第四句。",
            ),
        ],
    )
    runtime = compile_production_plan("1_1", episode, plan, _bible(), _assets())
    groups = build_visual_groups(runtime)

    assert len(runtime.units) == 4
    assert len(groups) == 3
    assert groups[0].unit_ids == [
        "shot_001_turn_01",
        "shot_001_turn_02",
    ]
    assert groups[1].unit_ids == ["shot_002_turn_01"]
    assert groups[2].unit_ids == ["shot_003_turn_01"]
    assert "固定空间轴线" in groups[0].spatial_anchor
    assert "不是多张静态图片串联" in groups[0].motion_prompt
    assert "【本连续镜头摄影机计划】模式=locked" in groups[0].motion_prompt
    assert "整组只执行上面唯一的摄影机计划" in groups[0].motion_prompt
    assert groups[0].shot_contract is not None
    assert groups[0].shot_contract.contract_version == "hell-grind-adapted-v1"
    assert groups[0].shot_contract.external_audio_is_master is False
    assert groups[0].shot_contract.risk_focus
    assert groups[0].image_contract is not None
    assert groups[0].image_contract.purpose == "shot_start_keyframe"

    local_groups = build_visual_groups(runtime, allow_cross_shot_merge=True)
    assert len(local_groups) == 3
    assert [group.planned_seconds for group in local_groups] == [9.043, 6.75, 6.75]


def test_generation_window_splits_long_visual_groups() -> None:
    source = "第一句。第二句。第三句。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    plan = EpisodePlan(
        video_title="第一章",
        hook="开场",
        summary="短镜测试",
        shots=[
            Shot(
                index=1,
                narration="第一句。",
                subtitle="第一句。",
                visual_prompt="旧书店门口",
                motion_prompt="林晚抬头",
                location="旧书店",
                source_quote=source,
                turns=[
                    ScriptTurn(text="第一句。", source_quote="第一句。"),
                    ScriptTurn(text="第二句。", source_quote="第二句。"),
                    ScriptTurn(text="第三句。", source_quote="第三句。"),
                ],
            )
        ],
    )
    runtime = compile_production_plan("1_1", episode, plan, _bible(), _assets())
    groups = build_visual_groups(runtime, target_seconds=8.5)

    assert [len(group.unit_ids) for group in groups] == [2, 1]
    assert [group.planned_seconds for group in groups] == [8.1, 4.0]


def test_compiler_assigns_consecutive_beats_and_ignores_measured_audio_for_duration() -> None:
    source = "第一句。第二句。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="林晚站在旧书店门内",
        motion_prompt="林晚看门、推门、停手",
        characters=["林晚"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(text="第一句。", source_quote="第一句。"),
            ScriptTurn(text="第二句。", source_quote="第二句。"),
        ],
        performance_plan=PerformancePlan(
            objective="决定是否开门",
            start_state="林晚离门一步，手臂垂下",
            motion_beats=[
                MotionBeat(
                    phase="opening",
                    seconds=1.0,
                    actor="林晚",
                    target="木门",
                    action="林晚抬眼看向木门",
                    end_state="视线停在门闩",
                ),
                MotionBeat(
                    phase="development",
                    seconds=2.0,
                    actor="林晚",
                    target="门闩",
                    action="林晚伸手推开门闩",
                    end_state="门闩离开卡槽",
                ),
                MotionBeat(
                    phase="resolution",
                    seconds=1.0,
                    actor="林晚",
                    target="门外",
                    action="林晚停手看向门外",
                    end_state="她站定并留出门口视线",
                ),
            ],
            end_state="林晚站定并看向门外",
        ),
    )
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门", hook="门", summary="开门", shots=[shot]),
        _bible(),
        _assets(),
    )

    assert [unit.performance_beat_indexes for unit in runtime.units] == [[0, 1], [2]]
    assert [unit.planned_seconds for unit in runtime.units] == [4.75, 4.0]

    group = build_visual_groups(runtime)[0]

    assert group.planned_seconds == 8.85
    assert group.shot_contract is not None
    assert [beat.action for beat in group.shot_contract.beat_timeline] == [
        "林晚抬眼看向木门",
        "林晚伸手推开门闩",
        "林晚停手看向门外",
    ]
    assert group.shot_contract.beat_timeline[-1].end_seconds == 8.85
    assert "4.8秒竖屏" in compile_phanrouter_runtime_motion_prompt(runtime.units[0])


def _two_character_bible() -> StoryBible:
    bible = _bible().model_copy(deep=True)
    bible.characters.append(
        Character(name="周宇", appearance="短发青年", wardrobe="灰色夹克")
    )
    return bible


def _two_character_assets() -> SeriesAssetManifest:
    assets = _assets().model_copy(deep=True)
    assets.characters.append(
        AssetRecord(
            asset_id="character_002",
            kind="character",
            name="周宇",
            spec_path="series_assets/characters/character_002/spec.json",
            primary_image="series_assets/characters/character_002/turnaround.jpeg",
            prompt_sha256="c",
        )
    )
    assets.voice_assignments["周宇"] = "verse"
    return assets


def test_visual_group_never_crosses_visible_speakers_in_one_shot() -> None:
    source = "林晚说：“不要开门。”周宇回答：“已经晚了。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="旧书店内两人隔门对话",
        motion_prompt="两人先后回应",
        characters=["林晚", "周宇"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
                source_quote=source,
            ),
            ScriptTurn(
                role="周宇",
                speaker_name="周宇",
                text="已经晚了。",
                speaking=True,
                delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
                source_quote=source,
            ),
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门外", hook="门外", summary="门外", shots=[shot]),
        _two_character_bible(),
        _two_character_assets(),
    )
    groups = build_visual_groups(runtime)

    assert [group.unit_ids for group in groups] == [
        ["shot_001_turn_01"],
        ["shot_001_turn_02"],
    ]
    assert [group.visual_strategy for group in groups] == [
        VisualStrategy.DIRECT_ASSETS,
        VisualStrategy.DIRECT_ASSETS,
    ]
    assert all(group.shot_contract is not None for group in groups)
    assert groups[0].shot_contract.exact_dialogue == ["林晚：不要开门。"]
    assert groups[1].shot_contract.exact_dialogue == ["周宇：已经晚了。"]


def test_compiler_materializes_scene_shot_turn_and_exact_dialogue_prompt() -> None:
    source = "林晚低声说：“不要开门。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    plan = EpisodePlan(
        video_title="不要开门",
        hook="不要开门",
        summary="林晚阻止开门",
        shots=[
            Shot(
                index=1,
                narration=source,
                subtitle=source,
                visual_prompt="旧书店内",
                motion_prompt="轻微推镜",
                characters=["林晚"],
                location="旧书店",
                source_quote=source,
                turns=[
                    ScriptTurn(
                        role="林晚",
                        speaker_name="林晚",
                        text="不要开门。",
                        speaking=True,
                        source_quote=source,
                    )
                ],
            )
        ],
    )
    runtime = compile_production_plan("1_1", episode, plan, _bible(), _assets())
    assert len(runtime.scenes) == len(runtime.shots) == len(runtime.units) == 1
    unit = runtime.units[0]
    assert unit.character_asset_ids == ["character_001"]
    assert unit.location_asset_id == "location_001"
    assert unit.text in unit.motion_prompt
    assert "参考音频" in unit.motion_prompt
    assert "只有该角色开口" in unit.motion_prompt
    assert "黑发少女" in unit.keyframe_prompt
    assert "蓝色风衣" in unit.keyframe_prompt
    assert "黑发少女" in unit.motion_prompt
    assert "旧书店" in unit.keyframe_prompt
    assert "分镜视觉约束：旧书店内" in unit.keyframe_prompt
    assert unit.performance_plan is not None
    assert unit.camera_plan is not None
    assert unit.camera_plan.mode == "locked"
    assert "动作发生前一瞬" in unit.keyframe_prompt
    assert "不要复制参考图中的静态姿势" in unit.keyframe_prompt
    assert "【动作链】" in unit.motion_prompt
    assert "【行动轴】" in unit.motion_prompt


def test_camera_budget_preserves_adjacent_story_motivated_subtle_moves() -> None:
    source = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。第九句。第十句。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    sentences = [f"第{name}句。" for name in "一二三四五六七八九十"]
    shots = [
        Shot(
            index=index,
            narration=sentence,
            subtitle=sentence,
            visual_prompt=f"林晚发现门后的人并转身，{sentence}",
            motion_prompt="林晚转身并发现门后的人",
            characters=["林晚"],
            location="旧书店",
            source_quote=sentence,
            scene_job="高潮" if index == 10 else "发展",
        )
        for index, sentence in enumerate(sentences, 1)
    ]

    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门后", hook="门后", summary="门后", shots=shots),
        _bible(),
        _assets(),
    )
    modes = [unit.camera_plan.mode for unit in runtime.units if unit.camera_plan is not None]

    assert modes[:9] == ["motivated_subtle"] * 9
    assert modes[-1] == "motivated_emphasis"
    assert modes.count("locked") == 0


def test_camera_budget_prioritizes_motivated_shots_without_modulo_assignment() -> None:
    source = "第一句。第二句。第三句。第四句。第五句。第六句。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    sentences = [f"第{name}句。" for name in "一二三四五六"]
    shots = [
        Shot(
            index=index,
            narration=sentence,
            subtitle=sentence,
            visual_prompt=(
                "林晚推开门，真相揭晓" if index == 2 else "林晚站在旧书店里观察"
            ),
            motion_prompt="林晚推开门" if index == 2 else "林晚抬眼",
            characters=["林晚"],
            location="旧书店",
            source_quote=sentence,
            scene_job="高潮" if index == 2 else "发展",
        )
        for index, sentence in enumerate(sentences, 1)
    ]

    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门后", hook="门后", summary="门后", shots=shots),
        _bible(),
        _assets(),
    )
    modes = [unit.camera_plan.mode for unit in runtime.units]

    assert modes[1] == "motivated_emphasis"
    assert "受强调运镜预算约束" not in runtime.units[1].camera_plan.motivation
    assert modes.count("motivated_emphasis") == 1


def test_camera_budget_uses_narrative_reveal_even_without_motion_keywords() -> None:
    source = "第一句。第二句。第三句。"
    shots = [
        Shot(
            index=index,
            narration=sentence,
            subtitle=sentence,
            visual_prompt="林晚安静地站在旧书店",
            motion_prompt="林晚保持克制",
            characters=["林晚"],
            location="旧书店",
            source_quote=sentence,
            shot_intent=ShotIntent(
                dramatic_function="reveal" if index == 2 else "advance",
                viewer_focus="林晚终于理解门后的真相",
            ),
        )
        for index, sentence in enumerate(("第一句。", "第二句。", "第三句。"), 1)
    ]
    runtime = compile_production_plan(
        "1_1",
        Episode(
            index=1,
            source_title="第一章",
            source_text=source,
            text_count=len(source),
            source_start=0,
            source_end=len(source),
        ),
        EpisodePlan(video_title="门后", hook="门后", summary="门后", shots=shots),
        _bible(),
        _assets(),
    )

    assert [unit.camera_plan.mode for unit in runtime.units] == [
        "locked",
        "motivated_subtle",
        "locked",
    ]


def test_action_shot_compiles_physics_chain_and_environment_feedback() -> None:
    source = "楚战手中的玉石杯轰然化为粉末。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    assets = _assets().model_copy(deep=True)
    assets.locations[0].version = "v002"
    assets.locations[0].identity_invariants = ["唯一门口", "固定木桌"]
    assets.locations[0].state_variables = {"time_of_day": "bright_day"}
    plan = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(
            video_title="碎杯",
            hook="碎杯",
            summary="楚战压碎玉石杯",
            shots=[
                Shot(
                    index=1,
                    narration=source,
                    subtitle=source,
                    visual_prompt="楚战手中的玉石杯出现裂纹并化为粉末",
                    motion_prompt="楚战逐渐握紧玉石杯，杯壁碎裂",
                    characters=["林晚"],
                    location="旧书店",
                    source_quote=source,
                    performance_plan=PerformancePlan(
                        objective="让压碎玉杯改变局面",
                        start_state="林晚手持完整玉杯",
                        motion_beats=[
                            MotionBeat(
                                phase="opening",
                                trigger="听见挑衅",
                                action="林晚把玉杯举到胸前",
                                reaction="玉杯仍然完整",
                            ),
                            MotionBeat(
                                phase="development",
                                trigger="决定反击",
                                action="林晚慢慢握紧玉杯",
                                reaction="裂纹从掌心接触点扩散",
                            ),
                            MotionBeat(
                                phase="resolution",
                                trigger="玉杯碎裂",
                                action="林晚停住手臂并松开碎屑",
                                reaction="碎屑向下落定",
                            ),
                        ],
                        end_state="碎屑落地，林晚保持原站位",
                    ),
                )
            ],
        ),
        _bible(),
        assets,
    )
    unit = plan.units[0]

    assert unit.action_physics_plan is not None
    assert "裂纹" in unit.action_physics_plan.contact
    assert plan.scenes[0].spatial_contract is not None
    assert plan.scenes[0].spatial_contract.location_version_id == "location_001@v002"
    assert plan.scenes[0].spatial_contract.anchor_objects == ["唯一门口", "固定木桌"]

    group = build_visual_groups(plan, series_assets=assets)[0]
    assert group.shot_contract is not None
    assert len(group.shot_contract.beat_timeline) == 3
    assert [beat.action for beat in group.shot_contract.beat_timeline] == [
        "林晚把玉杯举到胸前",
        "林晚慢慢握紧玉杯",
        "林晚停住手臂并松开碎屑",
    ]
    assert "物理反馈" in "".join(
        beat.reaction for beat in group.shot_contract.beat_timeline
    )
    assert "裂纹" in "".join(
        beat.reaction for beat in group.shot_contract.beat_timeline
    )
    assert group.image_contract is not None
    assert group.image_contract.location_asset_version_id == "location_001@v002"

    runtime = EpisodeProductionRuntime(
        Settings(provider="phanrouter", image_model="gpt-image-2", video_model="sd2.5"),
        None,
        None,
        None,
        None,
    )  # type: ignore[arg-type]
    proxy = runtime._visual_group_proxy(group, {unit.unit_id: unit})
    adapter = runtime._build_provider_prompt_adapter(group, proxy)
    assert "裂纹" in adapter.video_prompt
    assert "环境只响应" in adapter.video_prompt


def test_missing_battle_energy_question_does_not_emit_energy_physics() -> None:
    source = "三年了，我的战气去了哪里？"
    runtime = compile_production_plan(
        "1_1",
        Episode(
            index=1,
            source_title="第一章",
            source_text=source,
            text_count=len(source),
            source_start=0,
            source_end=len(source),
        ),
        EpisodePlan(
            video_title="战气",
            hook="战气",
            summary="林晚追问力量消失",
            shots=[
                Shot(
                    index=1,
                    narration=source,
                    subtitle=source,
                    visual_prompt="林晚低头看右手",
                    motion_prompt="林晚慢慢握紧手掌后抬眼",
                    characters=["林晚"],
                    location="旧书店",
                    source_quote=source,
                )
            ],
        ),
        _bible(),
        _assets(),
    )

    assert runtime.units[0].action_physics_plan is None


def test_story_keyframe_keeps_all_visible_characters_and_exact_subject_count() -> None:
    source = "周宇挡在门前，林晚说：“不要开门。”"
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="周宇挡在门前，林晚向前半步阻止他",
        motion_prompt="林晚向前半步，周宇停在门前",
        characters=["周宇", "林晚"],
        location="旧书店",
        source_quote=source,
        visual_strategy=VisualStrategy.STORY_KEYFRAME,
        keyframe_reasons=["multi_character_blocking"],
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                source_quote=source,
            )
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        Episode(
            index=1,
            source_title="第一章",
            source_text=source,
            text_count=len(source),
            source_start=0,
            source_end=len(source),
        ),
        EpisodePlan(video_title="门", hook="门", summary="阻止开门", shots=[shot]),
        _two_character_bible(),
        _two_character_assets(),
    )

    unit = runtime.units[0]
    assert unit.character_asset_ids == ["character_002", "character_001"]
    assert "恰好画出2名具名角色" in unit.keyframe_prompt
    assert "只画林晚单人" not in unit.keyframe_prompt

    group = build_visual_groups(runtime, series_assets=_two_character_assets())[0]
    assert group.visual_strategy == VisualStrategy.STORY_KEYFRAME
    assert group.shot_contract is not None
    assert group.shot_contract.visible_asset_ids == [
        "character_002@v001",
        "character_001@v001",
    ]
    assert group.image_contract is not None
    assert group.image_contract.exact_subject_count == 2
    assert group.image_contract.subject_asset_version_ids == [
        "character_002@v001",
        "character_001@v001",
    ]
    assert "恰好保留2名已绑定具名角色" in group.keyframe_prompt


def test_story_keyframe_discovers_visible_listener_from_visual_prompt() -> None:
    source = "周宇挡在门前，林晚说：“不要开门。”"
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="周宇挡在门前，林晚向前半步阻止他",
        motion_prompt="林晚向前半步，周宇停住",
        characters=["林晚"],
        location="旧书店",
        source_quote=source,
        visual_strategy=VisualStrategy.STORY_KEYFRAME,
        keyframe_reasons=["multi_character_blocking"],
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                source_quote=source,
            )
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        Episode(
            index=1,
            source_title="第一章",
            source_text=source,
            text_count=len(source),
            source_start=0,
            source_end=len(source),
        ),
        EpisodePlan(video_title="门", hook="门", summary="门", shots=[shot]),
        _two_character_bible(),
        _two_character_assets(),
    )

    assert runtime.units[0].character_asset_ids == [
        "character_001",
        "character_002",
    ]


def test_narration_camera_direction_uses_visible_subject_not_narrator() -> None:
    source = "林晚转身离开。"
    runtime = compile_production_plan(
        "1_1",
        Episode(
            index=1,
            source_title="第一章",
            source_text=source,
            text_count=len(source),
            source_start=0,
            source_end=len(source),
        ),
        EpisodePlan(
            video_title="离开",
            hook="离开",
            summary="林晚离开",
            shots=[
                Shot(
                    index=1,
                    narration=source,
                    subtitle=source,
                    visual_prompt="林晚转身走向门口",
                    motion_prompt="林晚转身离开",
                    characters=["林晚"],
                    location="旧书店",
                    source_quote=source,
                )
            ],
        ),
        _bible(),
        _assets(),
    )

    assert "林晚始终保持" in runtime.units[0].camera_plan.screen_direction
    assert "旁白始终保持" not in runtime.units[0].camera_plan.screen_direction


def test_dialogue_speakers_keep_complementary_axis_sides_across_shots() -> None:
    source = "林晚说：“别开门。”周宇说：“我听见了。”林晚说：“退后。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    turns = (
        ("林晚", "别开门。", "林晚说：“别开门。”"),
        ("周宇", "我听见了。", "周宇说：“我听见了。”"),
        ("林晚", "退后。", "林晚说：“退后。”"),
    )
    shots = [
        Shot(
            index=index,
            narration=text,
            subtitle=text,
            visual_prompt=f"{speaker}在旧书店内说话",
            motion_prompt=f"{speaker}抬眼",
            characters=["林晚", "周宇"],
            location="旧书店",
            source_quote=quote,
            turns=[
                ScriptTurn(
                    role=speaker,
                    speaker_name=speaker,
                    text=text,
                    speaking=True,
                    source_quote=quote,
                )
            ],
        )
        for index, (speaker, text, quote) in enumerate(turns, 1)
    ]

    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门后", hook="门后", summary="门后", shots=shots),
        _two_character_bible(),
        _two_character_assets(),
    )

    assert runtime.units[0].composition_prompt != runtime.units[2].composition_prompt
    assert runtime.units[0].composition_prompt != runtime.units[1].composition_prompt
    assert "画面右侧三分线" in runtime.units[0].composition_prompt
    assert "画面右侧三分线" in runtime.units[2].composition_prompt
    assert "画面左侧三分线" in runtime.units[1].composition_prompt


def test_each_shot_contract_compiles_one_performance_and_one_camera_plan() -> None:
    source = "第一句。第二句。第三句。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shots = [
        Shot(
            index=index,
            narration=sentence,
            subtitle=sentence,
            visual_prompt="林晚转身发现门后的人",
            motion_prompt="林晚转身并发现门后的人",
            characters=["林晚"],
            location="旧书店",
            source_quote=sentence,
        )
        for index, sentence in enumerate(("第一句。", "第二句。", "第三句。"), 1)
    ]
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门后", hook="门后", summary="门后", shots=shots),
        _bible(),
        _assets(),
    )
    groups = build_visual_groups(runtime)

    assert len(groups) == 3
    for group in groups:
        assert group.motion_prompt.count("【本连续镜头摄影机计划】") == 1
        assert group.motion_prompt.count("【摄影机模式】") == 0
        assert group.motion_prompt.count("【镜头目的】") == 1
        assert group.shot_contract is not None
        assert len(group.shot_contract.beat_timeline) <= 4


def test_visual_group_keeps_offscreen_cast_but_routes_one_visible_speaker() -> None:
    source = "林晚说：“不要开门。”周宇在门外没有入画。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="林晚单人胸像，周宇只作为画外视线对象不入前景",
        motion_prompt="林晚侧头后说话",
        characters=["林晚", "周宇"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
                source_quote=source,
            ),
            ScriptTurn(
                text="门外的人没有回答。",
                source_quote=source,
            ),
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门外", hook="门外", summary="门外", shots=[shot]),
        _two_character_bible(),
        _two_character_assets(),
    )
    groups = build_visual_groups(runtime)
    group = groups[0]

    assert len(groups) == 2
    assert group.character_asset_ids == ["character_001"]
    proxy = EpisodeProductionRuntime._visual_group_proxy(
        group, {unit.unit_id: unit for unit in runtime.units}
    )
    assert proxy.character_asset_ids == ["character_001"]
    assert proxy.speaking is True
    assert proxy.role == "林晚"
    assert proxy.speaker_name == "林晚"
    assert "最终画面只保留这一名人物" in group.keyframe_prompt
    assert "不得生成背景人脸" in group.keyframe_prompt
    assert groups[1].shot_contract is not None
    assert "F-DIALOGUE-VISUALIZED" in groups[1].shot_contract.risk_focus


def test_visual_group_finishes_action_inside_delivery_window() -> None:
    source = "林晚说：“不要开门。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="林晚单人胸像",
        motion_prompt="林晚侧头后说话",
        characters=["林晚"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
                source_quote=source,
            )
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门外", hook="门外", summary="门外", shots=[shot]),
        _bible(),
        _assets(),
    )

    group = build_visual_groups(runtime)[0]

    assert group.planned_seconds == runtime.units[0].planned_seconds
    assert f"前{group.planned_seconds:.2f}秒内完成并收势" in group.motion_prompt
    assert "余下时间保持动作终点和稳定构图" in group.motion_prompt


def test_visual_group_does_not_direct_route_an_ambiguous_second_character() -> None:
    runtime = compile_production_plan(
        "1_1",
        Episode(
            index=1,
            source_title="第一章",
            source_text="林晚看着周宇说不要开门。",
            text_count=13,
            source_start=0,
            source_end=13,
        ),
        EpisodePlan(
            video_title="门外",
            hook="门外",
            summary="门外",
            shots=[
                Shot(
                    index=1,
                    narration="林晚看着周宇说不要开门。",
                    subtitle="不要开门。",
                    visual_prompt="林晚看着周宇，两人保持对话站位",
                    motion_prompt="林晚侧头后说话",
                    characters=["林晚", "周宇"],
                    location="旧书店",
                    source_quote="林晚看着周宇说不要开门。",
                    turns=[
                        ScriptTurn(
                            role="林晚",
                            speaker_name="林晚",
                            text="不要开门。",
                            speaking=True,
                            delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
                            source_quote="林晚看着周宇说不要开门。",
                        ),
                        ScriptTurn(
                            text="周宇就站在她对面。",
                            source_quote="林晚看着周宇说不要开门。",
                        ),
                    ],
                )
            ],
        ),
        _two_character_bible(),
        _two_character_assets(),
    )
    groups = build_visual_groups(runtime)

    assert groups[0].character_asset_ids == ["character_001"]
    assert groups[1].character_asset_ids == ["character_001", "character_002"]


def test_runtime_units_never_reuse_complete_turn_artifacts() -> None:
    source = "林晚低声说：“不要开门。快走。”"
    episode = Episode(
        index=1, source_title="第一章", source_text=source, text_count=len(source), source_start=0, source_end=len(source)
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="门外",
        motion_prompt="推镜",
        characters=["林晚"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(role="林晚", speaker_name="林晚", text="不要开门。", speaking=True, source_quote=source),
            ScriptTurn(role="林晚", speaker_name="林晚", text="快走。", speaking=True, source_quote=source),
        ],
    )
    plan = EpisodePlan(video_title="门外", hook="门外", summary="门外", shots=[shot])
    runtime = compile_production_plan("1_1", episode, plan, _bible(), _assets())
    assert len({unit.keyframe_path for unit in runtime.units}) == 2
    assert len({unit.raw_video_path for unit in runtime.units}) == 2
    assert len({unit.keyframe_prompt for unit in runtime.units}) == 2
    assert len({unit.motion_prompt for unit in runtime.units}) == 2


def test_copy_keyframe_keeps_all_provider_audit_sidecars(tmp_path: Path) -> None:
    source = tmp_path / "attempt/keyframe.jpeg"
    target = tmp_path / "canonical/keyframe.jpeg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"jpeg")
    for suffix in (".task.json", ".request.json", ".local.json"):
        source.with_suffix(source.suffix + suffix).write_text(suffix, encoding="utf-8")

    copy_keyframe(source, target)

    assert target.read_bytes() == b"jpeg"
    for suffix in (".task.json", ".request.json", ".local.json"):
        assert target.with_suffix(target.suffix + suffix).read_text() == suffix


def test_two_reference_prompt_uses_image_identity_without_rewriting_character() -> None:
    unit = RuntimeUnit(
        unit_id="visual_001",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="林晚",
        speaker_name="林晚",
        speaking=True,
        text="别开门。",
        emotion="警惕",
        source_quote="别开门。",
        location_asset_id="location_001",
        voice="voice-1",
        visual_prompt="旧书店",
        motion_instruction="成年女子侧头看向门外",
        motion_prompt="侧头",
        keyframe_prompt="旧书店里的林晚",
        actor_description="林晚，成年女性，黑色长发，蓝色风衣",
        composition_prompt="左侧三分线胸像",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )

    prompt = EpisodeProductionRuntime._two_reference_keyframe_prompt(unit)

    assert "图1只锁定同一角色的脸型" in prompt
    assert "林晚，成年女性，黑色长发，蓝色风衣" not in prompt
    assert "成年女子侧头看向门外" in prompt
    assert "十五岁少年" not in prompt
    assert "炭黑粗布" not in prompt


def test_two_reference_prompt_preserves_approved_3d_guoman_rendering() -> None:
    unit = RuntimeUnit(
        unit_id="visual_001",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="林晚",
        speaker_name="林晚",
        speaking=True,
        text="别开门。",
        emotion="警惕",
        source_quote="别开门。",
        location_asset_id="location_001",
        voice="voice-1",
        visual_prompt="高品质3D国漫旧书店",
        motion_instruction="成年女子侧头看向门外",
        motion_prompt="侧头",
        keyframe_prompt="3D国漫，哑光Toon-PBR材质",
        actor_description="林晚，成年女性，黑色长发，蓝色风衣",
        composition_prompt="左侧三分线胸像",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )

    prompt = EpisodeProductionRuntime._two_reference_keyframe_prompt(unit)

    assert "3D国漫剧情关键帧" in prompt
    assert "哑光无毛孔皮肤" in prompt
    assert "克制Toon-PBR" in prompt
    assert "二维线稿平涂" not in prompt
    assert "禁止半写实厚涂" not in prompt


def test_nonvisible_character_voice_keeps_voice_identity_without_driving_lips() -> None:
    source = "林晚望着门外，心里只剩一句：不要开门。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle="不要开门。",
        visual_prompt="林晚隔着旧书店窗户观察门外，嘴巴闭合",
        motion_prompt="林晚先屏住呼吸，再握紧窗帘",
        characters=["林晚"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=False,
                delivery_mode=TurnDelivery.INNER_VOICE,
                source_quote=source,
            )
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门外", hook="门外", summary="门外", shots=[shot]),
        _bible(),
        _assets(),
    )

    unit = runtime.units[0]
    assert unit.role == "林晚"
    assert unit.voice == "coral"
    assert unit.delivery_mode == TurnDelivery.INNER_VOICE
    assert unit.speaking is False
    assert "所有人物嘴巴自然闭合" in unit.keyframe_prompt
    assert "口型必须与参考音频逐字同步" not in unit.motion_prompt


def test_dialogue_unit_excludes_non_speaker_from_prompt_and_asset_ids() -> None:
    source = "周宇站在门边，林晚低声说：“不要开门。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="周宇站在门口，林晚回头阻止他",
        motion_prompt="镜头从周宇推向林晚",
        characters=["周宇", "林晚"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                source_quote=source,
            )
        ],
    )
    runtime = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门", hook="门", summary="门", shots=[shot]),
        _two_character_bible(),
        _two_character_assets(),
    )

    unit = runtime.units[0]
    assert unit.character_asset_ids == ["character_001"]
    assert "周宇" not in unit.keyframe_prompt
    assert "周宇" not in unit.motion_prompt
    assert "林晚回头阻止他" in unit.keyframe_prompt
    assert "不得出现被对话者" in unit.keyframe_prompt


def test_dialogue_reference_uses_only_uncropped_speaker_asset(tmp_path: Path) -> None:
    novel_dir = tmp_path / "novel"
    episode_dir = novel_dir / "episode"
    speaker = novel_dir / "series_assets/characters/character_001/turnaround.jpeg"
    other = novel_dir / "series_assets/characters/character_002/turnaround.jpeg"
    location = novel_dir / "series_assets/locations/location_001/establishing.jpeg"
    for path, color in ((speaker, "red"), (other, "blue"), (location, "green")):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (360, 640), color).save(path, "JPEG", quality=95)

    source = "周宇站在门边，林晚低声说：“不要开门。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    shot = Shot(
        index=1,
        narration=source,
        subtitle=source,
        visual_prompt="周宇站在门口，林晚回头阻止他",
        motion_prompt="推镜",
        characters=["周宇", "林晚"],
        location="旧书店",
        source_quote=source,
        turns=[
            ScriptTurn(
                role="林晚",
                speaker_name="林晚",
                text="不要开门。",
                speaking=True,
                source_quote=source,
            )
        ],
    )
    unit = compile_production_plan(
        "1_1",
        episode,
        EpisodePlan(video_title="门", hook="门", summary="门", shots=[shot]),
        _two_character_bible(),
        _two_character_assets(),
    ).units[0]
    factory = SeriesAssetFactory(Settings(provider="mock"), provider=None)  # type: ignore[arg-type]

    board = factory.reference_board(
        episode_dir, unit, _two_character_assets(), novel_dir
    )
    metadata = json.loads(
        board.with_suffix(board.suffix + ".request.json").read_text(encoding="utf-8")
    )

    assert board.read_bytes() == speaker.read_bytes()
    assert metadata["mode"] == "visible_speaker_identity_only"
    assert metadata["sources"] == [str(speaker)]


def test_keyframe_cast_guard_excludes_unselected_named_characters() -> None:
    source = "林晚回头看向周宇。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    plan = EpisodePlan(
        video_title="回头",
        hook="回头",
        summary="林晚回头",
        shots=[
            Shot(
                index=1,
                narration=source,
                subtitle=source,
                visual_prompt="林晚独自在旧书店回头",
                motion_prompt="林晚回头",
                characters=["林晚"],
                location="旧书店",
                source_quote=source,
            )
        ],
    )
    unit = compile_production_plan(
        "1_1", episode, plan, _two_character_bible(), _two_character_assets()
    ).units[0]

    guard = SeriesAssetFactory.keyframe_cast_guard(unit, _two_character_assets())

    assert "林晚（对应character_001定妆资产）" in guard
    assert "周宇" in guard
    assert "本镜不得出场" in guard
    assert "不具名且不抢主体的背景群众" in guard


def test_locked_series_asset_survives_prompt_revision_without_image_call(tmp_path: Path) -> None:
    class Provider:
        calls = 0

        def create_image(self, prompt, output, reference=None):
            self.calls += 1
            raise AssertionError("locked series asset should be reused")

    output = tmp_path / "turnaround.jpeg"
    output.write_bytes(b"existing-character-asset")
    meta = output.with_suffix(".jpeg.request.json")
    meta.write_text(
        json.dumps({"request_sha256": "old-prompt-identity"}), encoding="utf-8"
    )
    provider = Provider()
    factory = SeriesAssetFactory(
        Settings(provider="mock", reuse_existing_assets=True), provider
    )

    result = factory._ensure_image("revised character prompt", output)

    assert result.path == output
    assert provider.calls == 0
    saved = json.loads(meta.read_text(encoding="utf-8"))
    assert saved["origin"] == "locked-existing-asset"
    assert saved["artifact_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_series_asset_prompts_follow_3d_story_bible_without_2d_conflict() -> None:
    bible = _bible().model_copy(
        update={"visual_style": "高精度半写实3D国漫CG，PBR材质，仙侠游戏过场动画"}
    )

    character_prompt = SeriesAssetFactory._character_prompt(
        bible, "林晚", "黑发东方面孔", "蓝色丝绸古装"
    )
    expression_prompt = SeriesAssetFactory._expression_prompt(bible, "林晚")
    location_prompt = SeriesAssetFactory._location_prompt(bible, "旧书店")

    for prompt in (character_prompt, expression_prompt, location_prompt):
        assert "半写实3D国漫CG" in prompt
        assert "PBR" in prompt
        assert "不要二维插画" in prompt
        assert "不要文字" in prompt
        assert "不要文字、Logo、水印、真人照片、写实短剧、3D" not in prompt


def test_series_asset_prompts_support_25d_without_falling_into_full_3d() -> None:
    bible = _bible().model_copy(
        update={"visual_style": "国风2.5D半写实动态漫，手绘轮廓与电影体积光"}
    )

    character_prompt = SeriesAssetFactory._character_prompt(
        bible, "林晚", "黑发东方面孔", "蓝色丝绸古装"
    )
    location_prompt = SeriesAssetFactory._location_prompt(bible, "旧书店")

    for prompt in (character_prompt, location_prompt):
        assert "国风2.5D半写实动态漫" in prompt
        assert "手绘轮廓" in prompt
        assert "电影体积光" in prompt
        assert "不要纯二维平涂" in prompt
        assert "不要二维插画、墨线赛璐璐" not in prompt


def test_cartoon_style_exclusion_does_not_select_2_5d_rendering() -> None:
    bible = _bible().model_copy(
        update={
            "visual_style": (
                "二维国风卡通动画，清晰线稿与赛璐璐阴影；"
                "禁止真人照片、2.5D厚涂和三维游戏CG"
            )
        }
    )

    prompt = SeriesAssetFactory._character_prompt(
        bible, "林晚", "黑发东方面孔", "蓝色风衣"
    )

    assert "二维国风卡通动画人物资产" in prompt
    assert "两级赛璐璐阴影" in prompt
    assert "国风2.5D半写实动态漫人物资产" not in prompt


def test_production_settings_fail_closed_without_asr_backend() -> None:
    with pytest.raises(ValueError, match="NOVEL_ASR_COMMAND"):
        Settings(provider="mock", admission_mode="production").validate()


def test_latentsync_is_hard_disabled() -> None:
    with pytest.raises(ValueError, match="LatentSync is disabled"):
        Settings(video_model="LatentSync-1.6").validate()


def test_phanrouter_keyframe_uses_only_character_and_scene_references(
    tmp_path: Path,
) -> None:
    manifest = _assets()
    novel_dir = tmp_path / "novel"
    character = novel_dir / manifest.characters[0].primary_image
    location = novel_dir / manifest.locations[0].primary_image
    style_master = tmp_path / "style-master.jpeg"
    for path, color in (
        (character, (220, 205, 190)),
        (location, (235, 220, 180)),
        (style_master, (250, 230, 200)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (108, 192), color).save(path, "JPEG")
    spec = novel_dir / manifest.characters[0].spec_path
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        json.dumps(
            {
                "name": "林晚",
                "age": "二十六岁",
                "hair": "黑色齐肩短发，不束发",
                "episode_costumes": ["本集穿蓝色风衣"],
                "base_costume": "蓝色风衣",
                "palette": "蓝、黑",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    episode_dir = novel_dir / "episode"
    audio = episode_dir / "work/audio.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"wav")

    class Assets:
        calls: list[tuple[str, Path, tuple[Path, ...]]] = []

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return character

        def _ensure_image(
            self,
            prompt,
            output,
            reference=None,
            additional_references=(),
        ):
            self.calls.append((prompt, reference, additional_references))
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (108, 192), (230, 215, 185)).save(output, "JPEG")
            return ImageResult(path=output)

    unit = RuntimeUnit(
        unit_id="visual_001",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="林晚",
        speaker_name="林晚",
        speaking=True,
        delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
        text="不要开门。",
        emotion="警惕",
        source_quote="不要开门。",
        character_asset_ids=["character_001"],
        location_asset_id="location_001",
        voice="heroine",
        visual_prompt="明亮旧书店内，林晚望向画外门口",
        motion_instruction="林晚把手从门把上收回",
        motion_prompt="很长的内部导演说明",
        keyframe_prompt="很长的内部关键帧说明",
        composition_prompt="右前方四分之三胸像，人物位于左侧三分线",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )
    assets = Assets()
    runtime = EpisodeProductionRuntime(
        Settings(
            provider="phanrouter",
            image_model="gpt-image-2",
            video_model="sd2.5",
            style_master_path=style_master,
        ),
        None,
        None,
        assets,
        None,
    )  # type: ignore[arg-type]

    prepared = runtime._prepare_keyframe(
        episode_dir,
        novel_dir,
        unit,
        manifest,
    )

    assert len(assets.calls) == 1
    image_prompt, primary, additional = assets.calls[0]
    assert primary == character
    assert additional == (location,)
    assert style_master not in (primary, *additional)
    assert "图1只锁定" in image_prompt
    assert "图2只锁定" in image_prompt
    assert "发型=黑色齐肩短发，不束发" in image_prompt
    assert len(image_prompt) < 700
    runtime_prompt = prepared["unit"].motion_prompt
    assert "Seedance自行生成" in runtime_prompt
    assert "外部音频" not in runtime_prompt
    assert "不要开门。" in runtime_prompt
    assert "很长的内部导演说明" not in runtime_prompt
    assert len(runtime_prompt) < 520


def test_phanrouter_single_character_narration_puts_identity_reference_first(
    tmp_path: Path,
) -> None:
    manifest = _assets()
    novel_dir = tmp_path / "novel"
    character = novel_dir / manifest.characters[0].primary_image
    location = novel_dir / manifest.locations[0].primary_image
    for path, color in (
        (character, (220, 205, 190)),
        (location, (235, 220, 180)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (108, 192), color).save(path, "JPEG")
    spec = novel_dir / manifest.characters[0].spec_path
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        json.dumps(
            {
                "name": "林晚",
                "age": "二十六岁",
                "hair": "黑色齐肩短发，不束发",
                "episode_costumes": ["本集穿蓝色风衣"],
                "base_costume": "蓝色风衣",
                "palette": "蓝、黑",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    episode_dir = novel_dir / "episode"
    audio = episode_dir / "work/audio.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"wav")

    class Assets:
        calls: list[tuple[str, Path, tuple[Path, ...]]] = []

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return location

        def _ensure_image(
            self,
            prompt,
            output,
            reference=None,
            additional_references=(),
        ):
            self.calls.append((prompt, reference, additional_references))
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (108, 192), (230, 215, 185)).save(output, "JPEG")
            return ImageResult(path=output)

    unit = RuntimeUnit(
        unit_id="visual_001",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        delivery_mode=TurnDelivery.NARRATION,
        text="林晚仍站在门内。",
        emotion="克制",
        source_quote="林晚仍站在门内。",
        character_asset_ids=["character_001"],
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="明亮旧书店内，林晚望向画外门口",
        motion_instruction="林晚把手从门把上收回",
        motion_prompt="固定机位",
        keyframe_prompt="旧书店里的林晚",
        composition_prompt="右前方四分之三胸像，人物位于左侧三分线",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )
    assets = Assets()
    runtime = EpisodeProductionRuntime(
        Settings(
            provider="phanrouter",
            image_model="gpt-image-2",
            video_model="sd2.5",
        ),
        None,
        None,
        assets,
        None,
    )  # type: ignore[arg-type]

    runtime._prepare_keyframe(episode_dir, novel_dir, unit, manifest)

    assert len(assets.calls) == 1
    image_prompt, primary, additional = assets.calls[0]
    assert primary == character
    assert additional == (location,)
    assert "图1只锁定同一角色" in image_prompt
    assert "图2只锁定场景" in image_prompt
    assert "发型=黑色齐肩短发，不束发" in image_prompt
    assert "禁止把短发改成长发或束发" in image_prompt


def test_bright_location_gate_rejects_dark_keyframe(tmp_path: Path) -> None:
    location = tmp_path / "location.jpeg"
    dark = tmp_path / "dark.jpeg"
    bright = tmp_path / "bright.jpeg"
    Image.new("RGB", (64, 64), (190, 180, 160)).save(location, "JPEG")
    Image.new("RGB", (64, 64), (30, 30, 35)).save(dark, "JPEG")
    Image.new("RGB", (64, 64), (175, 165, 150)).save(bright, "JPEG")

    assert keyframe_brightness_report(dark, location)["status"] == "failed"
    assert keyframe_brightness_report(bright, location)["status"] == "passed"


def test_seedance_native_prompt_requests_direct_speech_without_reference_audio() -> None:
    unit = RuntimeUnit(
        unit_id="visual_001",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="林晚",
        speaker_name="林晚",
        speaking=True,
        text="不要开门。",
        emotion="警惕",
        source_quote="不要开门。",
        location_asset_id="location_001",
        location_name="旧书店",
        voice="unused",
        visual_prompt="旧书店内",
        motion_instruction="林晚望向门口",
        motion_prompt="内部导演说明",
        keyframe_prompt="门内",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/raw.mp4",
        segment_path="work/segment.mp4",
    )

    prompt = compile_seedance_native_audio_prompt(unit)

    assert "Seedance自行生成" in prompt
    assert "直接自然说原句：‘不要开门。’" in prompt
    assert "外部音频" not in prompt


def test_native_dialogue_audio_is_selected_without_tts(tmp_path: Path) -> None:
    raw_video = tmp_path / "raw.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(raw_video),
        ],
        check=True,
    )
    group = RuntimeVisualGroup(
        group_id="visual_001",
        scene_id="scene_001",
        shot_ids=["shot_001"],
        unit_ids=["shot_001_turn_01"],
        location_asset_id="location_001",
        spatial_anchor="旧书店行动轴",
        combined_text="不要开门。",
        keyframe_prompt="门内",
        motion_prompt="人物说话",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/raw.mp4",
        segment_path="work/segment.mp4",
    )

    settings = Settings(provider="phanrouter", admission_mode="preview")
    runtime = EpisodeProductionRuntime(
        settings, None, None, None, None
    )  # type: ignore[arg-type]

    selected, report = runtime._select_group_audio(
        episode_dir=tmp_path,
        group=group,
        raw_video=raw_video,
    )

    assert selected.is_file()
    assert report["selected_source"] == "native_dialogue"
    assert "asr" not in report


def test_legacy_sd25_audio_policy_is_read_only() -> None:
    settings = Settings(
        provider="phanrouter",
        admission_mode="preview",
        phanrouter_api_key="runtime-only",
        video_model="sd2.5",
        final_audio_policy="sd25_native_original",
    )

    with pytest.raises(ValueError, match="legacy audio artifacts are read-only"):
        settings.validate()


def test_phanrouter_rejects_legacy_sd20_model() -> None:
    with pytest.raises(ValueError, match="must be sd2.5"):
        Settings(
            provider="phanrouter",
            phanrouter_api_key="runtime-only",
            video_model="sd2.0",
        ).validate()


def test_admission_cache_identity_includes_video_backend() -> None:
    sd20 = admission_backend_identity(Settings(video_model="sd2.0"))
    sd25 = admission_backend_identity(Settings(video_model="sd2.5"))

    assert sd20 != sd25
    assert sd25["video_model"] == "sd2.5"


def test_admission_cache_identity_excludes_legacy_voice_map() -> None:
    identity = admission_backend_identity(Settings())

    assert "tts_model" not in identity
    assert "tts_command_sha256" not in identity
    assert identity["render_policy_revision"] == (
        "transparent-outline-subs-story-art-endpoints-v2"
    )
    assert identity["camera_policy_revision"] == "motivated-camera-v2-subtle-unbudgeted"


def test_legacy_lip_sync_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_LIP_SYNC_COMMAND", "/models/old-adapter")
    with pytest.raises(ValueError, match="lip-sync inspection/remediation is disabled"):
        Settings.from_env()


@pytest.mark.parametrize("has_metadata", [True, False])
def test_sd25_rerun_can_reuse_hash_verified_locked_keyframe(
    tmp_path: Path,
    has_metadata: bool,
) -> None:
    class Media:
        calls = 0

        def create_video(self, prompt, image, output, duration):
            self.calls += 1
            assert image.path.read_bytes() == b"locked-keyframe"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"new-sd25-video")
            return output

    class Assets:
        image_calls = 0

        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            self.image_calls += 1
            raise AssertionError("locked keyframe should be reused")

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    keyframe = episode_dir / "work/keyframe.jpeg"
    video = episode_dir / "work/clip.mp4"
    reference = episode_dir / "work/reference.jpeg"
    for path, content in (
        (audio, b"audio"),
        (keyframe, b"locked-keyframe"),
        (video, b"old-sd20-video"),
        (reference, b"reference"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="门外传来脚步声。",
        emotion="紧张",
        source_quote="门外传来脚步声。",
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="门外",
        motion_prompt="新版动作链和镜头轨迹",
        keyframe_prompt="门外的脚步声",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
    )
    if has_metadata:
        video.with_suffix(".mp4.request.json").write_text(
            json.dumps(
                {
                    "request_sha256": "old-sd20-request",
                    "keyframe_sha256": hashlib.sha256(keyframe.read_bytes()).hexdigest(),
                    "workflow": "direct-reference-audio-video-no-lip-review-v1",
                }
            ),
            encoding="utf-8",
        )
    settings = Settings(provider="mock", reuse_existing_keyframes=True)
    media = Media()
    assets = Assets(reference)
    runtime = EpisodeProductionRuntime(settings, media, None, assets, None)  # type: ignore[arg-type]

    runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert media.calls == 1
    assert assets.image_calls == 0
    assert video.read_bytes() == b"new-sd25-video"
    metadata = json.loads(video.with_suffix(".mp4.request.json").read_text())
    assert metadata["keyframe_source"] == "locked-existing-keyframe"


def test_visual_retry_reuses_first_keyframe_and_only_resubmits_video(tmp_path: Path) -> None:
    class Media:
        calls = 0
        keyframe_bytes: list[bytes] = []

        def create_video(self, prompt, image, output, duration):
            self.calls += 1
            self.keyframe_bytes.append(image.path.read_bytes())
            if self.calls == 1:
                raise RuntimeError("transient remote failure")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"retry-succeeded")
            return output

    class Assets:
        image_calls = 0

        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            self.image_calls += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"one-keyframe")
            return ImageResult(path=output)

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    reference = episode_dir / "work/reference.jpeg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    reference.write_bytes(b"reference")
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="门外传来脚步声。",
        emotion="紧张",
        source_quote="门外传来脚步声。",
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="门外",
        motion_prompt="轻微推镜",
        keyframe_prompt="门外的脚步声",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
    )
    media = Media()
    assets = Assets(reference)
    runtime = EpisodeProductionRuntime(
        Settings(provider="mock", max_unit_attempts=2),
        media,
        None,
        assets,
        None,
    )  # type: ignore[arg-type]

    runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert media.calls == 2
    assert assets.image_calls == 1
    assert media.keyframe_bytes == [b"one-keyframe", b"one-keyframe"]
    assert (episode_dir / "work/clip.mp4").read_bytes() == b"retry-succeeded"


def test_sd25_privacy_rejection_redraws_anime_keyframe_before_retry(
    tmp_path: Path,
) -> None:
    class Media:
        video_calls = 0
        image_calls = 0
        keyframe_bytes: list[bytes] = []

        def create_image(self, prompt, output, reference=None):
            self.image_calls += 1
            assert reference is not None
            assert reference.read_bytes() == b"photo-like-anime-keyframe"
            assert "明确的原创二维国风电视动画" in prompt
            assert "禁止真人照片" in prompt
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"flat-cel-anime-keyframe")
            return ImageResult(path=output)

        def create_video(self, prompt, image, output, duration):
            self.video_calls += 1
            self.keyframe_bytes.append(image.path.read_bytes())
            if self.video_calls == 1:
                raise RuntimeError(
                    "InputImageSensitiveContentDetected.PrivacyInformation"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video-safe-retry")
            return output

    class Assets:
        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"photo-like-anime-keyframe")
            return ImageResult(path=output)

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    reference = episode_dir / "work/reference.jpeg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    reference.write_bytes(b"reference")
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="门外传来脚步声。",
        emotion="紧张",
        source_quote="门外传来脚步声。",
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="明亮大厅里的古装青年",
        motion_prompt="轻微推镜",
        keyframe_prompt="门外的脚步声",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
    )
    media = Media()
    runtime = EpisodeProductionRuntime(
        Settings(provider="phanrouter", admission_mode="production", max_unit_attempts=2),
        media,
        None,
        Assets(reference),
        None,
    )  # type: ignore[arg-type]

    row = runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert media.image_calls == 1
    assert media.video_calls == 2
    assert media.keyframe_bytes == [
        b"photo-like-anime-keyframe",
        b"flat-cel-anime-keyframe",
    ]
    assert row["attempt"] == 2
    assert (episode_dir / "work/clip.mp4").read_bytes() == b"video-safe-retry"
    report = next(episode_dir.glob("work/visual_attempts/**/privacy_repair_report.json"))
    assert json.loads(report.read_text(encoding="utf-8"))["policy"] == (
        "explicit-flat-2d-anime-redraw-v1"
    )


def test_image_safety_rejection_uses_calm_anime_start_frame(tmp_path: Path) -> None:
    class Media:
        def create_video(self, prompt, image, output, duration):
            assert image.path.read_bytes() == b"safe-anime-keyframe"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            return output

    class Assets:
        calls: list[str] = []

        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                raise RuntimeError(
                    "The input or output was flagged as sensitive."
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"safe-anime-keyframe")
            return ImageResult(path=output)

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    reference = episode_dir / "work/reference.jpeg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    reference.write_bytes(b"reference")
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="众人望向锦盒。",
        emotion="紧张",
        source_quote="众人望向锦盒。",
        location_asset_id="location_001",
        character_asset_ids=["character_001"],
        voice="narrator",
        visual_prompt="拥挤人群争抢锦盒",
        motion_prompt="镜头推进",
        keyframe_prompt="拥挤人群争抢锦盒",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
    )
    assets = Assets(reference)
    runtime = EpisodeProductionRuntime(
        Settings(provider="phanrouter", admission_mode="production"),
        Media(),
        None,
        assets,
        None,
    )  # type: ignore[arg-type]

    runtime._prepare_visual(episode_dir, tmp_path, unit, _assets())

    assert len(assets.calls) == 2
    assert "拥挤人群争抢锦盒" in assets.calls[0]
    assert "拥挤人群争抢锦盒" not in assets.calls[1]
    assert "明亮高完成度二维国风电视动画" in assets.calls[1]
    report = next(
        episode_dir.glob("work/visual_attempts/**/image_safety_fallback_report.json")
    )
    assert json.loads(report.read_text(encoding="utf-8"))["policy"] == (
        "calm-single-subject-anime-start-frame-v1"
    )


def test_visual_retry_uses_local_motion_fallback_after_two_remote_failures(
    tmp_path: Path,
) -> None:
    class Media:
        calls = 0

        def create_video(self, prompt, image, output, duration):
            self.calls += 1
            raise RuntimeError("remote policy rejection")

    class Assets:
        image_calls = 0

        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            self.image_calls += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"one-keyframe")
            return ImageResult(path=output)

    class Renderer:
        calls = 0

        def _silent_card_segment(self, image, output, duration):
            self.calls += 1
            assert image.read_bytes() == b"one-keyframe"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"local-motion-fallback")
            return output

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    reference = episode_dir / "work/reference.jpeg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    reference.write_bytes(b"reference")
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="门外传来脚步声。",
        emotion="紧张",
        source_quote="门外传来脚步声。",
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="门外",
        motion_prompt="轻微推镜",
        keyframe_prompt="门外的脚步声",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
    )
    media = Media()
    assets = Assets(reference)
    renderer = Renderer()
    runtime = EpisodeProductionRuntime(
        Settings(provider="mock", max_unit_attempts=2),
        media,
        renderer,
        assets,
        None,
    )  # type: ignore[arg-type]

    row = runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert media.calls == 2
    assert assets.image_calls == 1
    assert renderer.calls == 1
    assert row["visual_source"].startswith("local-keyframe-motion-fallback")
    metadata = json.loads(
        (episode_dir / "work/clip.mp4.request.json").read_text(encoding="utf-8")
    )
    assert metadata["visual_source"].startswith("local-keyframe-motion-fallback")


def test_production_visual_failure_never_uses_static_fallback(tmp_path: Path) -> None:
    class Media:
        def create_video(self, prompt, image, output, duration):
            raise RuntimeError("remote policy rejection")

    class Assets:
        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"one-keyframe")
            return ImageResult(path=output)

    class Renderer:
        def _silent_card_segment(self, image, output, duration):
            raise AssertionError("production must not create a static fallback")

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    reference = episode_dir / "work/reference.jpeg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    reference.write_bytes(b"reference")
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="门外传来脚步声。",
        emotion="紧张",
        source_quote="门外传来脚步声。",
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="门外",
        motion_prompt="轻微推镜",
        keyframe_prompt="门外的脚步声",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
    )
    runtime = EpisodeProductionRuntime(
        Settings(provider="mock", admission_mode="production", max_unit_attempts=2),
        Media(),
        Renderer(),
        Assets(reference),
        None,
    )  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="static fallback is forbidden"):
        runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]


def test_cover_uses_art_title_and_forbids_dialogue_copy() -> None:
    episode = Episode(
        index=1,
        source_title="第一章 雨停以后",
        source_text="林晚走进雨夜书店。",
        text_count=9,
        source_start=0,
        source_end=9,
    )
    episode_plan = EpisodePlan(
        video_title="雨后小故事：第一章 雨停以后",
        hook="她在书店认出了失踪多年的故人",
        summary="两人在雨夜书店对峙",
        shots=[
            Shot(
                index=1,
                narration="林晚走进雨夜书店。",
                subtitle="林晚走进雨夜书店。",
                visual_prompt="雨夜书店",
                motion_prompt="镜头推进",
                source_quote="林晚走进雨夜书店。",
            )
        ],
    )

    prompt, art_title, episode_label = EpisodeProductionRuntime._cover_prompt(
        bible=_bible().model_copy(update={"novel_title": "雨后小故事"}),
        episode=episode,
        episode_plan=episode_plan,
    )

    assert art_title == "雨停以后"
    assert episode_label == "第01集"
    assert "独立传播封面无字底图" in prompt
    assert "不得生成任何文字" in prompt
    assert "排版引擎精确绘制" in prompt
    assert "雨停以后" not in prompt
    assert "禁止出现旁白、人物台词、对白、字幕" in prompt


def test_cover_reference_prefers_early_character_rich_unit() -> None:
    def unit(unit_id: str, characters: list[str], speaking: bool = False) -> RuntimeUnit:
        return RuntimeUnit(
            unit_id=unit_id,
            episode_id="1_1",
            scene_id="scene_001",
            shot_id=f"shot_{unit_id[-1]}",
            shot_index=int(unit_id[-1]),
            turn_index=1,
            role="林晚" if speaking else "narrator",
            speaker_name="林晚" if speaking else "旁白",
            speaking=speaking,
            text="不要走。",
            emotion="紧张",
            source_quote="不要走。",
            character_asset_ids=characters,
            location_asset_id="location_001",
            voice="coral" if speaking else "narrator",
            visual_prompt="雨夜书店",
            motion_prompt="参考音频，仅林晚说：不要走。" if speaking else "镜头推进",
            keyframe_prompt="雨夜书店",
            keyframe_path=f"work/{unit_id}.jpeg",
            raw_video_path=f"work/{unit_id}.mp4",
            segment_path=f"work/{unit_id}_segment.mp4",
        )

    selected = EpisodeProductionRuntime._select_cover_unit(
        ProductionPlan(
            video_id="1_1",
            source_title="第一章",
            source_text_sha256="a",
            style_fingerprint="style-1",
            scenes=[
                {
                    "scene_id": "scene_001",
                    "index": 1,
                    "location_asset_id": "location_001",
                    "narrative_job": "冲突",
                    "shot_ids": ["shot_1", "shot_2", "shot_3"],
                }
            ],
            shots=[
                {
                    "shot_id": f"shot_{index}",
                    "scene_id": "scene_001",
                    "index": index,
                    "narrative_job": "冲突",
                    "location_asset_id": "location_001",
                    "source_quote": "不要走。",
                    "unit_ids": [f"unit_{index}"],
                }
                for index in range(1, 4)
            ],
            units=[
                unit("unit_1", []),
                unit("unit_2", ["character_001", "character_002"]),
                unit("unit_3", ["character_001", "character_002"]),
            ],
        )
    )

    assert selected.unit_id == "unit_2"


def test_endpoint_cards_use_cover_and_final_story_keyframe(tmp_path: Path) -> None:
    class Renderer:
        normalized: tuple[Path, Path] | None = None
        card_background: Path | None = None

        def normalize_jpeg(self, source: Path, output: Path):
            self.normalized = (source, output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(source.read_bytes())
            return output

        def make_card(self, background, output, novel_title, label, subtitle):
            self.card_background = background
            output.write_bytes(b"ending")
            return output

    episode_dir = tmp_path / "episode"
    cover = episode_dir / "cover.jpeg"
    final_keyframe = episode_dir / "work/keyframes/final.jpeg"
    ending = episode_dir / "ending.jpeg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    final_keyframe.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"dedicated-cover")
    final_keyframe.write_bytes(b"final-story-art")
    unit = RuntimeUnit(
        unit_id="shot_010_turn_01",
        episode_id="1_1",
        scene_id="scene_010",
        shot_id="shot_010",
        shot_index=10,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="他们重新打开灯。",
        emotion="释然",
        source_quote="他们重新打开灯。",
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="书店亮灯",
        motion_prompt="灯光亮起",
        keyframe_prompt="最后的书店",
        keyframe_path="work/keyframes/final.jpeg",
        raw_video_path="work/raw.mp4",
        segment_path="work/segment.mp4",
    )
    plan = ProductionPlan(
        video_id="1_1",
        source_title="第一章",
        source_text_sha256="source",
        style_fingerprint="style",
        scenes=[
            {
                "scene_id": "scene_010",
                "index": 1,
                "location_asset_id": "location_001",
                "narrative_job": "和解",
                "shot_ids": ["shot_010"],
            }
        ],
        shots=[
            {
                "shot_id": "shot_010",
                "scene_id": "scene_010",
                "index": 1,
                "narrative_job": "和解",
                "location_asset_id": "location_001",
                "source_quote": "他们重新打开灯。",
                "unit_ids": [unit.unit_id],
            }
        ],
        units=[unit],
    )
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text="他们重新打开灯。",
        text_count=8,
        source_start=0,
        source_end=8,
    )
    renderer = Renderer()
    runtime = EpisodeProductionRuntime(
        Settings(provider="mock"), None, renderer, None, None  # type: ignore[arg-type]
    )

    intro, background = runtime._prepare_endpoint_cards(
        episode_dir=episode_dir,
        plan=plan,
        episode=episode,
        episode_plan=EpisodePlan(
            video_title="雨停以后",
            hook="雨停",
            summary="和解",
            shots=[
                Shot(
                    index=1,
                    narration="他们重新打开灯。",
                    subtitle="他们重新打开灯。",
                    visual_prompt="书店亮灯",
                    motion_prompt="灯光亮起",
                    source_quote="他们重新打开灯。",
                )
            ],
        ),
        bible=_bible().model_copy(update={"novel_title": "雨后小故事"}),
        cover=cover,
        ending=ending,
        episode_count=1,
    )

    assert intro.read_bytes() == b"dedicated-cover"
    assert background == final_keyframe
    assert renderer.card_background == final_keyframe
