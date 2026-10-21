"""Shared HTTP and response-stream transport; callers retain task retry policies."""
from __future__ import annotations
import json
import os
import time
import httpx
from .config import JsonEndpoint, streaming_wanted
from .resources import model_slot


class IncompleteResponse(RuntimeError):
    """The service did not deliver a completed answer; retry policy stays with the caller."""


def stream_completion(client: httpx.Client, url: str, headers: dict, payload: dict, timeout: float | None = None, *, settings: JsonEndpoint | None = None) -> dict:
    """POST with stream=True and rebuild the non-streaming response body.

    A platform behind a proxy cuts non-streaming requests at about 60 s, far
    less than a planner call takes; streaming keeps the connection alive.
    The vLLM-only chat_template_kwargs is dropped and the platform's own
    reasoning_effort field is set instead.
    """
    request = {k: v for k, v in payload.items() if k != "chat_template_kwargs"}
    # DS4.1 uses an API-level switch rather than the local vLLM template flag.
    # Preserve the caller's existing non-thinking intent and its output budget.
    if (request.get('model') in {'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017'}
            and (payload.get('chat_template_kwargs') or {}).get('enable_thinking') is False):
        request.setdefault('thinking', {'type': 'disabled'})
    request.update({"stream": True, "stream_options": {"include_usage": True}})
    request.setdefault("reasoning_effort", settings.reasoning if settings else os.environ.get("QWEN38_LOCAL_REASONING", "low"))
    # The callers' budgets fit the local model's context window; a platform
    # model has room to spare but counts its reasoning inside max_tokens.
    floor = settings.min_max_tokens if settings else int(os.environ.get("QWEN38_LOCAL_MIN_MAX_TOKENS", "50000") or 0)
    if floor > 0:
        request["max_tokens"] = max(int(request.get("max_tokens") or 0), floor)
    content, reasoning, finish, usage = [], [], None, None
    response_id, request_id, served_model = None, None, None
    def metering_body(status='answered'):
        return {'usage': usage,
                **{k: v for k, v in [('id', response_id), ('_request_id', request_id),
                    ('_served_model', served_model)] if v is not None},
                **({'_status': status} if status != 'answered' else {})}
    ends_at = time.monotonic() + timeout if timeout is not None else None
    try:
        with client.stream("POST", url, headers=headers, json=request, timeout=timeout) as response:
            request_id = response.headers.get('x-phanthy-request-id') or response.headers.get('x-request-id')
            if response.status_code >= 400:
                response.read()
                response.raise_for_status()
            for line in response.iter_lines():
                if ends_at is not None and time.monotonic() >= ends_at:
                    raise httpx.ReadTimeout("stream request budget exhausted", request=response.request)
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                usage = event.get("usage") or usage
                response_id = event.get('id') or response_id
                served_model = event.get('model') or served_model
                if isinstance(event, dict) and event.get("error"):
                    error = event['error']
                    _meter(url, request, metering_body('incomplete'))
                    retryable = isinstance(error, dict) and error.get('code') in {
                        'upstream_failure', 'upstream_stream_error', 'upstream_unavailable'}
                    failure = IncompleteResponse if retryable else RuntimeError
                    raise failure(f"streamed error from {url}: {json.dumps(error, ensure_ascii=False)[:300]}")
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content.append(delta.get("content") or "")
                    reasoning.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
                    finish = choice.get("finish_reason") or finish
    except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as error:
        if not isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
            _meter(url, request, metering_body('incomplete'))
        raise
    if not "".join(content) and not "".join(reasoning) and finish != 'length':
        _meter(url, request, metering_body('incomplete'))
        raise IncompleteResponse(f"empty streamed response from {url} (finish={finish}, usage={usage})")
    if finish is None:
        _meter(url, request, metering_body('incomplete'))
        raise IncompleteResponse(f"unfinished streamed response from {url}: missing finish_reason")
    return {**metering_body(), "choices": [{"message": {"content": "".join(content), "reasoning": "".join(reasoning)}, "finish_reason": finish}]}


_DEFAULT_TIMEOUT = object()


def send_json(client, url, headers, payload, *, timeout=_DEFAULT_TIMEOUT):
    options = {} if timeout is _DEFAULT_TIMEOUT else {'timeout': timeout}
    return client.post(url, headers=headers, json=payload, **options)


