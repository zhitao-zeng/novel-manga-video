from novel_manga.config import Settings
from .planner import CommandPlanner, DeterministicPlanner, OpenAICompatiblePlanner, Planner


def build_planner(settings: Settings) -> Planner:
    if settings.planner_backend == "command" or (
        settings.planner_backend == "auto" and settings.planner_command
    ):
        return CommandPlanner(settings)
    elif settings.planner_backend == "openai-compatible" or (
        settings.planner_backend == "auto" and settings.llm_base_url and settings.llm_api_key
    ):
        return OpenAICompatiblePlanner(settings)
    # Deterministic fallback is intentionally faithful/chronological; it
    # cannot make semantic short-drama selections without a planner model.
    return DeterministicPlanner()

