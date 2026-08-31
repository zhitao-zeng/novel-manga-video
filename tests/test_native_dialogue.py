from pathlib import Path
from types import SimpleNamespace

import pytest

import novel_manga.production_runtime as runtime_module
import novel_manga.render as render_module
from novel_manga.config import NATIVE_DIALOGUE_POLICY, Settings
from novel_manga.models import (
    EpisodePlan,
    SceneAudioPlan,
    ScriptTurn,
    Shot,
    SpeechStrategy,
    TurnDelivery,
)
from novel_manga.production_models import RuntimeUnit, RuntimeVisualGroup
from novel_manga.production_runtime import (
    EpisodeProductionRuntime,
    apply_native_dialogue_profile,
    compile_seedance_native_audio_prompt,
    native_dialogue_quality_route,
)
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.render import Renderer
from novel_manga.runtime_backends import correct_protected_lexicon


def _unit(
    *,
    text: str = "楚烟儿，等等我。",
    delivery_mode: TurnDelivery = TurnDelivery.VISIBLE_DIALOGUE,
) -> RuntimeUnit:
    speaking = delivery_mode == TurnDelivery.VISIBLE_DIALOGUE
    silent_action = delivery_mode == TurnDelivery.SILENT_ACTION
    return RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="楚焱" if speaking else "action" if silent_action else "narrator",
        speaker_name="楚焱" if speaking else "动作" if silent_action else "旁白",
        speaking=speaking,
        delivery_mode=delivery_mode,
        text=text,
        emotion="克制",
        source_quote=text,
        character_asset_ids=["character_001"] if speaking else [],
        location_asset_id="location_001",
        voice="voice-1",
        visual_prompt="楚家广场",
        motion_instruction="楚焱转头",
        motion_prompt="楚焱转头",
        keyframe_prompt="楚家广场中的楚焱",
        audio_plan=SceneAudioPlan(
            speech_strategy=SpeechStrategy.NATIVE,
            voice_reference_id="voice_anchor_chuyan_v1" if speaking else "",
        ),
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
        planned_seconds=6.0,
    )


def _group(unit: RuntimeUnit) -> RuntimeVisualGroup:
    return RuntimeVisualGroup(
        group_id="visual_001",
        scene_id=unit.scene_id,
        shot_ids=[unit.shot_id],
        unit_ids=[unit.unit_id],
        location_asset_id=unit.location_asset_id,
        character_asset_ids=unit.character_asset_ids,
        spatial_anchor="广场行动轴",
        combined_text=unit.text,
        keyframe_prompt=unit.keyframe_prompt,
        motion_prompt=unit.motion_prompt,
        keyframe_path="work/group.jpeg",
        raw_video_path="work/group.mp4",
        segment_path="work/group-segment.mp4",
        planned_seconds=6.0,
    )


def _plan(turn: ScriptTurn) -> EpisodePlan:
    return EpisodePlan(
        video_title="第一集",
        hook="开门",
        summary="测试原生对白",
        shots=[
            Shot(
                index=1,
                narration=turn.text,
                subtitle=turn.text,
                visual_prompt="楚家广场",
                motion_prompt="楚焱转头",
                source_quote=turn.source_quote,
                turns=[turn],
            )
        ],
    )


def test_native_dialogue_profile_blocks_voiceover_and_normalizes_audio_strategy() -> None:
    visible = ScriptTurn(
        role="楚焱",
        speaker_name="楚焱",
        text="等等我。",
        speaking=True,
        delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
        source_quote="等等我。",
    )
    normalized = apply_native_dialogue_profile(_plan(visible))

    assert normalized.shots[0].audio_plan.speech_strategy == SpeechStrategy.NATIVE

    narrator = ScriptTurn(
        text="一刻钟前。",
        delivery_mode=TurnDelivery.NARRATION,
        source_quote="一刻钟前。",
    )
    with pytest.raises(ValueError, match="narration is forbidden"):
        apply_native_dialogue_profile(_plan(narrator))

    inner = ScriptTurn(
        role="楚焱",
        speaker_name="楚焱",
        text="我该怎么办？",
        speaking=False,
        delivery_mode=TurnDelivery.INNER_VOICE,
        source_quote="我该怎么办？",
    )
    with pytest.raises(ValueError, match="inner_voice is forbidden"):
        apply_native_dialogue_profile(_plan(inner))

    title = ScriptTurn(
        text="一刻钟前",
        delivery_mode=TurnDelivery.TITLE_CARD,
        source_quote="一刻钟前",
    )
    assert apply_native_dialogue_profile(_plan(title)).shots[0].turns[0].delivery_mode == TurnDelivery.TITLE_CARD


def test_native_turn_defaults_follow_delivery_instead_of_narrator_role() -> None:
    visible = ScriptTurn.model_validate(
        {
            "speaker_name": "测验员",
            "text": "三段，低级。",
            "delivery_mode": "visible_dialogue",
            "source_quote": "三段，低级。",
        }
    )
    silent = ScriptTurn.model_validate(
        {
            "text": "楚焱握紧拳头。",
            "delivery_mode": "silent_action",
            "source_quote": "楚焱握紧拳头。",
        }
    )

    assert visible.role == "测验员"
    assert visible.speaking is True
    assert silent.role == "action"
    assert silent.speaker_name == ""
    assert silent.speaking is False


