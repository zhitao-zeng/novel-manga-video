"""Argument errors shared by pure request formatting and host repair operations."""


class RepairArgumentError(ValueError):
    """The proposal cannot be executed in its supplied argument shape."""


class UnsupportedInput(ValueError):
    """A source decision was rejected, rather than interrupted by a service."""


class CompileConflict(ValueError):
    def __init__(self,message,details):
        super().__init__(message)
        self.details=details


def validate_resume(row):
    if row.get('resume') is True:
        changes=set(row)-{'clip_id','action','resume','reason'}
        if changes:
            raise RepairArgumentError('resume:true executes the saved decision unchanged; '
                f'remove new fields {sorted(changes)} to resume, or omit resume to submit a changed proposal '
                'within the remaining revision allowance')


def first_pass_scope(ctx,clip_ids):
    import os
    if os.environ.get('NOVEL_FIRST_PASS_ONLY')!='1':return None
    from novel_manga.application.preparation.store import unfilmed_video_clips
    pending=unfilmed_video_clips(ctx.episode_dir)
    rejected=set(clip_ids)-pending
    if rejected:
        raise RepairArgumentError('first pass only repairs inputs for clips without video; '
            f'rejected clips: {sorted(rejected)}; allowed clips: {sorted(pending)}')
    return pending


def spent_video_budget(ctx,clip_ids):
    from novel_manga.application.repair import history
    counts=history.generation_counts(ctx.episode_dir)
    return {cid:'effective clip retry budget used' for cid in clip_ids
            if history.remaining_takes(ctx.episode_dir,cid,preparation=True)<=0}
