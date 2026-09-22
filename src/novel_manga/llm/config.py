from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import contextlib
import os
import hashlib

# The two model endpoints this project runs on, by name.  QWEN38_LOCAL_* is read by three different
# jobs - the planner, the H3 prompt translation and the judge - so moving one of them used to move all
# three.  A command applies the preset it wants to its own process, and nothing else changes.
#
# Neither streams.  The streaming transport was written for a platform model behind a proxy: it drops
# chat_template_kwargs as "vLLM-only" and raises max_tokens to a floor of 50,000, because such a model
# counts its reasoning inside the budget.  Flash-Next is vLLM, reached directly, and chat_template_kwargs
# is where every request in this project says enable_thinking: False.  With the field stripped it thought
# for as long as it was allowed: 38,094 tokens on one chapter's first attempt, and seventeen minutes on a
# fill-in-the-blanks binding that then came back finish_reason=length with its JSON cut off.  Asked the
# same question directly it answers in 0.4 s with the switch and starts reasoning without it.
ENDPOINTS = {
    "local": {"QWEN38_LOCAL_BASE_URL": ",".join(f"http://127.0.0.1:{p}/v1" for p in range(18120, 18125)),
              "QWEN38_LOCAL_MODEL": "Qwen3.8-27B-Project",
              "QWEN38_LOCAL_API_KEY_VAR": "SECOND_REVIEW_NO_KEY", "QWEN38_LOCAL_STREAM": "0"},
    "flashnext": {"QWEN38_LOCAL_BASE_URL": "http://172.28.4.81:8038/v1",
                  "QWEN38_LOCAL_MODEL": "Qwen3.8-Flash-Next",
                  "QWEN38_LOCAL_API_KEY_VAR": "GPU81_QWEN_API_KEY", "QWEN38_LOCAL_STREAM": "0"},
}


def planner_endpoint_name(environ=None) -> str:
    """Which endpoint plans a chapter: Flash-Next unless NOVEL_PLANNER_ENDPOINT names another.

    Read here so the planner and the batch preflight that probes on its behalf agree.  An empty value
    is the default, not an endpoint called "": `NOVEL_PLANNER_ENDPOINT= python
    scripts/plan_chapter_thin.py --help` used to trace back before argparse ran.  A value naming
    nothing is refused with the list, since the alternative is planning on whatever the process
    environment happened to hold.
    """
    environ = os.environ if environ is None else environ
    name = str(environ.get("NOVEL_PLANNER_ENDPOINT", "") or "").strip() or "flashnext"
    if name not in ENDPOINTS:
        raise ValueError(f"NOVEL_PLANNER_ENDPOINT={name!r} names no endpoint; pick one of {sorted(ENDPOINTS)}")
    return name


def endpoint_settings(name: str) -> JsonEndpoint:
    """A named endpoint as the settings for one call, with the process environment left alone."""
    if name not in ENDPOINTS:
        raise ValueError(f"unknown endpoint {name}; pick one of {sorted(ENDPOINTS)}")
    return JsonEndpoint.from_env(ENDPOINTS[name])


@contextlib.contextmanager
def using_endpoint(name: str, environ=None):
    """Point this process's model calls at one named endpoint, and put back what was there.

    Restoring matters even though a command usually exits right after: the same process runs several
    commands under test, and a preset that outlives its caller silently re-points everything that
    reads QWEN38_LOCAL_* afterwards - which is the failure mode this registry exists to prevent.
    """
    if name not in ENDPOINTS:
        raise ValueError(f"unknown endpoint {name}; pick one of {sorted(ENDPOINTS)}")
    environ = os.environ if environ is None else environ
    before = {key: environ.get(key) for key in ENDPOINTS[name]}
    environ.update(ENDPOINTS[name])
    try:
        yield name
    finally:
        for key, value in before.items():
            if value is None:
                environ.pop(key, None)
            else:
                environ[key] = value


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