def test_native_prompt_uses_voice_anchor() -> None:
    unit = _unit()
    prompt = compile_seedance_native_audio_prompt(unit)
    proxy = EpisodeProductionRuntime._visual_group_proxy(
        _group(unit),
        {unit.unit_id: unit},
    )

    assert "voice_anchor_chuyan_v1" in prompt
    assert "Seedance自行生成" in prompt
    assert "外部音频是唯一" not in prompt
    assert proxy.audio_plan.speech_strategy == SpeechStrategy.NATIVE


def test_silent_action_has_environment_audio_but_no_voice_or_asr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    unit = _unit(
        text="楚焱握紧拳头后松开。",
        delivery_mode=TurnDelivery.SILENT_ACTION,
    )
    prompt = compile_seedance_native_audio_prompt(unit)
    assert "环境声" in prompt
    assert "无人说话、无人旁白" in prompt
    assert "准确表达" not in prompt

    class Evidence:
        @staticmethod
        def transcribe(*args, **kwargs):
            raise AssertionError("silent_action must not invoke ASR")

    runtime = EpisodeProductionRuntime(
        Settings(final_audio_policy=NATIVE_DIALOGUE_POLICY),
        None,
        None,
        None,
        Evidence(),
    )  # type: ignore[arg-type]
    group = _group(unit)
    audio = tmp_path / "silent-native.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(runtime_module, "media_duration", lambda path: 5.0)
    row, timeline = runtime._native_group_asr(
        group=group,
        audio=audio,
        units_by_id={unit.unit_id: unit},
        protected_terms=[],
    )
    assert row["status"] == "not_applicable_silent_action"
    assert timeline[0]["events"] == []


def test_native_dialogue_production_configuration_does_not_require_tts() -> None:
    settings = Settings(
        provider="command",
        admission_mode="production",
        final_audio_policy=NATIVE_DIALOGUE_POLICY,
        image_command="/models/image",
        video_command="/models/video",
        asr_command="/models/asr",
        planner_backend="command",
        planner_command="/models/deepseek-planner",
    )

    settings.validate()


def test_native_dialogue_provider_requests_native_audio_without_audio_url() -> None:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(
        video_model="sd2.5",
        final_audio_policy=NATIVE_DIALOGUE_POLICY,
    )

    payload = provider._video_payload(
        "楚焱直接说出台词",
        "https://example.test/frame.png",
        6.0,
    )

    assert payload["generate_audio"] is True
    assert all(item.get("type") != "audio_url" for item in payload["content"])


def test_protected_lexicon_corrects_only_expected_named_terms() -> None:
    corrected, rows = correct_protected_lexicon(
        "楚燕儿追上楚炎。",
        "楚烟儿追上楚焱。",
        ["楚烟儿", "楚焱", "楚家"],
    )

    assert corrected == "楚烟儿追上楚焱。"
    assert {row["canonical"] for row in rows} == {"楚烟儿", "楚焱"}


def test_native_quality_route_uses_only_three_hard_checks_and_two_bounded_retries() -> None:
    bad = {
        "hypothesis": "乱语",
        "cer": 0.8,
        "max_volume_db": -40.0,
        "speaker_count": 2,
    }

    first = native_dialogue_quality_route(bad, quality_attempt=0)
    second = native_dialogue_quality_route(bad, quality_attempt=1)
    terminal = native_dialogue_quality_route(bad, quality_attempt=2)
    acceptable = native_dialogue_quality_route(
        {
            "hypothesis": "楚焱哥哥",
            "cer": 0.49,
            "max_volume_db": -8.0,
            "speaker_count": 1,
        },
        quality_attempt=0,
    )

    assert set(first["issues"]) == {
        "voice_energy_missing",
        "gibberish_cer_over_0_5",
        "multiple_speakers_in_single_speaker_contract",
    }
    assert first["action"] == "regenerate_same_contract_once"
    assert second["action"] == "regenerate_reaction_or_over_shoulder"
    assert terminal["action"] == "fail_native_dialogue_gate"
    assert acceptable["action"] == "accept"
    assert acceptable["legacy_cer_thresholds_report_only"] is True


