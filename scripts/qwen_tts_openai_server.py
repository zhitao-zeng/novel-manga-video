#!/usr/bin/env python3
"""OpenAI-compatible speech endpoint backed by one persistent Qwen3-TTS model.

The novel pipeline already supports an OpenAI-compatible ``/audio/speech``
backend.  This adapter keeps the large local Qwen model resident instead of
loading it once for every narration/dialogue turn.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import io
import json
import os
import subprocess
import sys
import threading
import types
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


def _stub_unused_torchaudio_25hz(model_dir: Path) -> None:
    """Avoid importing an ABI-incompatible torchaudio path unused by 12 Hz TTS."""

    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("tokenizer_type") != "qwen3_tts_tokenizer_12hz":
        raise ValueError("the torchaudio compatibility shim only supports Qwen3-TTS 12 Hz")

    def stub(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        return module

    torchaudio = stub("torchaudio")
    compliance = stub("torchaudio.compliance")
    kaldi = stub("torchaudio.compliance.kaldi")
    compliance.kaldi = kaldi
    torchaudio.compliance = compliance
    sys.modules["torchaudio"] = torchaudio
    sys.modules["torchaudio.compliance"] = compliance
    sys.modules["torchaudio.compliance.kaldi"] = kaldi


class SpeechRequest(BaseModel):
    model: str = "Qwen3-TTS-12Hz-1.7B-CustomVoice"
    voice: str = Field(default="Uncle_Fu", min_length=1)
    input: str = Field(min_length=1, max_length=500)
    response_format: str = "wav"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    instructions: str | None = None


class QwenSpeechService:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir.resolve()
        self.model = None
        self.lock = threading.Lock()

    def load(self) -> None:
        _stub_unused_torchaudio_25hz(self.model_dir)
        from qwen_tts import Qwen3TTSModel

        self.model = Qwen3TTSModel.from_pretrained(
            str(self.model_dir),
            device_map="cuda:0",
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
        )

    @property
    def ready(self) -> bool:
        return self.model is not None

    @staticmethod
    def _speed_wav(payload: bytes, speed: float) -> bytes:
        if abs(speed - 1.0) < 0.005:
            return payload
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "wav", "-i", "pipe:0",
                "-filter:a", f"atempo={speed:.4f}", "-f", "wav", "pipe:1",
            ],
            input=payload,
            capture_output=True,
            check=True,
        )
        return result.stdout

    def synthesize(self, request: SpeechRequest) -> bytes:
        if request.response_format.lower() != "wav":
            raise ValueError("only response_format=wav is supported")
        if self.model is None:
            raise RuntimeError("Qwen TTS model is not ready")
        with self.lock:
            wavs, sample_rate = self.model.generate_custom_voice(
                text=request.input,
                speaker=request.voice,
                language="Chinese",
                instruct=request.instructions or "标准普通话，自然清晰，音色稳定。",
            )
        if not wavs or len(wavs[0]) == 0:
            raise RuntimeError("Qwen TTS returned empty audio")
        stream = io.BytesIO()
        sf.write(
            stream,
            np.asarray(wavs[0], dtype=np.float32),
            int(sample_rate),
            format="WAV",
            subtype="PCM_16",
        )
        return self._speed_wav(stream.getvalue(), request.speed)


def create_app(model_dir: Path) -> FastAPI:
    service = QwenSpeechService(model_dir)
    api_key = os.getenv("QWEN_TTS_API_KEY")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.load()
        yield

    app = FastAPI(title="Qwen3-TTS OpenAI-compatible server", lifespan=lifespan)

    def authorize(authorization: str | None) -> None:
        if api_key and authorization != f"Bearer {api_key}":
            raise HTTPException(status_code=401, detail="invalid bearer token")

    @app.get("/ready")
    def ready() -> dict[str, object]:
        return {"ready": service.ready, "model": model_dir.name}

    @app.get("/v1/models")
    def models(authorization: str | None = Header(default=None)) -> dict[str, object]:
        authorize(authorization)
        return {"object": "list", "data": [{"id": model_dir.name, "object": "model"}]}

    @app.post("/v1/audio/speech")
    def speech(
        request: SpeechRequest,
        authorization: str | None = Header(default=None),
    ) -> Response:
        authorize(authorization)
        try:
            payload = service.synthesize(request)
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            raise HTTPException(status_code=500, detail=str(error)[:1000]) from error
        return Response(payload, media_type="audio/wav")

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18003)
    args = parser.parse_args()
    uvicorn.run(create_app(args.model_dir), host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
