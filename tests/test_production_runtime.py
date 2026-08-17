import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.admission import admission_backend_identity
from novel_manga.models import (
    Character,
    Episode,
    EpisodePlan,
    ScriptTurn,
    Shot,
    StoryBible,
    TurnDelivery,
)
from novel_manga.production import SeriesAssetFactory, compile_production_plan
from novel_manga.production_models import (
    AssetRecord,
    ProductionPlan,
    RuntimeUnit,
    SeriesAssetManifest,
)
from novel_manga.production_runtime import (
    EpisodeProductionRuntime,
    build_visual_groups,
    is_direct_reference_audio_visual_cache,
    policy_safe_motion_prompt,
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


def test_visual_groups_keep_turns_but_remove_one_second_cuts() -> None:
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
    for unit, seconds in zip(runtime.units, [4.0, 4.0, 1.2, 1.1], strict=True):
        unit.audio_seconds = seconds

    groups = build_visual_groups(runtime)

    assert len(runtime.units) == 4
    assert len(groups) == 1
    assert groups[0].unit_ids == [
        "shot_001_turn_01",
        "shot_001_turn_02",
        "shot_002_turn_01",
        "shot_003_turn_01",
    ]
    assert "固定空间轴线" in groups[0].spatial_anchor
    assert "不是多张静态图片串联" in groups[0].motion_prompt
    assert "【本连续镜头摄影机计划】模式=locked" in groups[0].motion_prompt
    assert "整组只执行上面唯一的摄影机计划" in groups[0].motion_prompt
    assert groups[0].video_audio_path.endswith("visual_001.wav")


def test_h3_eight_second_window_splits_long_visual_groups() -> None:
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
        summary="H3 短镜测试",
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
    for unit in runtime.units:
        unit.audio_seconds = 3.0

    groups = build_visual_groups(runtime, target_seconds=7.5)

    assert [len(group.unit_ids) for group in groups] == [2, 1]


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


def test_compiler_materializes_scene_shot_turn_and_exact_reference_audio_prompt() -> None:
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


def test_camera_budget_defaults_to_locked_and_prevents_adjacent_moving_shots() -> None:
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

    assert modes.count("locked") >= 7
    assert sum(mode != "locked" for mode in modes) <= len(modes) // 3
    assert modes.count("motivated_emphasis") <= 1
    assert all(not (left != "locked" and right != "locked") for left, right in zip(modes, modes[1:]))


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

    assert modes[1] == "motivated_subtle"
    assert "受强调运镜预算约束" in runtime.units[1].camera_plan.motivation
    assert sum(mode != "locked" for mode in modes) <= len(modes) // 3


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

    assert runtime.units[0].composition_prompt == runtime.units[2].composition_prompt
    assert runtime.units[0].composition_prompt != runtime.units[1].composition_prompt
    assert "画面右侧三分线" in runtime.units[0].composition_prompt
    assert "画面左侧三分线" in runtime.units[1].composition_prompt


def test_visual_group_compiles_many_performances_but_only_one_camera_plan() -> None:
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
    for unit in runtime.units:
        unit.audio_seconds = 1.0

    group = build_visual_groups(runtime)[0]

    assert group.motion_prompt.count("【本连续镜头摄影机计划】") == 1
    assert group.motion_prompt.count("【摄影机模式】") == 0
    assert group.motion_prompt.count("【镜头目的】") == 3


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
    for unit in runtime.units:
        unit.audio_seconds = 2.0

    group = build_visual_groups(runtime)[0]

    assert group.character_asset_ids == ["character_001", "character_002"]
    assert group.direct_video_character_asset_ids == ["character_001"]
    proxy = EpisodeProductionRuntime._visual_group_proxy(
        group, {unit.unit_id: unit for unit in runtime.units}
    )
    assert proxy.character_asset_ids == ["character_001", "character_002"]
    assert proxy.direct_video_character_asset_ids == ["character_001"]


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
    for unit in runtime.units:
        unit.audio_seconds = 2.0

    group = build_visual_groups(runtime)[0]

    assert group.character_asset_ids == ["character_001", "character_002"]
    assert group.direct_video_character_asset_ids == []


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
    assert unit.reference_audio_required is False
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


def test_production_settings_fail_closed_without_asr_backend() -> None:
    with pytest.raises(ValueError, match="NOVEL_ASR_COMMAND"):
        Settings(provider="mock", admission_mode="production").validate()


def test_latentsync_is_hard_disabled() -> None:
    with pytest.raises(ValueError, match="LatentSync is disabled"):
        Settings(video_model="LatentSync-1.6").validate()


def test_direct_asset_strategy_requires_local_h3() -> None:
    with pytest.raises(ValueError, match="requires the command provider and MiniMax H3"):
        Settings(local_visual_strategy="h3-direct-single-character").validate()
    Settings(
        provider="command",
        video_model="MiniMax-H3-Ref2VA",
        image_command="/models/image",
        video_command="/models/h3-video",
        tts_command="/models/tts",
        local_visual_strategy="h3-direct-single-character",
    ).validate()


def test_direct_h3_assets_selects_only_one_character_dialogue_group(
    tmp_path: Path,
) -> None:
    assets = _assets()
    novel_dir = tmp_path / "novel"
    character = novel_dir / assets.characters[0].primary_image
    location = novel_dir / assets.locations[0].primary_image
    for path in (character, location):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpeg")
    runtime = EpisodeProductionRuntime(
        Settings(
            provider="command",
            video_model="MiniMax-H3-Ref2VA",
            image_command="/models/image",
            video_command="/models/h3-video",
            tts_command="/models/tts",
            local_visual_strategy="h3-direct-single-character",
        ),
        None,
        None,
        None,
        None,
    )  # type: ignore[arg-type]
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
        reference_audio_required=True,
        text="林晚低声说不要开门。",
        emotion="警惕",
        source_quote="不要开门。",
        character_asset_ids=["character_001"],
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="旧书店",
        motion_prompt="林晚转头并说话",
        keyframe_prompt="旧书店里的林晚",
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )

    assert runtime._direct_h3_assets(novel_dir, unit, assets) == (
        character,
        (location,),
    )
    assert runtime._direct_h3_assets(
        novel_dir,
        unit.model_copy(update={"reference_audio_required": False}),
        assets,
    ) is None


