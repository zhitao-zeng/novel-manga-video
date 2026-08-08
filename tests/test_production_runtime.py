import json
from pathlib import Path

import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.models import Character, Episode, EpisodePlan, ScriptTurn, Shot, StoryBible
from novel_manga.production import SeriesAssetFactory, compile_production_plan
from novel_manga.production_models import AssetRecord, RuntimeUnit, SeriesAssetManifest
from novel_manga.production_runtime import (
    EpisodeProductionRuntime,
    is_direct_reference_audio_visual_cache,
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


def test_production_settings_fail_closed_without_asr_backend() -> None:
    with pytest.raises(ValueError, match="NOVEL_ASR_COMMAND"):
        Settings(provider="mock", admission_mode="production").validate()


def test_latentsync_is_hard_disabled() -> None:
    with pytest.raises(ValueError, match="LatentSync is disabled"):
        Settings(video_model="LatentSync-1.6").validate()


def test_legacy_lip_sync_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_LIP_SYNC_COMMAND", "/models/old-adapter")
    with pytest.raises(ValueError, match="lip-sync inspection/remediation is disabled"):
        Settings.from_env()


def test_modified_or_latentsync_visual_cache_is_not_reused() -> None:
    assert is_direct_reference_audio_visual_cache({"workflow": "sd2.0-reference-audio"})
    assert not is_direct_reference_audio_visual_cache(
        {"workflow": "sd2.0-reference-audio", "postprocess": "closed-tail-crossfade"}
    )
    assert not is_direct_reference_audio_visual_cache({"backend": "latent_sync-local"})


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
            "workflow": "sd2.0-reference-audio",
            "postprocess": "legacy-lip-remediation",
        }),
        encoding="utf-8",
    )

    runtime._prepare_visual(episode_dir, tmp_path, unit, None)  # type: ignore[arg-type]

    assert media.calls == 1
    assert video.read_bytes() == b"new-direct-reference-audio-video"
