from __future__ import annotations

from ..config import Settings
from ..planner import CommandPlanner, DeterministicPlanner, OpenAICompatiblePlanner, Planner
from .base import ProviderBundle
from .command import CommandMediaProvider
from .mock import MockMediaProvider
from .phanrouter import PhanRouterMediaProvider


def build_planner(settings: Settings) -> Planner:
    if settings.planner_backend == "command" or (
        settings.planner_backend == "auto" and settings.planner_command
    ):
        return CommandPlanner(settings)
    elif settings.planner_backend == "openai-compatible" or (
        settings.planner_backend == "auto" and settings.llm_base_url and settings.llm_api_key
    ):
        return OpenAICompatiblePlanner(settings)
    return DeterministicPlanner()


def build_providers(settings: Settings) -> ProviderBundle:
    planner = build_planner(settings)
    if settings.provider == "mock":
        return ProviderBundle(planner=planner, media=MockMediaProvider(settings))
    if settings.provider == "phanrouter":
        return ProviderBundle(planner=planner, media=PhanRouterMediaProvider(settings))
    if settings.provider == "command":
        return ProviderBundle(planner=planner, media=CommandMediaProvider(settings))
    raise ValueError(f"unknown provider: {settings.provider}")
