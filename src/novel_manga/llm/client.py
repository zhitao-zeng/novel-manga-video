"""Structured model requests with the existing per-task truncation policy."""
from __future__ import annotations
import base64
import io
import itertools
import json
import os
import re
import time
from pathlib import Path
import httpx
from PIL import Image
from .config import JsonEndpoint, endpoint_order, endpoint_key
from .transport import post_any

_CALL_COUNTER = itertools.count(os.getpid())

def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def image_part(path: Path, max_side: int) -> dict:
    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = min(1.0, max_side / max(image.size))
        if scale < 1.0:
            image = image.resize((round(image.width * scale), round(image.height * scale)))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}}


def ask_json(parts: list[dict], schema: dict, *, name: str, max_tokens: int = 700, timeout: float = 600.0, retry_truncated: bool = True, settings: JsonEndpoint | None = None) -> dict:
    """Ask a JSON question, sharing the timeout across endpoints and at most one length retry."""
    headers = {}
    key = settings.key if settings else endpoint_key()
    if key:
        headers["Authorization"] = "Bearer " + key
    payload = {
        "model": settings.model if settings else os.environ.get("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"), "temperature": 0, "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        "messages": [{"role": "user", "content": parts}],
    }
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for retry in range(2 if retry_truncated else 1):
            request_key = f"{name}-{next(_CALL_COUNTER)}"
            bases = endpoint_order(request_key, list(settings.endpoints)) if settings else endpoint_order(request_key)
            body = post_any(client, bases, headers, payload, settings=settings, deadline=deadline,
                            timeout_message=f"{name}: {timeout:g}s request budget exhausted")
            choice = body["choices"][0]
            content = choice["message"].get("content") or "{}"
            try:
                return json.loads(content)
            except json.JSONDecodeError as error:
                if choice.get("finish_reason") == "length":
                    if retry == 0 and retry_truncated and payload["max_tokens"] < 8000:
                        payload["max_tokens"] = min(8000, payload["max_tokens"] * 2)
                        log(f"{name}: JSON truncated; one retry with {payload['max_tokens']} tokens inside the remaining time budget")
                        continue
                    raise ValueError(f"{name}: JSON truncated at {payload['max_tokens']} output tokens") from error
                match = re.search(r"\{.*\}", content, re.S)
                return json.loads(match.group(0)) if match else {}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}

