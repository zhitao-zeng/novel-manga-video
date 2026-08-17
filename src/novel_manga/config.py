from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _optional_float(name: str) -> float | None:
    value = os.getenv(name)
    return float(value) if value is not None and value.strip() else None


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"
    admission_mode: str = "preview"
    fps: int = 25
    width: int = 1080
    height: int = 1920
    intro_seconds: float = 4.0
    outro_seconds: float = 4.0
    output_root: Path = Path("outputs")
    font_path: Path = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")
    bgm_path: Path | None = None
    phanrouter_base_url: str = "https://cloud.phanthy.com/phanrouter"
    phanrouter_api_key: str | None = None
    phanrouter_image_api_key: str | None = None
    image_model: str = "gpt-image-2"
    video_model: str = "sd2.5"
    video_requires_audio: bool = False
    video_max_seconds: float = 14.0
    image_command: str | None = None
    local_image_prompt_policy: str | None = None
    local_visual_strategy: str = "keyframe"
    video_command: str | None = None
    model_lifecycle_command: str | None = None
    inline_reference_images: bool = False
    reuse_existing_assets: bool = False
    reuse_existing_keyframes: bool = False
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gpt-5-mini"
    llm_max_tokens: int = 8192
    llm_disable_thinking: bool = False
    planner_backend: str = "auto"
    planner_command: str | None = None
    planner_max_revisions: int = 2
    tts_base_url: str | None = None
    tts_api_key: str | None = None
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    tts_command: str | None = None
    tts_speed: float | None = None
    tts_narration_speed: float | None = None
    tts_dialogue_speed: float | None = None
    voice_map: dict[str, str] = field(default_factory=dict)
    local_tts_python: Path | None = None
    local_tts_model_dir: Path | None = None
    local_tts_model_file: str = "model.onnx"
    local_tts_sid: int = 0
    media_workers: int = 2
    video_workers: int = 2
    max_unit_attempts: int = 2
    align_command: str | None = None
    asr_command: str | None = None
    max_asr_cer: float = 0.12
    max_turn_cer: float = 0.35
    max_subtitle_cps: float = 15.0
    request_timeout: float = 180.0
    poll_timeout: float = 900.0

    @classmethod
    def from_env(
        cls,
        provider: str = "mock",
        output_root: str | Path = "outputs",
        bgm_path: str | Path | None = None,
        admission_mode: str | None = None,
    ) -> "Settings":
        legacy_lip_sync = [
            name
            for name in (
                "NOVEL_LIP_SYNC_COMMAND",
                "NOVEL_LIP_SYNC_REMEDIATION_COMMAND",
                "NOVEL_LIP_SYNC_BACKEND_TYPE",
            )
            if os.getenv(name)
        ]
        if legacy_lip_sync:
            raise ValueError(
                "lip-sync inspection/remediation is disabled; remove legacy settings: "
                + ", ".join(legacy_lip_sync)
            )
        voice_map_raw = os.getenv("NOVEL_VOICE_MAP_JSON", "{}")
        try:
            voice_map = json.loads(voice_map_raw)
        except json.JSONDecodeError as error:
            raise ValueError("NOVEL_VOICE_MAP_JSON must be a JSON object") from error
        if not isinstance(voice_map, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in voice_map.items()
        ):
            raise ValueError("NOVEL_VOICE_MAP_JSON must map role names to voice names")
        settings = cls(
            provider=provider,
            admission_mode=admission_mode or os.getenv(
                "NOVEL_ADMISSION_MODE", "preview" if provider == "mock" else "production"
            ),
            output_root=Path(output_root),
            font_path=Path(os.getenv("NOVEL_FONT_PATH", str(cls.font_path))),
            bgm_path=Path(bgm_path) if bgm_path else None,
            phanrouter_base_url=os.getenv("PHANROUTER_BASE_URL", cls.phanrouter_base_url),
            phanrouter_api_key=os.getenv("PHANROUTER_API_KEY"),
            phanrouter_image_api_key=os.getenv("PHANROUTER_IMAGE_API_KEY"),
            image_model=os.getenv(
                "NOVEL_IMAGE_MODEL", os.getenv("PHANROUTER_IMAGE_MODEL", cls.image_model)
            ),
            video_model=os.getenv(
                "NOVEL_VIDEO_MODEL", os.getenv("PHANROUTER_VIDEO_MODEL", cls.video_model)
            ),
            video_requires_audio=os.getenv(
                "NOVEL_VIDEO_REQUIRES_AUDIO", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            video_max_seconds=float(os.getenv("NOVEL_VIDEO_MAX_SECONDS", "14")),
            image_command=os.getenv("NOVEL_IMAGE_COMMAND"),
            local_image_prompt_policy=(
                os.getenv("NOVEL_LOCAL_IMAGE_PROMPT_POLICY", "native-v5")
                if provider == "command"
                else None
            ),
            local_visual_strategy=(
                os.getenv("NOVEL_LOCAL_VISUAL_STRATEGY", "keyframe")
                if provider == "command"
                else "keyframe"
            ),
            video_command=os.getenv("NOVEL_VIDEO_COMMAND"),
            model_lifecycle_command=os.getenv("NOVEL_MODEL_LIFECYCLE_COMMAND"),
            inline_reference_images=os.getenv(
                "PHANROUTER_INLINE_REFERENCE_IMAGES", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            reuse_existing_assets=os.getenv(
                "NOVEL_REUSE_EXISTING_ASSETS", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            reuse_existing_keyframes=os.getenv(
                "NOVEL_REUSE_EXISTING_KEYFRAMES", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            llm_base_url=os.getenv("NOVEL_LLM_BASE_URL"),
            llm_api_key=os.getenv("NOVEL_LLM_API_KEY"),
            llm_model=os.getenv("NOVEL_LLM_MODEL", cls.llm_model),
            llm_max_tokens=int(os.getenv("NOVEL_LLM_MAX_TOKENS", "8192")),
            llm_disable_thinking=os.getenv("NOVEL_LLM_DISABLE_THINKING", "0").strip().lower()
            in {"1", "true", "yes", "on"},
            planner_backend=os.getenv("NOVEL_PLANNER_BACKEND", "auto"),
            planner_command=os.getenv("NOVEL_PLANNER_COMMAND"),
            planner_max_revisions=int(os.getenv("NOVEL_PLANNER_MAX_REVISIONS", "2")),
            tts_base_url=os.getenv("NOVEL_TTS_BASE_URL"),
            tts_api_key=os.getenv("NOVEL_TTS_API_KEY"),
            tts_model=os.getenv("NOVEL_TTS_MODEL", cls.tts_model),
            tts_voice=os.getenv("NOVEL_TTS_VOICE", cls.tts_voice),
            tts_command=os.getenv("NOVEL_TTS_COMMAND"),
            tts_speed=_optional_float("NOVEL_TTS_SPEED"),
            tts_narration_speed=_optional_float("NOVEL_TTS_NARRATION_SPEED"),
            tts_dialogue_speed=_optional_float("NOVEL_TTS_DIALOGUE_SPEED"),
            voice_map=voice_map,
            local_tts_python=Path(os.environ["NOVEL_LOCAL_TTS_PYTHON"]) if os.getenv("NOVEL_LOCAL_TTS_PYTHON") else None,
            local_tts_model_dir=Path(os.environ["NOVEL_LOCAL_TTS_MODEL_DIR"]) if os.getenv("NOVEL_LOCAL_TTS_MODEL_DIR") else None,
            local_tts_model_file=os.getenv("NOVEL_LOCAL_TTS_MODEL_FILE", "model.onnx"),
            local_tts_sid=int(os.getenv("NOVEL_LOCAL_TTS_SID", "0")),
            media_workers=int(os.getenv("NOVEL_MEDIA_WORKERS", "2")),
            video_workers=int(os.getenv("NOVEL_VIDEO_WORKERS", "2")),
            max_unit_attempts=int(os.getenv("NOVEL_MAX_UNIT_ATTEMPTS", "2")),
            align_command=os.getenv("NOVEL_ALIGN_COMMAND"),
            asr_command=os.getenv("NOVEL_ASR_COMMAND"),
            max_asr_cer=float(os.getenv("NOVEL_MAX_ASR_CER", "0.12")),
            max_turn_cer=float(os.getenv("NOVEL_MAX_TURN_CER", "0.35")),
            max_subtitle_cps=float(os.getenv("NOVEL_MAX_SUBTITLE_CPS", "15")),
            request_timeout=float(os.getenv("NOVEL_REQUEST_TIMEOUT", "180")),
            poll_timeout=float(os.getenv("NOVEL_POLL_TIMEOUT", "900")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.fps not in (25, 30):
            raise ValueError("fps must be 25 or 30")
        if not self.font_path.is_file():
            raise ValueError(f"subtitle/card font not found: {self.font_path}")
        if self.admission_mode not in {"preview", "production"}:
            raise ValueError("admission_mode must be preview or production")
        if self.planner_backend not in {"auto", "deterministic", "openai-compatible", "command"}:
            raise ValueError("NOVEL_PLANNER_BACKEND is invalid")
        if not 0 <= self.planner_max_revisions <= 2:
            raise ValueError("NOVEL_PLANNER_MAX_REVISIONS must be between 0 and 2")
        if not 512 <= self.llm_max_tokens <= 32768:
            raise ValueError("NOVEL_LLM_MAX_TOKENS must be between 512 and 32768")
        if not 1 <= self.media_workers <= 8:
            raise ValueError("NOVEL_MEDIA_WORKERS must be between 1 and 8")
        if not 1 <= self.video_workers <= 8:
            raise ValueError("NOVEL_VIDEO_WORKERS must be between 1 and 8")
        if not 4.0 <= self.video_max_seconds <= 14.0:
            raise ValueError("NOVEL_VIDEO_MAX_SECONDS must be between 4 and 14")
        if not 1 <= self.max_unit_attempts <= 4:
            raise ValueError("NOVEL_MAX_UNIT_ATTEMPTS must be between 1 and 4")
        for name, value in (
            ("NOVEL_TTS_SPEED", self.tts_speed),
            ("NOVEL_TTS_NARRATION_SPEED", self.tts_narration_speed),
            ("NOVEL_TTS_DIALOGUE_SPEED", self.tts_dialogue_speed),
        ):
            if value is not None and not 0.5 <= value <= 2.0:
                raise ValueError(f"{name} must be between 0.5 and 2.0")
        if self.request_timeout <= 0 or self.poll_timeout <= 0:
            raise ValueError("request and poll timeouts must be positive")
        if self.local_image_prompt_policy not in {
            None,
            "legacy",
            "native-v1",
            "native-v2",
            "native-v3",
            "native-v4",
            "native-v5",
        }:
            raise ValueError(
                "NOVEL_LOCAL_IMAGE_PROMPT_POLICY must be legacy, native-v1, native-v2, native-v3, native-v4, or native-v5"
            )
        if self.local_visual_strategy not in {
            "keyframe",
            "h3-direct-single-character",
        }:
            raise ValueError(
                "NOVEL_LOCAL_VISUAL_STRATEGY must be keyframe or h3-direct-single-character"
            )
        video_backend_identity = "".join(
            character
            for character in f"{self.video_model} {self.video_command or ''}".casefold()
            if character.isalnum()
        )
        if "latentsync" in video_backend_identity:
            raise ValueError(
                "LatentSync is disabled; use a direct reference-audio video backend"
            )
        if self.local_visual_strategy == "h3-direct-single-character" and (
            self.provider != "command"
            or not any(token in video_backend_identity for token in ("minimaxh3", "h3ref2va"))
        ):
            raise ValueError(
                "h3-direct-single-character requires the command provider and MiniMax H3"
            )
        if self.planner_backend == "command" and not self.planner_command:
            raise ValueError("NOVEL_PLANNER_COMMAND is required for command planner")
        if self.planner_backend == "openai-compatible" and not (
            self.llm_base_url and self.llm_api_key
        ):
            raise ValueError(
                "openai-compatible planner requires NOVEL_LLM_BASE_URL and NOVEL_LLM_API_KEY"
            )
        if self.provider == "phanrouter":
            if self.video_model != "sd2.5":
                raise ValueError("PhanRouter video model must be sd2.5")
            missing = []
            for name, value in (("PHANROUTER_API_KEY", self.phanrouter_api_key),):
                if not value:
                    missing.append(name)
            if self.planner_backend != "command" and bool(self.llm_base_url) != bool(self.llm_api_key):
                missing.append("NOVEL_LLM_BASE_URL/NOVEL_LLM_API_KEY pair")
            remote_tts = bool(self.tts_base_url and self.tts_api_key)
            local_tts = bool(self.local_tts_python and self.local_tts_model_dir)
            command_tts = bool(self.tts_command)
            if not remote_tts and not local_tts and not command_tts:
                missing.append("remote TTS pair, local TTS model, or NOVEL_TTS_COMMAND")
            if missing:
                raise ValueError("production provider missing environment variables: " + ", ".join(missing))
            if local_tts:
                assert self.local_tts_python is not None and self.local_tts_model_dir is not None
                if not self.local_tts_python.is_file():
                    raise ValueError(f"local TTS Python not found: {self.local_tts_python}")
                for filename in (self.local_tts_model_file, "tokens.txt", "lexicon.txt", "phone.fst", "date.fst", "number.fst"):
                    if not (self.local_tts_model_dir / filename).is_file():
                        raise ValueError(f"local TTS model file missing: {self.local_tts_model_dir / filename}")
        if self.provider == "command":
            missing_commands = [
                name
                for name, value in (
                    ("NOVEL_IMAGE_COMMAND", self.image_command),
                    ("NOVEL_VIDEO_COMMAND", self.video_command),
                    ("NOVEL_TTS_COMMAND", self.tts_command),
                )
                if not value
            ]
            if missing_commands:
                raise ValueError(
                    "command media provider missing environment variables: "
                    + ", ".join(missing_commands)
                )
        if self.admission_mode == "production":
            missing_quality = [
                name
                for name, value in (
                    ("NOVEL_ASR_COMMAND", self.asr_command),
                )
                if not value
            ]
            if missing_quality:
                raise ValueError(
                    "production admission requires executable evidence backends: "
                    + ", ".join(missing_quality)
                )
