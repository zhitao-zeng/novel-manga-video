"""Existing media and draft archives shared by production and episode tools."""
from pathlib import Path
import shutil
from novel_manga.util import read_json


def media_started(episode_dir):
    from novel_manga.application.repair import history
    if any(history.generation_counts(episode_dir).values()):
        return True
    clips = episode_dir / 'work/clips'
    return (any(p.stat().st_size > 0 for p in clips.glob('*/attempt_*/clip.mp4'))
            or any(read_json(p, {}).get('task_id') for p in clips.glob('*/attempt_*/clip.mp4.task.json')))


def archive_adoption(episode_dir, run_dir):
    root = run_dir / 'adoption-history'
    directory = root / f'{sum(p.is_dir() for p in root.glob("*")) + 1:03d}'
    directory.mkdir(parents=True)
    for path in (episode_dir / 'chapter_script.json', episode_dir / 'clip_plan.json',
                 episode_dir / 'episode_review.json', episode_dir / 'review_feedback.json',
                 episode_dir / 'thin_media_report.json', episode_dir / 'episode_execution.json',
                 run_dir / 'prepared.json', run_dir / 'adopted-episode.json',
                 run_dir / 'adopted-requests.json',
                 run_dir / 'tool-repair-decisions.json', run_dir / 'result.json'):
        if path.is_file():
            shutil.copy2(path, directory / path.name)
    if (run_dir / 'final-candidate').is_dir():
        shutil.copytree(run_dir / 'final-candidate', directory / 'final-candidate')
    return directory
