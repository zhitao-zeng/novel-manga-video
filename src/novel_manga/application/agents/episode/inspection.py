"""EpisodeInspection: existing episode operations with explicit service dependencies."""
from __future__ import annotations

import os

from novel_manga.util import read_json
from novel_manga.application.production.artifacts import media_started


from .tool_inputs import draft_file


class EpisodeInspection:
    def __init__(self, ctx, drafts, evidence):
        self.ctx = ctx
        self.drafts = drafts
        self.evidence = evidence

    def status(self, payload):
        from collections import Counter
        from novel_manga.application.repair import history
        from .input_repair import repair_status, remaining_input_attempts, SHOT_FIELDS
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        review = read_json(self.ctx.episode_dir/'episode_review.json', {})
        media = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        unreviewed = self.evidence.unreviewed_clips(media, review)
        counts = history.generation_counts(self.ctx.episode_dir)
        clip_ids = [c['clip_id'] for c in plan.get('clips', [])]
        rechecks = Counter(cid for row in read_json(self.ctx.run_dir/'tool-repair-decisions.json', [])
                           for cid in row.get('keep_checks', []))
        limits = {cid: history.recheck_limit(self.ctx.episode_dir, cid) for cid in clip_ids}
        extra = {cid: history.conditional_extra(self.ctx.episode_dir, cid) for cid in clip_ids}
        input_left = remaining_input_attempts(self.ctx, clip_ids)
        input_states = repair_status(self.ctx)
        from .input_records import input_operations, input_records
        records = input_records(self.ctx)
        input_ops = {cid: input_states.get(cid, {}).get('operations')
                     or input_operations(self.ctx, cid, records=records) for cid in clip_ids}
        take_left = {cid: history.remaining_takes(self.ctx.episode_dir,cid,preparation=True)
                     for cid in clip_ids}
        check_left = {cid: max(0, int(limit)-rechecks[cid]) for cid, limit in limits.items() if limit is not None}
        eligible = (set(review.get('feedback') or {}) | set(media.get('failed_clips') or [])
                    | set(media.get('gate_failed_clips') or []) | set(media.get('blocked_clips') or {})
                    | set((media.get('quality_review') or {}).get('remaining') or []))
        publication = []
        if (review.get('review_mode') == 'verify' and self.evidence.current_review(review)
                and (media.get('assembly') or {}).get('pending_publish')):
            from novel_manga.application.repair.delivery import precise_review_blockers
            from novel_manga.application.review.store import current_takes
            publication = precise_review_blockers(review, current_takes(self.ctx.episode_dir, plan, review),
                [c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'])
            eligible.update(publication)
        if self.evidence.clip_technical_issues(media):
            eligible.update(c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video')
        videos = {r['clip_id'] for r in media.get('clips', []) if (r.get('selected') or {}).get('video')}
        voice_blocks = self.evidence.voice_recording_blocks(plan)
        actions = {}
        risk_choices={}
        from novel_manga.application.preparation.readiness import render_risk_choices
        unfilmed = self.evidence.unfilmed_clips(plan, media)
        for clip in plan.get('clips', []):
            cid = clip['clip_id']; allowed = []
            if cid in eligible or cid in unfilmed:
                if any(r.get('role') in {'character','location'} for r in clip.get('references',[])):
                    allowed.append('repair_assets')
                if float(clip.get('request_seconds')or 0)>self.ctx.clip_seconds_max:
                    allowed.append('repack')
            if cid in eligible:
                choices=render_risk_choices(self.ctx.episode_dir,clip)
                if take_left[cid] and any(choices.values()):
                    risk_choices[cid]=choices
                    allowed.append('accept_risk')
                if take_left[cid] and cid not in voice_blocks:
                    allowed.append('edit')
                    if clip.get('prompt_h3'):
                        allowed.append('retake')
                if cid in videos and (limits[cid] is None or check_left[cid]):
                    allowed.append('keep')
            if (cid in eligible or cid in unfilmed) and (take_left[cid] or input_ops[cid].get('recovery',{}).get('allowed')) and (
                    any(operation['allowed'] for operation in input_ops[cid].values())):
                allowed.append('input')
            actions[cid] = allowed
        from novel_manga.application.agents.episode.pipeline_preparation import semantic_pending
        editorial = semantic_pending(self.ctx) and not media_started(self.ctx.episode_dir)
        if editorial:
            for cid in eligible:
                if cid in actions:
                    actions[cid].append('recheck_input')
                    if not (self.ctx.run_dir/'pipeline-replan-request.json').exists():
                        actions[cid].append('replan')
        first_pending = None
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            from novel_manga.application.preparation.store import unfilmed_video_clips
            first_pending = unfilmed_video_clips(self.ctx.episode_dir, plan)
            actions = {cid: operations if cid in first_pending else [] for cid, operations in actions.items()}
        manifest=read_json(self.ctx.work_dir/'input/manifest.json',{})
        scoped_editor=(manifest.get('production_mode') in {'pipeline_review','batch_review'}
            and manifest.get('agent_phase') in {'input_repair','review'}
            and (manifest.get('production_mode')=='pipeline_review' or bool(plan.get('clips'))))
        return {'ok': True, 'execution': read_json(self.ctx.run_dir / 'execution.json', {}),
                **({'first_pass_remaining': sorted(first_pending)} if first_pending is not None else {}),
                'repair_capabilities': {'adopted_input': {
                    'characters': 'missing source actor cards', 'shot_fields': sorted(SHOT_FIELDS),
                    'phases': 'existing character appearance or registered phase reuse',
                    'locations': 'source-supported empty scene cards'}, 'source_review_required': True},
                'adopted': bool(plan.get('clips')), 'clip_count': len(plan.get('clips', [])),
                'can_adopt_draft': not media_started(self.ctx.episode_dir) and not scoped_editor,
                'review_required': bool(unreviewed) or (bool(review.get('clips')) and not self.evidence.current_review(review)),
                'unreviewed_clips': unreviewed,
                'review_command': 'review before finish when current video reviews are missing or stale',
                'retry_policy': {'scope': 'current per-clip permissions include approved conditional extras',
                    'instruction': 'Use repair_actions to submit apply now. A pending extra is permission to prepare '
                                   'an edit/input/retake; the host activates generation after validation and input preflight. '
                                   'Do not infer a hard limit from historical counts or previous stop messages.'},
                'repair_actions': actions, 'upstream_actions':{cid:[a for a in ops if a in {'repair_assets','repack'}]for cid,ops in actions.items()},
                'risk_choices':{cid:v for cid,v in risk_choices.items() if 'accept_risk' in actions[cid]},
                'publication_blockers': publication,
                'remaining_input_attempts': input_left,
                'remaining_rechecks': check_left,
                'conditional_extra_takes': {cid: {'limit': int(row['limit']),
                    'remaining': max(0, int(row['limit'])-counts.get(cid, 0)),
                    'prepared': bool(row.get('prepared')), 'activated': bool(row.get('activated')),
                    'next': 'Review current evidence and submit a valid edit/input/retake; generation activates only after final input preflight.'}
                    for cid, row in extra.items() if row},
                'input_repairs': input_states, 'input_operations': input_ops,
                'adopt_draft_command': ('Use scoped input repair or an explicitly offered replan operation.' if scoped_editor else
                    f'adopt {draft_file(self.ctx)} to prepare inputs without video; existing history is retained'),
                'generation_counts': counts,
                'voice_recording_blocked': voice_blocks,
                'remaining_takes': take_left,
                'media': self.evidence.media_summary(media)}
