"""Review existing takes and record one shared episode execution result; never generate."""
from pathlib import Path

from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history, managed
from novel_manga.application.review.episode import review_episode
from novel_manga.application.review.store import current_takes
from novel_manga.util import atomic_write_json, read_json


def media_matches(plan, media, *, notes=None):
    """Whether the existing report can enter repair without first rendering the current plan."""
    return bool(media.get('clips')
                and media.get('clip_plan_fingerprint') == plan_fingerprint(plan)
                and (not media.get('prompt_h3_fingerprint')
                     or media['prompt_h3_fingerprint'] == h3_prompt_fingerprint(plan))
                and (notes is None or (media.get('review_feedback') or {}) == notes))


def review_saved(directory: Path, *, fresh=None, video_name='clip.mp4'):
    review = review_episode(directory, video_name=video_name, fresh=fresh)
    if video_name == 'clip.mp4':
        plan = read_json(directory / 'clip_plan.json', {})
        history.observe(directory, review, current_takes(directory, plan, review))
    return review


def save_result(directory, media, review, *, plan=None, rounds=(), corrections=(), blocked=None,
                write_media=True):
    """The same technical/content acceptance for a single run and a batch review."""
    plan = plan if plan is not None else read_json(directory / 'clip_plan.json', {})
    planned = {c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'}
    rows = review.get('clips') or {}
    missing = sorted(planned - rows.keys())
    errors = sorted(cid for cid, row in rows.items() if row.get('severity') == 'review_error')
    # Keep the established handling of historical unstamped media. A known stale
    # report, however, cannot certify a newly compiled plan by merely re-reading it.
    stale = ((media.get('clip_plan_fingerprint') is not None
              and media['clip_plan_fingerprint'] != plan_fingerprint(plan))
             or (bool(media.get('prompt_h3_fingerprint'))
                 and media['prompt_h3_fingerprint'] != h3_prompt_fingerprint(plan))
             or (bool(media) and (media.get('review_feedback') or {})
                 != read_json(directory / 'review_feedback.json', {})))
    accepted = (bool((media.get('assembly') or {}).get('thin_passed'))
                and not media.get('failed_clips') and not media.get('gate_failed_clips')
                and not review.get('feedback') and not missing and not errors and not stale)
    result = {'passed': bool(accepted), 'repair_rounds': list(rounds),
              'automatic_corrections': sorted(set(corrections)), 'blocked': blocked or {},
              'missing_reviews': missing, 'review_errors': errors,
              'remaining': sorted(review.get('feedback') or {}),
              'generated_counts': managed.generated_counts(history.load(directory))}
    if stale:
        result['reason'] = 'media predates the current plan, H3 request or corrections'
    media['quality_review'] = result
    if write_media:
        atomic_write_json(directory / 'thin_media_report.json', media)
    atomic_write_json(directory / 'episode_execution.json', result)
    return media


def review_only(directory: Path, *, fresh=None, video_name='clip.mp4'):
    """Audit material already on disk; no renderer, preparation, trial or retake is started."""
    if video_name == 'clip.mp4':
        history.adopt_reviewed_run(directory)
    review = review_saved(directory, fresh=fresh, video_name=video_name)
    if video_name == 'clip.mp4':
        path = directory / 'thin_media_report.json'
        media = read_json(path, {})
        previous = media.get('quality_review') or {}
        save_result(directory, media, review, rounds=previous.get('repair_rounds', []),
                    corrections=previous.get('automatic_corrections', []),
                    blocked=previous.get('blocked', {}), write_media=path.is_file())
    return review
