from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_manga.indextts import (
    INDEXTTS_SYNTHESIS_TEXT_POLICY,
    find_reference_audio,
    indextts_synthesis_identity,
    indextts_synthesis_text,
    speed_to_duration_factor,
)
from novel_manga.voxcpm_voice import (
    extract_performance_style,
    resolve_voice_profile,
    stable_voice_seed,
    styled_clone_text,
    voice_cache_name,
    voice_design_text,
)


ROOT = Path(__file__).resolve().parents[1]


def test_qwen_voice_aliases_map_to_stable_voxcpm_profiles() -> None:
    assert resolve_voice_profile("Uncle_Fu").key == "deep_male"
    assert resolve_voice_profile("Dylan").key == "young_male"
    assert resolve_voice_profile("Serena").key == "warm_female"
    assert resolve_voice_profile("unknown-voice").key == "deep_male"
    assert voice_cache_name("Uncle_Fu") == "deep_male.wav"
    assert stable_voice_seed("Uncle_Fu", "reference") == stable_voice_seed(
        "alloy", "reference"
    )


def test_style_compiler_does_not_repeat_locked_dialogue_as_instruction() -> None:
    instructions = (
        "标准普通话，语速自然偏快但清晰。逐字准确朗读：门外有人。"
        "人物和专有名词必须准确；角色语气：警惕而克制。保持角色音色稳定。"
    )
    assert extract_performance_style(instructions) == "警惕而克制"
    assert styled_clone_text("门外有人。", instructions) == "(警惕而克制)门外有人。"
    assert "逐字准确朗读" not in styled_clone_text("门外有人。", instructions)


def test_narration_style_and_voice_design_are_explicit() -> None:
    profile = resolve_voice_profile("onyx")
    designed = voice_design_text(profile)
    assert designed.startswith("(")
    assert "低沉浑厚" in designed
    assert designed.endswith(profile.seed_text)
    assert extract_performance_style("角色语气：沉静。只做画外旁白。") == (
        "沉静，画外旁白，叙事感自然"
    )
    assert extract_performance_style("表演意图：警惕而克制；语速：快。") == (
        "警惕而克制"
    )


def test_offline_manifest_selects_indextts_and_reference_mount() -> None:
    manifest = json.loads((ROOT / "runtime/model_manifest.json").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["models"]["tts"] == "/models/indextts-2.5"
    assert manifest["models"]["tts-references"] == "/models/indextts-references"
    assert manifest["optional_models"] == []


def test_offline_runtime_versions_match_the_pinned_delivery_contract() -> None:
    manifest = json.loads((ROOT / "runtime/model_manifest.json").read_text())
    sources = manifest["runtime_sources"]
    assert sources["comfyui"]["revision"] == (
        "6f7cd7fceaaf60d2669b554936394a7412c6fde5"
    )
    assert sources["diffusers"]["version"] == "0.38.0"
    assert sources["indextts"]["version"] == "2.5"
    assert sources["indextts"]["source_revision"] == (
        "ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c"
    )
    assert sources["indextts"]["model_revision"] == (
        "c39ce5ba981572cb187443877ff559dfb246ce63"
    )
    assert sources["indextts"]["auxiliary_revisions"]["amphion/MaskGCT"] == (
        "265c6cef07625665d0c28d2faafb1415562379dc"
    )
    assert sources["qwen_asr"]["version"] == "0.0.6"
    assert sources["qwen_image_tools"]["revision"] == (
        "6b5e1f5cec987d404be5ac6657db3b9aacb56a89"
    )


def test_offline_image_defaults_to_indextts() -> None:
    dockerfile = (ROOT / "Dockerfile.offline").read_text()
    assert "NOVEL_TTS_MODEL=IndexTTS-2.5" in dockerfile
    assert "NOVEL_TTS_BACKEND=indextts" in dockerfile
    assert "/opt/venvs/indextts" in dockerfile
    requirements = (ROOT / "docker/audio-requirements.txt").read_text().splitlines()
    assert not any(line.startswith("voxcpm==") for line in requirements)
    assert not any(line.startswith("qwen-tts==") for line in requirements)


def test_voxcpm2_remains_available_as_an_explicit_legacy_stage() -> None:
    worker = (ROOT / "runtime/model_worker.py").read_text()
    assert 'stage == "audio-voxcpm"' in worker
    assert "LegacyAudioService(models, load_tts=True)" in worker


def test_indextts_speed_is_model_native_and_inverts_pipeline_semantics() -> None:
    assert speed_to_duration_factor(1.0) == 1.0
    assert speed_to_duration_factor(1.25) == pytest.approx(0.8)
    assert speed_to_duration_factor(1.15) == pytest.approx(0.8695652174)
    with pytest.raises(ValueError, match="between 0.5 and 2.0"):
        speed_to_duration_factor(0.0)

    worker = (ROOT / "runtime/model_worker.py").read_text()
    indextts_service = worker.split("class IndexTTSService:", 1)[1].split(
        "class AudioEvidenceService:", 1
    )[0]
    assert "duration_factor=duration_factor" in indextts_service
    assert "atempo" not in indextts_service


def test_indextts_synthesis_text_normalizes_typographic_long_pause() -> None:
    assert indextts_synthesis_text("嘘——小声点。") == "嘘，小声点。"
    assert indextts_synthesis_text("不要——过来。") == "不要，过来。"
    assert indextts_synthesis_text("普通短句。") == "普通短句。"
    assert indextts_synthesis_identity("普通短句。") == {}
    assert indextts_synthesis_identity("嘘——小声点。") == {
        "tts_synthesis_text_policy": INDEXTTS_SYNTHESIS_TEXT_POLICY
    }
    assert INDEXTTS_SYNTHESIS_TEXT_POLICY == "indextts-punctuation-pause-v1"

    worker = (ROOT / "runtime/model_worker.py").read_text()
    assert "self.synthesis_text(locked_text)" in worker


def test_indextts_reference_uses_stable_voice_profile(tmp_path: Path) -> None:
    reference = tmp_path / "warm_female.wav"
    reference.write_bytes(b"RIFF" + b"0" * 64)
    assert find_reference_audio(tmp_path, "Serena") == reference
    with pytest.raises(FileNotFoundError, match="deep_male.wav"):
        find_reference_audio(tmp_path, "alloy")


def test_offline_image_uses_the_production_prompt_policy_and_version_labels() -> None:
    dockerfile = (ROOT / "Dockerfile.offline").read_text()
    assert dockerfile.rfind("NOVEL_LOCAL_IMAGE_PROMPT_POLICY=native-v5") > (
        dockerfile.rfind("NOVEL_LOCAL_IMAGE_PROMPT_POLICY=native-v4")
    )
    assert (
        'io.novel-manga.qwen-image-tools-revision="'
        '6b5e1f5cec987d404be5ac6657db3b9aacb56a89"'
    ) in dockerfile
