"""Existing judge presets as explicit request settings, without import side effects."""
import os
from novel_manga.llm.config import ENDPOINTS, JsonEndpoint

# The same two presets the planner picks from; defined once in llm.config.
JUDGES = ENDPOINTS


def judge_settings(name: str | None = None) -> JsonEndpoint:
    name = name or os.environ.get("SECOND_REVIEW_JUDGE", "local")
    if name not in JUDGES:
        raise ValueError(f"unknown judge {name}; pick one of {sorted(JUDGES)}")
    return JsonEndpoint.from_env(JUDGES[name])
