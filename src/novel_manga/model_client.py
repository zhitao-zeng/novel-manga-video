"""Shared JSON model transport and image parts; no production workflow imports.

Endpoint ordering, truncation retry, timeout budget and stream reconstruction
are the existing batch policies. Callers own their prompts and scheduling.
"""
from __future__ import annotations
import base64
import hashlib
import io
import itertools
import json
import os
import re
import time
from pathlib import Path
import httpx
from PIL import Image

def qwen_endpoints() -> list[str]:
    """All local Qwen base URLs (QWEN38_LOCAL_BASE_URL may be comma-separated)."""
    raw = os.environ.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def endpoint_order(key: str) -> list[str]:
    """Endpoints in the order to try for one request: a stable pick by key
    (spreads chapters over instances) followed by the others as fallbacks."""
    endpoints = qwen_endpoints()
    start = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(endpoints)
    return endpoints[start:] + endpoints[:start]


BASE_URL = os.environ.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")


MODEL = os.environ.get("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project")


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


_CALL_COUNTER = itertools.count(os.getpid())  # episode workers must not all start at verify-0


def stream_completion(client: httpx.Client, url: str, headers: dict, payload: dict, timeout: float | None = None) -> dict:
    """POST with stream=True and rebuild the non-streaming response body.

    A platform behind a proxy cuts non-streaming requests at about 60 s, far
    less than a planner call takes; streaming keeps the connection alive.
    The vLLM-only chat_template_kwargs is dropped and the platform's own
    reasoning_effort field is set instead.
    """
    request = {k: v for k, v in payload.items() if k != "chat_template_kwargs"}
    request.update({"stream": True, "stream_options": {"include_usage": True}})
    request.setdefault("reasoning_effort", os.environ.get("QWEN38_LOCAL_REASONING", "low"))
    # The callers' budgets fit the local model's context window; a platform
    # model has room to spare but counts its reasoning inside max_tokens.
    floor = int(os.environ.get("QWEN38_LOCAL_MIN_MAX_TOKENS", "50000") or 0)
    if floor > 0:
        request["max_tokens"] = max(int(request.get("max_tokens") or 0), floor)
    content, reasoning, finish, usage = [], [], None, None
    with client.stream("POST", url, headers=headers, json=request, timeout=timeout) as response:
        if response.status_code >= 400:
            response.read()
            response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("error"):
                raise RuntimeError(f"streamed error from {url}: {json.dumps(event['error'], ensure_ascii=False)[:300]}")
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                content.append(delta.get("content") or "")
                reasoning.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
                finish = choice.get("finish_reason") or finish
            usage = event.get("usage") or usage
    if not "".join(content) and not "".join(reasoning):
        raise RuntimeError(f"empty streamed response from {url} (finish={finish}, usage={usage})")
    return {"choices": [{"message": {"content": "".join(content), "reasoning": "".join(reasoning)}, "finish_reason": finish}], "usage": usage}


def endpoint_key() -> str:
    """The endpoint's key, read from the variable QWEN38_LOCAL_API_KEY_VAR names
    (default QWEN38_LOCAL_API_KEY); empty for the local vLLM."""
    value = os.environ.get(os.environ.get("QWEN38_LOCAL_API_KEY_VAR", "QWEN38_LOCAL_API_KEY") or "QWEN38_LOCAL_API_KEY", "")
    path = os.environ.get("QWEN38_LOCAL_API_KEY_FILE", "").strip()
    if not value and path:
        try:
            value = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
    return value


def streaming_wanted() -> bool:
    return os.environ.get("QWEN38_LOCAL_STREAM", "").strip() == "1"


def ask_json(parts: list[dict], schema: dict, *, name: str, max_tokens: int = 700, timeout: float = 600.0, retry_truncated: bool = True) -> dict:
    """Ask a JSON question, sharing the timeout across endpoints and at most one length retry."""
    headers = {}
    if endpoint_key():
        headers["Authorization"] = "Bearer " + endpoint_key()
    payload = {
        "model": MODEL, "temperature": 0, "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        "messages": [{"role": "user", "content": parts}],
    }
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for retry in range(2 if retry_truncated else 1):
            last: Exception | None = None
            for base_url in endpoint_order(f"{name}-{next(_CALL_COUNTER)}"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{name}: {timeout:g}s request budget exhausted")
                try:
                    if streaming_wanted():
                        body = stream_completion(client, f"{base_url}/chat/completions", headers, payload, timeout=remaining)
                        break
                    response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=remaining)
                    response.raise_for_status()
                    body = response.json()
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as error:
                    last = error
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in (502, 503, 504):
                        last = error
                        continue
                    raise
            else:
                assert last is not None
                raise last
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