def test_native_group_subtitles_are_asr_text_with_protected_name_fix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Evidence:
        @staticmethod
        def transcribe(unit_id: str, reference: str, audio: Path) -> dict:
            return {
                "unit_id": unit_id,
                "reference": reference,
                "hypothesis": "楚燕儿等我。",
                "cer": 0.4,
                "errors": 2,
                "reference_chars": 7,
                "status": "passed",
                "backend": "fixture-asr",
            }

    runtime = EpisodeProductionRuntime(
        Settings(final_audio_policy=NATIVE_DIALOGUE_POLICY),
        None,
        None,
        None,
        Evidence(),
    )  # type: ignore[arg-type]
    unit = _unit()
    group = _group(unit)
    audio = tmp_path / "native.wav"
    audio.write_bytes(b"native")
    monkeypatch.setattr(runtime_module, "media_duration", lambda path: 6.0)
    monkeypatch.setattr(runtime_module, "measured_speech_bounds", lambda path: (0.4, 5.4))
    monkeypatch.setattr(runtime, "_audio_levels", lambda path: (-20.0, -3.0))

    row, timeline = runtime._native_group_asr(
        group=group,
        audio=audio,
        units_by_id={unit.unit_id: unit},
        protected_terms=["楚烟儿", "楚焱"],
    )

    subtitle_text = "".join(event["text"].replace(r"\N", "") for event in timeline[0]["events"])
    assert subtitle_text == "楚烟儿等我。"
    assert subtitle_text != unit.text
    assert all(event["subtitle_source"] == "native_audio_asr" for event in timeline[0]["events"])
    assert row["raw_hypothesis"] == "楚燕儿等我。"
    assert row["hypothesis"] == "楚烟儿等我。"


def test_native_mux_keeps_visual_duration_instead_of_audio_duration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    renderer = Renderer(Settings(final_audio_policy=NATIVE_DIALOGUE_POLICY))
    visual = tmp_path / "visual.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "segment.mp4"
    visual.write_bytes(b"video")
    audio.write_bytes(b"audio")
    commands: list[list[str]] = []

    def fake_duration(path: Path) -> float:
        return 8.0 if path == visual or path == output else 3.0

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"segment")

    monkeypatch.setattr(render_module, "media_duration", fake_duration)
    monkeypatch.setattr(render_module, "run", fake_run)

    _, duration = renderer.mux_visual_group(
        visual,
        audio,
        output,
    )

    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "atrim=duration=8.000" in graph
    assert commands[0][commands[0].index("-t") + 1] == "8.000"
    assert duration == 8.0


def test_native_quality_retry_preserves_original(
    tmp_path: Path,
) -> None:
    calls = []

    class Media:
        @staticmethod
        def create_video(prompt, image, output, duration):
            calls.append(
                {
                    "prompt": prompt,
                    "image": image.path,
                    "duration": duration,
                }
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"retry")

    runtime = EpisodeProductionRuntime(
        Settings(final_audio_policy=NATIVE_DIALOGUE_POLICY),
        Media(),
        None,
        None,
        None,
    )  # type: ignore[arg-type]
    unit = _unit()
    group = _group(unit)
    episode_dir = tmp_path / "episode"
    raw = episode_dir / group.raw_video_path
    keyframe = episode_dir / group.keyframe_path
    raw.parent.mkdir(parents=True, exist_ok=True)
    keyframe.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"original")
    keyframe.write_bytes(b"frame")

    runtime._regenerate_native_group_video(
        episode_dir=episode_dir,
        group=group,
        units_by_id={unit.unit_id: unit},
        quality_attempt=2,
        fallback_composition=True,
    )

    preserved = (
        episode_dir
        / "work/native_dialogue_quality_retries/visual_001/attempt_01_original.mp4"
    )
    assert preserved.read_bytes() == b"original"
    assert raw.read_bytes() == b"retry"
    assert "肩后反打" in calls[0]["prompt"]


def test_native_assembly_crossfades_and_mixes_bgm_before_final_loudnorm(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"music")
    renderer = Renderer(
        Settings(
            final_audio_policy=NATIVE_DIALOGUE_POLICY,
            intro_seconds=0,
            outro_seconds=0,
            bgm_path=bgm,
        )
    )
    segments = [tmp_path / "one.mp4", tmp_path / "two.mp4"]
    for segment in segments:
        segment.write_bytes(b"segment")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"generated")

    monkeypatch.setattr(render_module, "run", fake_run)
    work = tmp_path / "work"
    work.mkdir()
    _, _, _, events = renderer.assemble_production(
        tmp_path / "intro.jpeg",
        tmp_path / "ending.jpeg",
        [
            {
                "unit_id": "u1",
                "role": "楚焱",
                "segment": str(segments[0]),
                "duration": 4.0,
                "subtitle_events": [
                    {
                        "unit_id": "u1",
                        "role": "楚焱",
                        "start": 0.2,
                        "end": 3.5,
                        "text": "第一句。",
                        "subtitle_source": "native_audio_asr",
                    }
                ],
            },
            {
                "unit_id": "u2",
                "role": "楚焱",
                "segment": str(segments[1]),
                "duration": 4.0,
                "subtitle_events": [
                    {
                        "unit_id": "u2",
                        "role": "楚焱",
                        "start": 0.2,
                        "end": 3.5,
                        "text": "第二句。",
                        "subtitle_source": "native_audio_asr",
                    }
                ],
            },
        ],
        tmp_path / "final.mp4",
        work,
    )

    join_graph = commands[0][commands[0].index("-filter_complex") + 1]
    final_graph = commands[1][commands[1].index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.150" in join_graph
    assert "acrossfade=d=0.150" in join_graph
    assert "amix=inputs=2:duration=first:dropout_transition=2:normalize=0" in final_graph
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in final_graph
    assert events[1]["start"] == pytest.approx(4.05)
    assert all(event["subtitle_source"] == "native_audio_asr" for event in events)
