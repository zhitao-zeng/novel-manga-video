"""Pre-flight liveness for one episode run: measured now, never a listing.

QWEN38_LOCAL_BASE_URL lists instances that stay in the list after they die
(test_endpoint_liveness.py records the bill), and PhanRouter's model listing
returned an empty table for a valid sd2.5 key (docs/shiri-sd25-pilot-20260929.md)
- so the two PhanRouter keys are probed with one real but minimal submission
each: the model is named, the content is empty, and the service's validation
refusal (400/422) proves the key passed authentication and routing before
anything could be billed.  A 401/403 is the key being refused.

Every probe answers within two seconds, and a detail names only the variable
and the host:port - never a key.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from novel_manga.application.configuration import environment, project_root
from novel_manga.llm.config import ENDPOINTS, JsonEndpoint

TIMEOUT_SECONDS = 2.0
PHANROUTER_DEFAULT_BASE = "https://cloud.phanthy.com/phanrouter"
VIDEO_TASKS_PATH = "/api/v3/contents/generations/tasks"
IMAGE_GENERATIONS_PATH = "/v3/images/generations"
ASR_MODEL_FILES = ("model.int8.onnx", "tokens.txt")  # what thin_asr_segments.py loads


def _host(url: str) -> str:
    parsed = httpx.URL(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.host}:{port}"


def _get(client, url, *, headers, timeout):
    if client is not None:
        return client.get(url, headers=headers, timeout=timeout)
    with httpx.Client(timeout=timeout, trust_env=False) as owned:
        return owned.get(url, headers=headers)


def _post(client, url, *, headers, payload, timeout):
    if client is not None:
        return client.post(url, headers=headers, json=payload, timeout=timeout)
    with httpx.Client(timeout=timeout, trust_env=False) as owned:
        return owned.post(url, headers=headers, json=payload)


def _head(client, url, *, headers, timeout):
    if client is not None:
        return client.head(url, headers=headers, timeout=timeout)
    with httpx.Client(timeout=timeout, trust_env=False) as owned:
        return owned.head(url, headers=headers)


def check_endpoint(base, name, *, key="", client=None, timeout=TIMEOUT_SECONDS, detail=None):
    """One OpenAI-compatible endpoint is alive when its /models answers 200."""
    shown = detail or _host(base)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = _get(client, f"{base.rstrip('/')}/models", headers=headers, timeout=timeout)
    except httpx.HTTPError as error:
        return {"name": name, "ok": False, "detail": f"{shown} {type(error).__name__}"}
    return {"name": name, "ok": response.status_code == 200,
            "detail": f"{shown} HTTP {response.status_code}"}


def _permission_probe(client, url, name, detail, key, payload, timeout):
    """A submission the service must refuse on validation proves the key passed auth."""
    if not key:
        return {"name": name, "ok": False, "detail": f"{detail} key not set"}
    try:
        response = _post(client, url, headers={"Authorization": f"Bearer {key}"},
                         payload=payload, timeout=timeout)
    except httpx.HTTPError as error:
        return {"name": name, "ok": False, "detail": f"{detail} {type(error).__name__}"}
    status = response.status_code
    if status in (400, 422) or 200 <= status < 300:
        return {"name": name, "ok": True, "detail": f"{detail} HTTP {status}"}
    refused = " key refused" if status in (401, 403) else ""
    return {"name": name, "ok": False, "detail": f"{detail} HTTP {status}{refused}"}


def _text_checks(env, client, timeout):
    checks = []
    current = JsonEndpoint.from_env(env)
    for index, base in enumerate(current.endpoints):
        checks.append(check_endpoint(
            base, f"text:qwen38_local[{index}]", key=current.key, client=client, timeout=timeout,
            detail=f"QWEN38_LOCAL_BASE_URL[{index}] {_host(base)}"))
    for preset in ("flashnext", "deepseek"):
        endpoint = JsonEndpoint.from_env({**env, **ENDPOINTS[preset]})
        base = endpoint.endpoints[0]
        checks.append(check_endpoint(
            base, f"text:{preset}", key=endpoint.key, client=client, timeout=timeout,
            detail=f"{preset} {_host(base)}"))
    return checks


def _asset_check(env, client, base, key, timeout):
    name = "assets:phanrouter_library"
    if not (env.get("PHANROUTER_ASSET_GROUP_ID") or "").strip():
        return {"name": name, "ok": False, "detail": "PHANROUTER_ASSET_GROUP_ID not set"}
    asset_base = base[: -len("/api")] if base.endswith("/api") else base
    host = _host(base)
    try:
        response = _head(client, f"{asset_base}/open/CreateAsset",
                         headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    except httpx.HTTPError as error:
        return {"name": name, "ok": False,
                "detail": f"PHANROUTER_REFERENCE_ASSETS {host} {type(error).__name__}"}
    status = response.status_code
    # 405 is the expected answer to HEAD on a POST route: the library answered.
    ok = status < 500 and status not in (401, 403)
    return {"name": name, "ok": ok, "detail": f"PHANROUTER_REFERENCE_ASSETS {host} HTTP {status}"}


def _phanrouter_checks(env, client, timeout):
    base = (env.get("PHANROUTER_BASE_URL") or PHANROUTER_DEFAULT_BASE).rstrip("/")
    host = _host(base)
    video_var = (env.get("PHANROUTER_VIDEO_KEY_VAR") or "").strip() or "PHANROUTER_API_KEY"
    video_key = env.get(video_var, "")
    image_var = "PHANROUTER_IMAGE_API_KEY" if env.get("PHANROUTER_IMAGE_API_KEY") else video_var
    image_key = env.get("PHANROUTER_IMAGE_API_KEY") or video_key
    video_model = env.get("NOVEL_VIDEO_MODEL") or env.get("PHANROUTER_VIDEO_MODEL") or "sd2.5"
    image_model = env.get("NOVEL_IMAGE_MODEL") or env.get("PHANROUTER_IMAGE_MODEL") or "gpt-image-2"
    checks = [
        _permission_probe(client, f"{base}{VIDEO_TASKS_PATH}", "video:phanrouter_key",
                          f"{video_var} {video_model} {host}", video_key,
                          {"model": video_model, "content": []}, timeout),
        _permission_probe(client, f"{base}{IMAGE_GENERATIONS_PATH}", "image:phanrouter_key",
                          f"{image_var} {image_model} {host}", image_key,
                          {"model": image_model, "prompt": ""}, timeout),
    ]
    if (env.get("PHANROUTER_REFERENCE_ASSETS") or "").strip().lower() in {"1", "true", "yes"}:
        checks.append(_asset_check(env, client, base, video_key, timeout))
    return checks


def _local_image_checks(env, client, timeout):
    url = (env.get("NOVEL_LOCAL_QWEN_IMAGE_URL") or "").strip()
    if not url:
        return []
    detail = f"NOVEL_LOCAL_QWEN_IMAGE_URL {_host(url)}"
    try:
        response = _get(client, f"{url.rstrip('/')}/health", headers={}, timeout=timeout)
    except httpx.HTTPError as error:
        return [{"name": "image:local_qwen", "ok": False, "detail": f"{detail} {type(error).__name__}"}]
    return [{"name": "image:local_qwen", "ok": response.status_code == 200,
             "detail": f"{detail} HTTP {response.status_code}"}]


def _asr_check(env):
    name = "asr:model_dir"
    raw = (env.get("NOVEL_SENSEVOICE_MODEL_DIR") or "").strip()
    if not raw:
        return {"name": name, "ok": False, "detail": "NOVEL_SENSEVOICE_MODEL_DIR not set"}
    directory = Path(raw)
    if not directory.is_dir():
        return {"name": name, "ok": False, "detail": f"NOVEL_SENSEVOICE_MODEL_DIR {raw} not a directory"}
    missing = [member for member in ASR_MODEL_FILES if not (directory / member).is_file()]
    if missing:
        return {"name": name, "ok": False,
                "detail": f"NOVEL_SENSEVOICE_MODEL_DIR {raw} missing {', '.join(missing)}"}
    return {"name": name, "ok": True, "detail": f"NOVEL_SENSEVOICE_MODEL_DIR {raw}"}


def run_doctor(novel_dir, *, client=None, environ=None, timeout=TIMEOUT_SECONDS):
    """Probe every external service one episode run depends on; novel_dir anchors the run.

    Returns {"ok": bool, "checks": [{"name", "ok", "detail"}]}.  environ defaults to the
    project environment (process variables with the project .env as defaults), the same
    view thin_asr_segments.py reads.
    """
    env = environment(project_root()) if environ is None else dict(environ)
    checks = [*_text_checks(env, client, timeout),
              *_phanrouter_checks(env, client, timeout),
              *_local_image_checks(env, client, timeout),
              _asr_check(env)]
    return {"ok": all(row["ok"] for row in checks), "checks": checks}
