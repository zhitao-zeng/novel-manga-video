from pathlib import Path

import pytest

from runtime.minimax_h3_client import (
    MiniMaxH3Client,
    MiniMaxH3Config,
    H3_PROMPT_COMPILER_REVISION,
    aligned_frame_count,
    build_drama_prompt,
    stable_generation_seed,
)


def test_h3_frame_count_follows_17k_plus_5_grid() -> None:
    assert aligned_frame_count(8.0) == 192
    assert aligned_frame_count(5.0) == 124


def test_h3_graph_locks_picture_and_audio_references() -> None:
    client = MiniMaxH3Client(MiniMaxH3Config())
    graph = client.build_graph(
        image_name="shot.jpeg",
        audio_name="driver.wav",
        duration_seconds=8.0,
        prompt="女孩转头，说：别开门。",
        output_prefix="test/h3",
        seed=7,
    )

    conditioning = graph["9"]["inputs"]
    assert conditioning["ref_images.ref_image_0"] == ["7", 0]
    assert conditioning["ref_audios.ref_audio_0"] == ["8", 0]
    assert conditioning["length"] == 192
    assert graph["10"]["class_type"] == "VRGDG_MiniMaxH3AudioDrive"
    assert graph["18"]["inputs"]["audio"] == ["10", 1]
    assert "女孩转头" in conditioning["prompt"]
    assert graph["13"]["inputs"]["sampler_name"] == "res_multistep"


def test_h3_turbo_sampling_bundle_is_audited_and_wired() -> None:
    config = MiniMaxH3Config(
        model="minimax_h3_ref2va_pruned_turbo_int8_convrot.safetensors",
        model_revision="6395b6922e1a82694401e752b731aedf85ff8ac9",
        steps=8,
        sampler="euler",
        scheduler="simple",
    )
    client = MiniMaxH3Client(config)
    graph = client.build_graph(
        image_name="shot.jpeg",
        audio_name="driver.wav",
        duration_seconds=4.0,
        prompt="人物抬眼。",
        output_prefix="test/h3-turbo",
        seed=7,
    )

    assert graph["1"]["inputs"]["unet_name"] == config.model
    assert graph["13"]["inputs"]["sampler_name"] == "euler"
    assert graph["14"]["inputs"]["steps"] == 8
    assert graph["14"]["inputs"]["scheduler"] == "simple"
    assert config.audit_identity() == {
        "backend": "MiniMax-H3-Ref2VA",
        "model": config.model,
        "model_revision": config.model_revision,
        "steps": 8,
        "sampler": "euler",
        "scheduler": "simple",
        "sigma_shift_video": 12.0,
        "sigma_shift_audio": 3.0,
    }


def test_h3_graph_accepts_separate_character_and_environment_assets() -> None:
    client = MiniMaxH3Client(MiniMaxH3Config())
    graph = client.build_graph(
        image_name="character.jpeg",
        additional_image_names=("environment.jpeg",),
        audio_name="driver.wav",
        duration_seconds=4.0,
        prompt="把图1角色放进图2场景并完成对白。",
        output_prefix="test/h3-two-assets",
        seed=9,
    )

    conditioning = graph["9"]["inputs"]
    assert conditioning["ref_images.ref_image_0"] == ["7", 0]
    assert conditioning["ref_images.ref_image_1"] == ["20", 0]
    assert graph["20"]["inputs"]["image"] == "environment.jpeg"
    assert "<Picture 1> is the character identity asset" in conditioning["prompt"]
    assert "<Picture 2> is the empty environment asset" in conditioning["prompt"]
    assert "Place the character from <Picture 1> naturally inside" in conditioning["prompt"]
    assert "At frame 0, the character from <Picture 1> is already fully composited" in conditioning["prompt"]
    assert "never begin with an empty environment" in conditioning["prompt"]


def test_h3_prompt_treats_silence_as_non_speaking() -> None:
    prompt = build_drama_prompt("门外有脚步声，女孩屏住呼吸。")

    assert "Silent spans are narration" in prompt
    assert "keeps a naturally closed mouth" in prompt
    assert "action-reaction-action" in prompt
    assert "<Picture 1> is already the exact composed starting frame" in prompt
    assert "inside <Picture 2>" not in prompt


def test_h3_locked_camera_plan_is_not_overridden_by_generic_motion_wrapper() -> None:
    prompt = build_drama_prompt(
        "【本连续镜头摄影机计划】模式=locked；轨迹=锁定机位，摄影机全程保持完全静止。"
    )

    assert "physical camera completely stationary" in prompt
    assert "no pan, tilt, dolly, truck, orbit, crane" in prompt
    assert "Move through the same 3D environment" not in prompt


def test_h3_moving_camera_plan_keeps_one_explicit_trajectory() -> None:
    prompt = build_drama_prompt(
        "【本连续镜头摄影机计划】模式=motivated_subtle；轨迹=向右横移半步。"
    )

    assert "Follow only the physical camera trajectory explicitly specified" in prompt
    assert "never add a second move" in prompt


def test_h3_generation_seed_is_reproducible_and_input_bound(tmp_path: Path) -> None:
    character = tmp_path / "character.jpeg"
    location = tmp_path / "location.jpeg"
    audio = tmp_path / "driver.wav"
    character.write_bytes(b"character")
    location.write_bytes(b"location")
    audio.write_bytes(b"audio")

    kwargs = {
        "prompt": "锁定机位，人物抬眼。",
        "image_paths": (character, location),
        "audio_path": audio,
        "duration_seconds": 4.0,
    }
    first = stable_generation_seed(**kwargs)

    assert first == stable_generation_seed(**kwargs)
    assert first != stable_generation_seed(**{**kwargs, "prompt": "人物转身。"})
    assert 0 <= first < 2**63
    assert H3_PROMPT_COMPILER_REVISION == "h3-drama-v2-camera-contract"


def test_h3_rejects_missing_reference_audio_at_cli_contract(tmp_path: Path) -> None:
    config = MiniMaxH3Config(width=481)
    with pytest.raises(ValueError, match="divisible by 32"):
        MiniMaxH3Client(config)
