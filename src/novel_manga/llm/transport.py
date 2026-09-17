"""Shared HTTP and response-stream transport; callers retain task retry policies."""
from __future__ import annotations
import json
import os
import time
import httpx
from .config import JsonEndpoint, streaming_wanted

def stream_completion(client: httpx.Client, url: str, headers: dict, payload: dict, timeout: float | None = None, *, settings: JsonEndpoint | None = None) -> dict:
    """POST with stream=True and rebuild the non-streaming response body.

    A platform behind a proxy cuts non-streaming requests at about 60 s, far
    less than a planner call takes; streaming keeps the connection alive.
    The vLLM-only chat_template_kwargs is dropped and the platform's own
    reasoning_effort field is set instead.
    """
    request = {k: v for k, v in payload.items() if k != "chat_template_kwargs"}
    request.update({"stream": True, "stream_options": {"include_usage": True}})
    request.setdefault("reasoning_effort", settings.reasoning if settings else os.environ.get("QWEN38_LOCAL_REASONING", "low"))
    # The callers' budgets fit the local model's context window; a platform
    # model has room to spare but counts its reasoning inside max_tokens.
    floor = settings.min_max_tokens if settings else int(os.environ.get("QWEN38_LOCAL_MIN_MAX_TOKENS", "50000") or 0)
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


_DEFAULT_TIMEOUT = object()


def send_json(client, url, headers, payload, *, timeout=_DEFAULT_TIMEOUT):
    options = {} if timeout is _DEFAULT_TIMEOUT else {'timeout': timeout}
    return client.post(url, headers=headers, json=payload, **options)


def post_any(client, base_urls, headers, request, *, settings=None, deadline=None, timeout_message='model time budget exhausted'):
    last = None
    for base_url in base_urls:
        options = {}
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(timeout_message)
            options['timeout'] = remaining
        try:
            url = f"{base_url.rstrip('/')}/chat/completions"
            if settings.stream if settings else streaming_wanted():
                return stream_completion(client, url, headers, request, settings=settings, **options)
            response = send_json(client, url, headers, request, **options)
            response.raise_for_status()
            return response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as error:
            last = error
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (502, 503, 504):
                raise
            last = error
    assert last is not None
    raise last
