"""Select an existing single-episode operation for batch work; no repair policy here."""
import os
import sys

from novel_manga.application.production import common
from novel_manga.application.review.execution import media_matches, review_only
from novel_manga.util import read_json


def only_review(batch):
    return bool(getattr(batch.args, 'review_only', False) or getattr(batch.args, 'no_render', False))


def reviewed_render(batch):
    return bool(getattr(batch.args, 'unattended', False) and not only_review(batch))


def render_command(batch, chapter, *, repair_existing=False):
    directory = batch.episode_dir(chapter)
    args = batch.args
    command = [sys.executable, str(common.SCRIPTS / 'render_clips_thin.py'),
               '--novel-dir', str(batch.novel_dir), '--episode', directory.name,
               '--workers', str(args.workers), '--inflight', str(args.inflight)]
    if args.tier:
        command += ['--tier', args.tier]
    if args.prescreen:
        command.append('--prescreen')
    if not args.moderation_repair:
        command.append('--no-moderation-repair')
    if args.cache_only:
        command.append('--cache-only')
    if reviewed_render(batch):
        command.append('--repair' if repair_existing else '--review')
    return command


def collect_review(batch, chapter):
    """Read what the shared operation wrote, without judging the same takes a second time."""
    directory = batch.episode_dir(chapter)
    review = read_json(directory / 'episode_review.json', {})
    row = batch.rows[chapter]
    row['review_flags'] = review.get('flags', [])
    execution = read_json(directory / 'episode_execution.json', {})
    if execution:
        row['quality_review'] = execution
        row['auto_fixed'] = sorted(set(row.get('auto_fixed', []))
                                   | set(execution.get('automatic_corrections', [])))
        if not execution.get('passed'):
            details = []
            for key, label in (('remaining', '待修'), ('missing_reviews', '待审'), ('review_errors', '审查异常')):
                if execution.get(key):
                    details.append(label + '：' + ', '.join(execution[key]))
            if execution.get('reason'):
                details.append(execution['reason'])
            row['note'] = '；'.join(details) or row.get('note') or '尚未通过当前执行检查'
    if row['review_flags']:
        common.log(f'ch{chapter}: {len(row["review_flags"])} clip flag(s) remain: {row["review_flags"][0][:120]}')


def review_existing(batch, chapter):
    """Review-only ends here; unattended repair delegates once to the single-episode runner."""
    directory = batch.episode_dir(chapter)
    row = batch.rows[chapter]
    if batch.args.dry_run:
        row['review'] = 'would review and repair' if reviewed_render(batch) else 'would review'
        return
    if not reviewed_render(batch) or batch.args.cache_only:
        review_only(directory)
        collect_review(batch, chapter)
        return
    lock = directory / '.render.lock'
    if lock.is_file():
        try:
            pid = int(lock.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and common.pid_alive(pid):
            row['note'] = f'locked by pid {pid}'
            return
    lock.write_text(str(os.getpid()), encoding='utf-8')
    try:
        plan = read_json(directory / 'clip_plan.json', {})
        media = read_json(directory / 'thin_media_report.json', {})
        command = render_command(batch, chapter, repair_existing=media_matches(
            plan, media, notes=read_json(directory / 'review_feedback.json', {})))
        code, problem = batch.run(command, directory / 'render.log')
        row['render'] = batch.render_status(chapter)
        batch.fill_result(chapter)
        collect_review(batch, chapter)
        if code and problem:
            row['note'] = problem
    finally:
        lock.unlink(missing_ok=True)
