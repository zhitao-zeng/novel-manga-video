"""Resolve the original episode directory for finite recovery entries."""
from pathlib import Path


def context(row,novel_dir):
    from novel_manga.application.agents.episode.context import create_context
    run=Path(row['run_dir'])
    ctx=create_context(novel_dir,row['episode'],output_root=run.parents[1],input_mode=row['input_mode'])
    if ctx.run_dir.resolve()!=run.resolve():raise ValueError('recovery must retain the original run directory')
    return ctx
