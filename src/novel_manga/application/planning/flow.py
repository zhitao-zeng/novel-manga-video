"""Coordinate chapter inputs, bounded planning attempts and existing publication."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from .source_inputs import prepare_source
from .cast_inputs import prepare_cast
from .request_inputs import prepare_request
from .attempts import plan_attempts
from .publication import publish_result


def run(args, ctx: PlannerContext) -> int:
    source = prepare_source(args, ctx)
    cast = prepare_cast(args, ctx, source)
    inputs = prepare_request(args, ctx, source, cast)
    if inputs is None:
        return 0
    outcome = plan_attempts(args, ctx, inputs)
    return publish_result(args, ctx, inputs, outcome)
