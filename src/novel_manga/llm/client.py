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


def scaled_timeout(timeout: float, parts: list[dict]) -> float:
    """Reasoning models think for minutes before answering; a flat timeout tuned for the old
    fast local 27B (2026-09) kills big reviews mid-thought (ch473 binding review, 2026-10-10).
    Scale with input size; the caller's value is the floor for small asks."""
    chars = sum(len(str(p.get('text') or '')) + len(str(p.get('image_url') or '')) // 200 for p in parts)
    return max(timeout, min(1800.0, 60.0 + chars * 0.015))


def ask_json(parts: list[dict], schema: dict, *, name: str, max_tokens: int = 700, timeout: float = 600.0, retry_truncated: bool = True, settings: JsonEndpoint | None = None, json_object: bool = False) -> dict:
    """Ask a JSON question, sharing the timeout across endpoints and at most one length retry."""
    timeout = scaled_timeout(timeout, parts)
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
    if json_object:
        # Some deployed services accept json_schema but corrupt free-form string arrays.
        # These callers validate their own result; make the shape visible to the model.
        payload['response_format'] = {'type': 'json_object'}
        payload['messages'][0]['content'] = [*parts, {'type': 'text', 'text':
            'Return a JSON object matching this schema: ' + json.dumps(schema, ensure_ascii=False)}]
    from novel_manga.resource_waits import RequestDeadline
    deadline = RequestDeadline(timeout)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for retry in range(3 if retry_truncated else 1):
            request_key = f"{name}-{next(_CALL_COUNTER)}"
            bases = endpoint_order(request_key, list(settings.endpoints)) if settings else endpoint_order(request_key)
            body = post_any(client, bases, headers, payload, settings=settings, deadline=deadline,
                            timeout_message=f"{name}: {timeout:g}s request budget exhausted")
            choice = body["choices"][0]
            content = choice["message"].get("content") or "{}"
            from .refusal import refusal_reason
            fallback = os.environ.get('NOVEL_REFUSAL_FALLBACK')
            refused = choice['message'].get('refusal') or (
                refusal_reason(content) if not content.lstrip().startswith(('{', '[')) else '')
            if fallback and 'deepseek' in payload['model'].lower() and refused:
                from .config import endpoint_settings
                settings = endpoint_settings(fallback)
                headers = {'Authorization': 'Bearer '+settings.key} if settings.key else {}
                payload = {**payload, 'model': settings.model}
                log(f'{name}: explicit model refusal; switching once to {settings.model} within the same request budget')
                body = post_any(client, list(settings.endpoints), headers, payload, settings=settings,
                                deadline=deadline, timeout_message=f'{name}: fallback request budget exhausted')
                choice = body['choices'][0]
                content = choice['message'].get('content') or '{}'
            try:
                if choice.get('finish_reason') == 'length' and not choice['message'].get('content'):
                    # Thinking models may exhaust the budget before producing
                    # any JSON. The fallback "{}" is not a successful answer.
                    raise json.JSONDecodeError('empty truncated JSON response', '', 0)
                return json.loads(content)
            except json.JSONDecodeError as error:
                if choice.get("finish_reason") == "length":
                    # 2026-10-10: 主力切 Phanthy API 后网关实测可自然输出 9890 tokens，
                    # 重试上限从 8000 提到 16000（含 reasoning 型把预算耗在推理上的情形）。
                    # 另一实测：reasoning 偶发失控与预算无关——同一 prompt 一次 0 内容一次正常
                    # （13000 两次全空 vs 重发即 498 字正常）。预算翻倍后仍空，按原预算重发一次赌随机性。
                    empty = not choice['message'].get('content')
                    if retry == 0 and retry_truncated and payload["max_tokens"] < 16000:
                        payload["max_tokens"] = min(16000, payload["max_tokens"] * 2)
                        log(f"{name}: JSON truncated; one retry with {payload['max_tokens']} tokens inside the remaining time budget")
                        continue
                    if empty and retry == 1 and retry_truncated:
                        payload['max_tokens'] = max(700, payload['max_tokens'] // 2)
                        log(f"{name}: empty truncated reasoning again; one reset-budget retry with {payload['max_tokens']} tokens")
                        continue
                    raise ValueError(f"{name}: JSON truncated at {payload['max_tokens']} output tokens") from error
                match = re.search(r"\{.*\}", content, re.S)
                return json.loads(match.group(0)) if match else {}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}
