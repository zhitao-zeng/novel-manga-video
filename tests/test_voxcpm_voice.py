from __future__ import annotations

import json
from pathlib import Path

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


def test_offline_manifest_selects_voxcpm_and_keeps_qwen_optional() -> None:
    manifest = json.loads((ROOT / "runtime/model_manifest.json").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["models"]["tts"] == "/models/voxcpm2"
    assert manifest["models"]["tts-qwen"] == "/models/qwen3-tts-customvoice"
    assert "tts-qwen" in manifest["optional_models"]


def test_offline_image_defaults_to_voxcpm() -> None:
    dockerfile = (ROOT / "Dockerfile.offline").read_text()
    assert "NOVEL_TTS_MODEL=VoxCPM2" in dockerfile
    assert "NOVEL_TTS_BACKEND=voxcpm2" in dockerfile
    assert "NOVEL_TTS_QWEN_FALLBACK=1" in dockerfile
    requirements = (ROOT / "docker/audio-requirements.txt").read_text().splitlines()
    assert "voxcpm==2.0.3" in requirements
