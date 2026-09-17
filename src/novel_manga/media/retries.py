"""Clip retry decisions; no generation, asset repair, publication or file writes."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from .policy import PRIVACY_MARKER
from . import policy
from .cache import CacheMiss


@dataclass
class RetryState:
    attempt: int
    limit: int
    privacy_repairs: int = 0


@dataclass(frozen=True)
class GenerationOutcome:
    video: Path | None = None
    error: RuntimeError | None = None


@dataclass(frozen=True)
class Recovery:
    action: str
    reference_slot: str | None = None


def recovery_steps(error: RuntimeError, clip: dict, state: RetryState, *, moderation_repair: bool) -> list[Recovery]:
    text = str(error)
    steps = []
    if PRIVACY_MARKER in text and state.privacy_repairs < 2:
        index = re.search(r'content\[(\d+)\]', text)
        steps.append(Recovery('reference', index.group(1) if index else None))
    if policy.INPUT_TEXT_MARKER in text and not clip.get('_softened'):
        steps.append(Recovery('soften'))
    if policy.INPUT_TEXT_MARKER in text and moderation_repair and not clip.get('_repaired'):
        steps.append(Recovery('rewrite'))
    if any(marker in text for marker in policy.OUTPUT_MODERATION_MARKERS) and not clip.get('_compliance'):
        steps.append(Recovery('compliance'))
    return steps


@dataclass(frozen=True)
class NextAttempt:
    action: str
    attempt: int
    limit: int


def after_analysis(state: RetryState, analysis: dict, *, generated: bool, free_retries: bool,
                   next_cached: bool | None = None) -> NextAttempt:
    if analysis['passed']:
        return NextAttempt('stop', state.attempt, state.limit)
    limit = state.limit
    if not generated and limit < policy.MAX_ATTEMPTS_FREE:
        if not free_retries and next_cached is None:
            return NextAttempt('check_cache', state.attempt, limit)
        if free_retries or next_cached:
            limit += 1
    attempt = state.attempt + 1
    return NextAttempt('continue' if attempt <= limit else 'stop', attempt, limit)


def selected_take(attempts):
    return next((row for row in attempts if row['passed']), attempts[-1])


def failure_result(clip_id, attempts, error):
    message = f'{type(error).__name__}: {str(error)[:600]}'
    if attempts and not isinstance(error, CacheMiss):
        return {'clip_id': clip_id, 'attempts': attempts, 'retake_error': message, 'selected': selected_take(attempts)}
    return {'clip_id': clip_id, 'attempts': attempts, 'selected': attempts[-1] if attempts else None, 'error': message}
