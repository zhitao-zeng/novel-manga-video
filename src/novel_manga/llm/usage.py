"""Per-call LLM usage metering, append-only and fail-silent.

Every model call in this project funnels through llm.transport.post_any, which records
here.  Episode attribution is opt-in: entry points that know the episode wrap their work
in `scope(...)`; calls outside a scope still record name/model/tokens.
"""
from __future__ import annotations
import contextlib
import contextvars
import fcntl
import json
import threading
import time
from pathlib import Path

FILE = 'llm-usage.jsonl'
_LOCK = threading.Lock()
_SCOPE: contextvars.ContextVar[dict] = contextvars.ContextVar('llm_usage_scope', default={})

USAGE_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens',
              'input_tokens', 'output_tokens', 'cache_read_input_tokens',
              'cache_creation_input_tokens', 'reasoning_tokens')


@contextlib.contextmanager
def scope(**fields):
    """Tag every model call made inside this block with these fields (e.g. novel, episode)."""
    token = _SCOPE.set({**_SCOPE.get(), **fields})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scoped(**fields) -> dict:
    """Current scope merged with the given fields, for callers that record manually."""
    return {**_SCOPE.get(), **fields}


def record(*, name: str, model: str, usage: dict | None, url: str = '') -> bool:
    if not usage and _SCOPE.get().get('usage_known') is not False:
        return False
    usage = usage or {}
    try:
        from novel_manga.application.configuration import project_root
        path = project_root() / '.runtime' / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        counts = {k: usage[k] for k in USAGE_KEYS if usage.get(k) is not None}
        for field, details, key in [
                ('cache_read_input_tokens', 'prompt_tokens_details', 'cached_tokens'),
                ('reasoning_tokens', 'completion_tokens_details', 'reasoning_tokens')]:
            value = (usage.get(details) or {}).get(key)
            if value is not None:
                counts.setdefault(field, value)
        row = {'ts': time.time(), 'name': name, 'model': model or '',
               'endpoint': url.removesuffix('/chat/completions') if url else '',
               **counts,
               **_SCOPE.get()}
        with _LOCK:
            with path.open('a', encoding='utf-8') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        return True
    except Exception:  # noqa: BLE001 - metering must never break a model call
        return False
