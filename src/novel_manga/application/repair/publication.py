"""Flow-owned writes for validated repair candidates. No route selection or model calls."""
from __future__ import annotations
import json
import fcntl
from pathlib import Path
from novel_manga.util import atomic_write_json
from novel_manga.repair.proposal import RepairProposal
import novel_manga.util as utils
import novel_manga.application.repair.history as history


def publish_rewrite(episode_dir: Path, proposal: RepairProposal, *, use_history=True, identity=False, reframe=False):
    if not proposal.available or not proposal.changed:
        return
    script, new_plan, new_notes, changes = proposal.script, proposal.plan, proposal.notes, proposal.changes
    changed = proposal.changed
    appearance_checks = proposal.result.get('appearance_checks', {})
    old_notes = utils.read_json(episode_dir / 'review_feedback.json', {})
    if use_history:
        from novel_manga.application.repair.history import begin_trial
        begin_trial(episode_dir, set(changed), "identity" if identity else "reframe" if reframe else "rewrite", after_plan=new_plan, after_notes=new_notes, changes=changes)
    for name in ("chapter_script.json", "clip_plan.json"):
        if identity:
            before_identity = episode_dir / f'{name}.bak-source-identities'
            if not before_identity.exists():
                before_identity.write_bytes((episode_dir / name).read_bytes())
        backup = episode_dir / f"{name}.bak-repair-0914"
        if not backup.is_file():
            backup.write_text((episode_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
    write_artifacts(episode_dir, proposal, atomic_script=False, appearance_checks=appearance_checks,
                    write_notes=new_notes != old_notes)


def publish_candidate(directory, proposal, method, clip_ids, *, changes=None):
    history.begin_trial(directory, set(clip_ids), method, after_plan=proposal.plan,
                        after_notes=proposal.notes, changes=proposal.changes if changes is None else changes)
    write_artifacts(directory, proposal)


def write_artifacts(directory: Path, proposal: RepairProposal, *, atomic_script=True,
                    appearance_checks=None, write_notes=True):
    """Write already accepted artifacts in the established order, without charging a generation."""
    if atomic_script:
        atomic_write_json(directory / 'chapter_script.json', proposal.script)
    else:
        (directory / 'chapter_script.json').write_text(json.dumps(proposal.script, ensure_ascii=False, indent=1), encoding='utf-8')
    atomic_write_json(directory / 'clip_plan.json', proposal.plan)
    if appearance_checks:
        atomic_write_json(directory / 'repair_appearance_checks.json', appearance_checks)
    if write_notes:
        atomic_write_json(directory / 'review_feedback.json', proposal.notes)


def publish_preparation(directory: Path, proposal: RepairProposal):
    """Pre-render preparation has no repair trial or rendering budget debit."""
    write_artifacts(directory, proposal)


def publish_retake(directory, proposal):
    history.begin_trial(directory, set(proposal.changed), 'generation_retry',
                        after_plan=proposal.plan, changes=proposal.changes)
    atomic_write_json(directory / 'clip_plan.json', proposal.plan)


def publish_source_review(directory, proposal, checked, acceptances, records, report):
    publish_candidate(directory, proposal, 'source_recheck', checked)
    atomic_write_json(directory / 'source_acceptances.json', acceptances)
    state = directory.parent / 'repair_manager'
    with (state / 'verified.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush()
    atomic_write_json(directory / 'source_recheck_report.json', report)


def record_speaker_evidence(path, facts):
    atomic_write_json(path, facts)
