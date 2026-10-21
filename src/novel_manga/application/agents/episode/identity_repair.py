"""Return an unfilmed identity rejection to the original planning repair budget."""
from argparse import Namespace

from novel_manga.application.planning.repair import repair_chapter
from novel_manga.application.production.flow import Batch
from novel_manga.episodes import chapter_of
from novel_manga.util import atomic_write_json, read_json


def repair(ctx, config, failure):
    from .inputs import _copy
    source = ctx.source_episode_dir
    # The original draft must still be the one being prepared; never overwrite
    # a newer local screenplay with an earlier planning candidate.
    if read_json(source/'chapter_script.json', {}) != read_json(ctx.episode_dir/'chapter_script.json', {}):
        return {'status': 'held', 'reason': 'Current screenplay differs from the saved planning candidate'}
    _copy(source/'planning_attempt.json', ctx.episode_dir/'planning_attempt.json')
    pending = read_json(ctx.episode_dir/'planning_attempt.json', {})
    if not isinstance(pending.get('candidate'), dict) or not isinstance(pending.get('outline'), dict):
        return {'status': 'held', 'reason': 'Existing planning candidate or outline missing'}
    args = Namespace(novel_dir=ctx.novel_dir, source=None, title=None, notes_json=None,
        workspace_index=None, resubmit_unconfirmed=False, unattended=False, review_only=False,
        tier=None, card_parallel=1, merge=1, grow_bible=False, replan=False, dry_run=False,
        max_redo=0, min_seconds=0, min_chapter_chars=300, repair_planning=True)
    batch = Batch(args)
    chapter = chapter_of(ctx.episode_dir.name)
    batch.rows = {chapter: {'plan': 'failed', 'repair_stage': 'identity', 'note': failure}}
    result = repair_chapter(batch, chapter, config=config, existing=True,
                            record_path=source/'planning-repair.json')
    atomic_write_json(ctx.run_dir/'planning-identity-repair.json', result)
    return result
