"""A sheet somebody already cut is not a chapter to write.

The sandbox backend reaches the binding path by setting --bind-storyboard, and everything below it
was already correct.  What it did not look at was a branch further down: call_model asks for a story
method first and returns whatever that method generates, so a book naming a local method took it even
for a chapter holding an accepted sandbox storyboard - the method wrote its own scenes, and merge()
then failed on an authored shot it had never produced.

It has never fired, because no book sets story_method today.  That is luck, not design.
"""
from __future__ import annotations

import pytest

from novel_manga.application.planning import requests
from novel_manga.planning.context import PlannerContext


@pytest.fixture
def watched(monkeypatch):
    """call_model with both routes replaced, so the answer is which one it took."""
    taken = []
    monkeypatch.setattr("novel_manga.application.planning.method_pipeline.generate",
                        lambda **kwargs: taken.append("method") or ("{}", {}))
    monkeypatch.setattr(requests, "post_any", lambda *a, **k: taken.append("plain") or ("{}", {}))
    return taken


def call(ctx, watched, **extra):
    try:
        requests.call_model(base_url="http://endpoint", model="m", payload={"chapter_title": "第1章"},
                            schema={}, max_tokens=100, timeout=10, ctx=ctx, **extra)
    except Exception:  # the plain route needs more of the world than this test builds
        pass
    return watched


@pytest.mark.parametrize("story_method", ["", "shanyin", "leos"])
def test_an_authored_sheet_is_bound_whatever_method_the_book_names(story_method, watched):
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard = True
    ctx.story_method = story_method
    assert "method" not in call(ctx, watched, profile={"story_method": story_method})


@pytest.mark.parametrize("source", ["ctx", "profile"])
def test_without_an_authored_sheet_the_named_method_still_runs(source, watched):
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard = False
    ctx.story_method = "shanyin" if source == "ctx" else ""
    call(ctx, watched, profile={"story_method": "shanyin"} if source == "profile" else {})
    assert watched == ["method"]


def test_a_chapter_with_neither_goes_the_plain_route(watched):
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard = False
    ctx.story_method = ""
    assert "method" not in call(ctx, watched, profile={})