# An endpoint that cannot be reached, and when to try it again.  The list in QWEN38_LOCAL_BASE_URL is a
# list of candidates; whether one is up is a fact about right now, and it was being kept in a file that
# only changes when somebody edits it.  So every request whose hash started on a stopped instance paid
# the connect timeout before falling through - 3.1 s each, for a quarter of them, all day.  The H3 pool
# has had this for months (configs/h3_pool.json: unreachable_cooldown_minutes); the model client had not.
_UNREACHABLE: dict[str, float] = {}
UNREACHABLE_COOLDOWN = float(os.environ.get("NOVEL_ENDPOINT_COOLDOWN_SECONDS", "300"))


def _meter(url: str, request: dict, body: dict) -> None:
    """One usage row per answered call; never raises into the transport path."""
    try:
        from . import usage as llm_usage
        if not isinstance(request.get('model'), str) or not request['model']:
            return
        rf = request.get('response_format') or {}
        name = ((rf.get('json_schema') or {}).get('name')) or rf.get('type') or 'chat'
        metadata = {'protocol': 'chat_completions', 'status': body.get('_status', 'answered'),
                    'usage_known': bool(body.get('usage'))}
        for field, key in [('response_id', 'id'), ('request_id', '_request_id'),
                           ('served_model', '_served_model'), ('http_status', '_http_status')]:
            if body.get(key) is not None:
                metadata[field] = body[key]
        with llm_usage.scope(**metadata):
            llm_usage.record(name=name, model=request['model'], usage=body.get('usage'), url=url)
    except Exception:  # noqa: BLE001 - metering must never break a model call
        pass


def reachable_first(base_urls):
    """The same endpoints, with the ones in cooldown moved to the back rather than dropped.

    Moved, not removed: a cooldown is a guess about an endpoint, and a guess must not be able to leave
    a request with nowhere to go.  When every endpoint is in cooldown this returns all of them, in the
    order they were given, and the request tries them exactly as it used to.
    """
    now = time.monotonic()
    live = [url for url in base_urls if _UNREACHABLE.get(url, 0) <= now]
    return live + [url for url in base_urls if url not in live] if live else list(base_urls)


def post_any(client, base_urls, headers, request, *, settings=None, deadline=None, timeout_message='model time budget exhausted'):
    from novel_manga.resource_waits import RequestDeadline
    budget=deadline
    if deadline is not None and not isinstance(deadline,RequestDeadline):
        budget=RequestDeadline(max(0,deadline-time.monotonic()))
    last = None
    for base_url in reachable_first(list(base_urls)):
        streaming = settings.stream if settings else streaming_wanted()
        url = f"{base_url.rstrip('/')}/chat/completions"
        try:
            waiting=time.monotonic()
            with model_slot(base_url):
                options = {}
                if budget is not None:
                    budget.waited(time.monotonic()-waiting)
                    remaining = budget.remaining()
                    if remaining <= 0:
                        raise TimeoutError(timeout_message)
                    options['timeout'] = remaining
                url = f"{base_url.rstrip('/')}/chat/completions"
                if streaming:
                    body = stream_completion(client, url, headers, request, settings=settings, **options)
                    _meter(url, request, body)
                    return body
                response = send_json(client, url, headers, request, **options)
                response.raise_for_status()
                _UNREACHABLE.pop(base_url, None)   # it answered: whatever we believed about it is stale
                body = response.json()
                response_headers = getattr(response, 'headers', {})
                request_id = response_headers.get('x-phanthy-request-id') or response_headers.get('x-request-id')
                if request_id is not None:
                    body['_request_id'] = request_id
                if body.get('model') is not None:
                    body['_served_model'] = body['model']
                _meter(url, request, body)
                return body
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as error:
            if not streaming or not isinstance(error, httpx.RemoteProtocolError):
                _meter(url, request, {'_status': 'connection_error'})
            _UNREACHABLE[base_url] = time.monotonic() + UNREACHABLE_COOLDOWN
            last = error
        except IncompleteResponse as error:
            last = error
        except httpx.HTTPStatusError as error:
            _meter(url, request, {'_status': 'http_error', '_http_status': error.response.status_code,
                '_request_id': error.response.headers.get('x-phanthy-request-id') or error.response.headers.get('x-request-id')})
            if error.response.status_code not in (502, 503, 504):
                raise
            # a gateway that cannot reach its backend is the same situation from the caller's side
            _UNREACHABLE[base_url] = time.monotonic() + UNREACHABLE_COOLDOWN
            last = error
        except (httpx.NetworkError, httpx.TimeoutException):
            if not streaming:
                _meter(url, request, {'_status': 'connection_error'})
            raise
    assert last is not None
    raise last
