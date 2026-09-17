"""Minimal media flow for focused tests that intentionally skip provider construction."""
from novel_manga.application.rendering.flow import ThinMediaRunner
from novel_manga.media.context import RenderContext


def uninitialized_runner():
    runner = object.__new__(ThinMediaRunner)
    runner.context = RenderContext()
    return runner
