"""Execute one generation or an explicitly chosen refusal correction."""
from novel_manga.media.retries import GenerationOutcome, Recovery, RetryState
from novel_manga.media.common import log


def generate_attempt(runner, clip, attempt) -> GenerationOutcome:
    try:
        return GenerationOutcome(video=runner.generate_clip(clip, attempt))
    except RuntimeError as error:
        return GenerationOutcome(error=error)


def apply_recovery(runner, clip, state: RetryState, step: Recovery) -> bool:
    if step.action == 'reference':
        state.privacy_repairs += 1
        index = step.reference_slot
        repaired = runner.repair_rejected_reference(clip, int(index) - 1) if index else []
        if not repaired and state.privacy_repairs == 1:
            repaired = runner.repair_privacy_cards(clip)
        log(f"{clip['clip_id']}: reference rejected as a real person (image {index or '?'}); fixed {repaired or 'nothing'}; retrying")
        return bool(repaired)
    if step.action == 'soften':
        clip['_softened'] = True
        log(f"{clip['clip_id']}: prompt text refused by input moderation; retrying once with softened wording")
        return True
    if step.action == 'rewrite':
        clip['_repaired'] = True
        log(f"{clip['clip_id']}: still refused after softening; repairing the wording against the filter")
        return bool(runner.repair_refused_prompt(clip, state.attempt))
    if step.action == 'compliance':
        clip['_compliance'] = True
        log(f"{clip['clip_id']}: generated video rejected by output moderation; retrying once with a compliance line")
        return True
    raise ValueError(f'unknown recovery action: {step.action}')