def test_prepare_keyframe_bypasses_qwen_for_direct_h3_assets(tmp_path: Path) -> None:
    assets = _assets()
    novel_dir = tmp_path / "novel"
    character = novel_dir / assets.characters[0].primary_image
    location = novel_dir / assets.locations[0].primary_image
    for path in (character, location):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpeg")
    episode_dir = novel_dir / "episode"
    audio = episode_dir / "work/audio.wav"
    reference = episode_dir / "work/reference.jpeg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"wav")
    reference.write_bytes(b"board")

    class Assets:
        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return reference

        def _ensure_image(self, prompt, output, reference=None):
            raise AssertionError("direct H3 mode must not generate a per-shot keyframe")

    runtime = EpisodeProductionRuntime(
        Settings(
            provider="command",
            video_model="MiniMax-H3-Ref2VA",
            image_command="/models/image",
            video_command="/models/h3-video",
            tts_command="/models/tts",
            local_visual_strategy="h3-direct-single-character",
        ),
        None,
        None,
        Assets(),
        None,
    )  # type: ignore[arg-type]
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
        reference_audio_required=True,
        text="林晚低声说不要开门。",
        emotion="警惕",
        source_quote="不要开门。",
        character_asset_ids=["character_001"],
        location_asset_id="location_001",
        voice="narrator",
        visual_prompt="旧书店",
        motion_prompt="林晚转头并说话",
        keyframe_prompt="旧书店里的林晚",
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )

    prepared = runtime._prepare_keyframe(episode_dir, novel_dir, unit, assets)

    assert prepared["selected_keyframe"] == character
    assert prepared["additional_video_images"] == (location,)
    assert prepared["visual_input_strategy"] == "h3-character-plus-location-assets"
    assert runtime._direct_h3_assets(
        novel_dir,
        unit.model_copy(
            update={"character_asset_ids": ["character_001", "character_002"]}
        ),
        assets,
    ) is None


def test_phanrouter_rejects_legacy_sd20_model() -> None:
    with pytest.raises(ValueError, match="must be sd2.5"):
        Settings(
            provider="phanrouter",
            phanrouter_api_key="runtime-only",
            video_model="sd2.0",
            tts_command="/models/tts-adapter",
        ).validate()


def test_admission_cache_identity_includes_video_backend() -> None:
    sd20 = admission_backend_identity(Settings(video_model="sd2.0"))
    sd25 = admission_backend_identity(Settings(video_model="sd2.5"))

    assert sd20 != sd25
    assert sd25["video_model"] == "sd2.5"


def test_admission_cache_identity_includes_voice_map_and_render_policy() -> None:
    dylan = admission_backend_identity(
        Settings(voice_map={"林澈": "Dylan"})
    )
    designed = admission_backend_identity(
        Settings(voice_map={"林澈": "LinChe_Deep_Adult_v1"})
    )

    assert dylan != designed
    assert designed["render_policy_revision"] == (
        "transparent-outline-subs-story-art-endpoints-v2"
    )
    assert designed["camera_policy_revision"] == "motivated-camera-v1"


def test_legacy_lip_sync_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_LIP_SYNC_COMMAND", "/models/old-adapter")
    with pytest.raises(ValueError, match="lip-sync inspection/remediation is disabled"):
        Settings.from_env()


def test_modified_or_latentsync_visual_cache_is_not_reused() -> None:
    assert is_direct_reference_audio_visual_cache({"workflow": "sd2.5-reference-audio"})
    assert not is_direct_reference_audio_visual_cache(
        {"workflow": "sd2.5-reference-audio", "postprocess": "closed-tail-crossfade"}
    )
    assert not is_direct_reference_audio_visual_cache({"backend": "latent_sync-local"})


