from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import os
import hashlib

def qwen_endpoints() -> list[str]:
    """All local Qwen base URLs (QWEN38_LOCAL_BASE_URL may be comma-separated)."""
    raw = os.environ.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def endpoint_order(key: str, endpoints: list[str] | None = None) -> list[str]:
    """Endpoints in the order to try for one request: a stable pick by key
    (spreads chapters over instances) followed by the others as fallbacks."""
    endpoints = qwen_endpoints() if endpoints is None else endpoints
    start = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(endpoints)
    return endpoints[start:] + endpoints[:start]


def endpoint_key(environ=None) -> str:
    """The endpoint's key, read from the variable QWEN38_LOCAL_API_KEY_VAR names
    (default QWEN38_LOCAL_API_KEY); empty for the local vLLM."""
    environ = os.environ if environ is None else environ
    value = environ.get(environ.get("QWEN38_LOCAL_API_KEY_VAR", "QWEN38_LOCAL_API_KEY") or "QWEN38_LOCAL_API_KEY", "")
    path = environ.get("QWEN38_LOCAL_API_KEY_FILE", "").strip()
    if not value and path:
        try:
            value = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
    return value


def streaming_wanted() -> bool:
    return os.environ.get("QWEN38_LOCAL_STREAM", "").strip() == "1"


@dataclass(frozen=True)
class JsonEndpoint:
    model: str
    endpoints: tuple[str, ...]
    key: str = field(default="", repr=False)
    stream: bool = False
    reasoning: str = "low"
    min_max_tokens: int = 50000

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> JsonEndpoint:
        env = {**os.environ, **(overrides or {})}
        bases = env.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
        return cls(env.get("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"),
                   tuple(item.strip().rstrip("/") for item in bases.split(",") if item.strip()),
                   endpoint_key(env), env.get("QWEN38_LOCAL_STREAM", "").strip() == "1",
                   env.get("QWEN38_LOCAL_REASONING", "low"), int(env.get("QWEN38_LOCAL_MIN_MAX_TOKENS", "50000") or 0))

