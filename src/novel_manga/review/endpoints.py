"""Existing judge presets as explicit request settings, without import side effects."""
import os
from novel_manga.model_client import JsonEndpoint

JUDGES = {
    "local": {"QWEN38_LOCAL_BASE_URL": ",".join(f"http://127.0.0.1:{p}/v1" for p in range(18120, 18125)),
              "QWEN38_LOCAL_MODEL": "Qwen3.8-27B-Project",
              "QWEN38_LOCAL_API_KEY_VAR": "SECOND_REVIEW_NO_KEY", "QWEN38_LOCAL_STREAM": "0"},
    "flashnext": {"QWEN38_LOCAL_BASE_URL": "http://172.28.4.81:8038/v1",
                  "QWEN38_LOCAL_MODEL": "Qwen3.8-Flash-Next",
                  "QWEN38_LOCAL_API_KEY_VAR": "GPU81_QWEN_API_KEY", "QWEN38_LOCAL_STREAM": "1"},
}


def judge_settings(name: str | None = None) -> JsonEndpoint:
    name = name or os.environ.get("SECOND_REVIEW_JUDGE", "local")
    if name not in JUDGES:
        raise ValueError(f"unknown judge {name}; pick one of {sorted(JUDGES)}")
    return JsonEndpoint.from_env(JUDGES[name])