def test_policy_safe_prompt_removes_ip_names_and_verbatim_audio_instruction() -> None:
    prompt = (
        "萧家广场，萧薰儿走向萧炎。"
        "参考音频中的可见对白严格依次为：萧薰儿：萧炎哥哥。。"
        "只有这些可见对白驱动对应角色口型；"
        "参考音频中的旁白、画外对白和内心声期间，其他人闭嘴。"
    )

    safe = policy_safe_motion_prompt(prompt)

    assert "萧家" not in safe
    assert "萧薰儿" not in safe
    assert "萧炎" not in safe
    assert "参考音频" not in safe
    assert "紫衣少女" in safe
    assert "黑衣青年" in safe
    assert "人物只做与剧情相符的短句说话动作" in safe


def test_resumed_policy_rejection_skips_consumed_reference_audio_attempts(
    tmp_path: Path,
) -> None:
    class Media:
        calls: list[tuple[str, object]] = []

        def create_video(self, prompt, image, output, duration, reference_audio=None):
            self.calls.append((prompt, reference_audio))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"real-prompt-only-video")
            return output

    class Assets:
        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            raise AssertionError("resumed attempt must reuse the prior keyframe")

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
        role="林晚",
        speaker_name="林晚",
        speaking=True,
        text="不要走。",
        emotion="紧张",
        source_quote="不要走。",
        location_asset_id="location_001",
        voice="heroine",
        visual_prompt="门外",
        motion_prompt="萧薰儿看向萧炎。参考音频中的可见对白严格依次为：萧薰儿：萧炎哥哥。。只有这些可见对白驱动对应角色口型；",
        keyframe_prompt="门外的对话",
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
        audio_seconds=2.0,
    )
    runtime = EpisodeProductionRuntime(
        Settings(provider="mock", admission_mode="production", max_unit_attempts=4),
        Media(),
        None,
        Assets(reference),
        None,
    )  # type: ignore[arg-type]
    identity = runtime._visual_identity(unit, audio, reference)
    for attempt in (1, 2):
        consumed = (
            episode_dir / "work/visual_attempts" / unit.unit_id / identity[:8]
            / f"attempt_{attempt:02d}" / "keyframe.jpeg"
        )
        consumed.parent.mkdir(parents=True, exist_ok=True)
        consumed.write_bytes(b"locked-keyframe")

    row = runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert len(Media.calls) == 1
    assert Media.calls[0][1] is None
    assert "萧炎" not in Media.calls[0][0]
    assert row["attempt"] == 3
    assert row["visual_source"] == "sd2.5-policy-safe-prompt-dialogue-final-local-audio"


def test_modified_visual_cache_guard_is_wired_to_visual_generation(tmp_path: Path) -> None:
    class Media:
        calls = 0

        def create_video(self, prompt, image, output, duration, reference_audio=None):
            self.calls += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"new-direct-reference-audio-video")
            return output

    class Assets:
        def __init__(self, reference: Path):
            self.reference = reference

        def reference_board(self, episode_dir, unit, series_assets, novel_dir):
            return self.reference

        def _ensure_image(self, prompt, output, reference=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"new-keyframe")
            return ImageResult(path=output)

    episode_dir = tmp_path / "episode"
    audio = episode_dir / "work/audio.wav"
    keyframe = episode_dir / "work/keyframe.jpeg"
    video = episode_dir / "work/clip.mp4"
    reference = episode_dir / "work/reference.jpeg"
    for path, content in (
        (audio, b"audio"),
        (keyframe, b"old-keyframe"),
        (video, b"old-postprocessed-video"),
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
        motion_prompt="轻微推镜",
        keyframe_prompt="门外的脚步声",
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
        audio_seconds=2.0,
    )
    settings = Settings(provider="mock")
    media = Media()
    assets = Assets(reference)
    runtime = EpisodeProductionRuntime(settings, media, None, assets, None)  # type: ignore[arg-type]
    identity = runtime._visual_identity(unit, audio, reference)
    video.with_suffix(".mp4.request.json").write_text(
        json.dumps({
            "request_sha256": identity,
            "workflow": "sd2.5-reference-audio",
            "postprocess": "legacy-lip-remediation",
        }),
        encoding="utf-8",
    )

    runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert media.calls == 1
    assert video.read_bytes() == b"new-direct-reference-audio-video"


@pytest.mark.parametrize("has_metadata", [True, False])
def test_sd25_rerun_can_reuse_hash_verified_locked_keyframe(
    tmp_path: Path,
    has_metadata: bool,
) -> None:
    class Media:
        calls = 0

        def create_video(self, prompt, image, output, duration, reference_audio=None):
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
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
        audio_seconds=2.0,
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

        def create_video(self, prompt, image, output, duration, reference_audio=None):
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
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
        audio_seconds=2.0,
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


def test_visual_retry_uses_local_motion_fallback_after_two_remote_failures(
    tmp_path: Path,
) -> None:
    class Media:
        calls = 0

        def create_video(self, prompt, image, output, duration, reference_audio=None):
            self.calls += 1
            assert reference_audio is None
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
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
        audio_seconds=2.0,
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
        def create_video(self, prompt, image, output, duration, reference_audio=None):
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
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/clip.mp4",
        segment_path="work/segment.mp4",
        audio_seconds=2.0,
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
            audio_path=f"work/{unit_id}.wav",
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
        audio_path="work/audio.wav",
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
