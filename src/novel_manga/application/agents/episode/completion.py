"""EpisodeCompletion: existing episode operations with explicit service dependencies."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from novel_manga.util import atomic_write_json, read_json


from .tool_state import record_state
from novel_manga.application.production import episode_state


class EpisodeCompletion:
    def __init__(self, ctx, media, evidence):
        self.ctx = ctx
        self.media = media
        self.evidence = evidence

    def finish(self, payload):
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            raise ValueError('first pass hands completed video to the review queue; finish belongs to that queue')
        from novel_manga.application.repair import history
        from novel_manga.application.review.execution import save_result, media_matches
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        rounds = read_json(self.ctx.run_dir / 'tool-repair-decisions.json', [])
        unreviewed = self.evidence.unreviewed_clips(report, review)
        if unreviewed:
            return {'ok': False, 'error': 'missing_review', 'required_action': 'review',
                    'clips': unreviewed, 'message': 'Generated takes need independent review before finishing.'}
        from novel_manga.review.contracts import REVIEW_POLICY
        if review.get('clips') and not self.evidence.current_review(review):
            return {'ok': False, 'error': 'stale_review', 'required_action': 'review',
                    'message': 'Run review with the current policy and configured mode before finishing existing video.',
                    'review_policy': review.get('policy'), 'expected_review_policy': REVIEW_POLICY}
        if report.get('status') == 'clips_ready' or (report.get('assembly')or{}).get('needs_subtitle_refresh'):
            if not media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
                return {'ok': False, 'error': 'stale_media', 'required_action': 'render',
                        'message': 'Current requests need render before existing takes can be assembled.'}
            report = self.media.assemble(report)
        report = self.media.refresh_technical_policy(report, plan)
        report = self.media.review_final(report)
        review = read_json(self.ctx.episode_dir/'episode_review.json', review)
        rounds = rounds[read_json(self.ctx.run_dir / 'prepared.json', {}).get('repair_round_start', 0):]
        blocked = {}
        for row in rounds:
            for cid in (row.get('changed', []) + row.get('accepted', [])
                        + row.get('input_repaired', []) + row.get('risk_accepted', [])):
                blocked.pop(cid, None)
            # Older batches required a proposal for every failed clip. An
            # unsubmitted operation is pending work, not independent defect evidence.
            blocked.update({cid:reason for cid,reason in (row.get('blocked')or{}).items()
                            if reason!='agent omitted a decision'})
        report = save_result(self.ctx.episode_dir, report, review, plan=plan, rounds=rounds,
            corrections=[cid for row in rounds for cid in row.get('changed', [])], blocked=blocked)
        quality = report.get('quality_review') or {}
        episode_state.record(self.ctx.run_dir, 'acceptance_saved',
                             result='passed' if quality.get('passed') else 'needs_review',
                             passed=quality.get('passed') is True, remaining=quality.get('remaining', []))
        if (review.get('review_mode') == 'verify'
                and (report.get('quality_review') or {}).get('passed') is True
                and (report.get('assembly') or {}).get('pending_publish')):
            from novel_manga.application.repair.delivery import publish_if_ready, precise_review_blockers
            from novel_manga.application.review.store import current_takes
            from novel_manga.application.production.runs import episode_status
            takes = current_takes(self.ctx.episode_dir, plan, review)
            if not publish_if_ready(self.ctx.episode_dir, review, takes):
                expected = [c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video']
                unresolved = precise_review_blockers(review, takes, expected)
                return {'ok': False, 'passed': False, 'error': 'publication_gate',
                        'required_action': 'apply', 'clips': unresolved,
                        'publication_status': episode_status(self.ctx.episode_dir, True),
                        'message': 'The existing publication gate has not passed. Inspect current source/video evidence '
                                   'and use a bounded keep check or repair; a source exception alone does not clear '
                                   'an unresolved precise verdict.'}
            report = read_json(self.ctx.episode_dir / 'thin_media_report.json')
            episode_state.record(self.ctx.run_dir, 'published', result='published',
                                 video=(report.get('assembly') or {}).get('final_video'))
        atomic_write_json(self.ctx.run_dir / 'result.json', report)
        final = self.ctx.run_dir / 'final-candidate'
        final.mkdir(exist_ok=True)
        movie = (report.get('assembly') or {}).get('final_video')
        copied_movie = None
        if movie and Path(movie).is_file():
            copied_movie = final / f'{self.ctx.episode_dir.name}-episode-agent.mp4'
            shutil.copy2(movie, copied_movie)
        for path in (self.ctx.run_dir / 'result.json', self.ctx.episode_dir / 'chapter_script.json',
                     self.ctx.episode_dir / 'clip_plan.json', self.ctx.episode_dir / 'episode_review.json',
                     self.ctx.episode_dir / 'episode_execution.json'):
            if path.is_file():
                shutil.copy2(path, final / path.name)
        for source in self.ctx.episode_dir.rglob('request.json'):
            target = final / 'requests' / source.relative_to(self.ctx.episode_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        voices = {path.parent.name: read_json(path, {}).get('generated_voice_takes', 0)
                  for path in (self.ctx.episode_dir / 'work/inner_voice').glob('*/voice.json')}
        quality = report.get('quality_review') or {}
        author_session = read_json(self.ctx.work_dir / 'episode-session.json', {})
        manifest = {'method': ('one persistent sandbox agent owns authoring and host tool calls' if author_session
                              else 'existing requests executed by host with independent acceptance'),
            'passed': quality.get('passed') is True, 'quality_review': quality,
            'technical_passed': (report.get('assembly') or {}).get('thin_passed') is True,
            'actual_video_counts': history.generation_counts(self.ctx.episode_dir),
            'voice_recording_counts': voices, 'voice_recording_blocked': self.evidence.voice_recording_blocks(plan),
            'author_session': author_session,
            'movie': str(copied_movie) if copied_movie else None, 'frozen': read_json(self.ctx.run_dir / 'frozen.json')}
        atomic_write_json(final / 'manifest.json', manifest)
        atomic_write_json(self.ctx.work_dir / 'input/latest-finish.json', manifest)
        record_state(self.ctx, 'finished' if manifest['passed'] else 'finished_with_issues', movie=manifest['movie'])
        return {'ok': True, 'passed': manifest['passed'],
                'quality_review': {key: quality.get(key) for key in
                                  ('passed', 'missing_reviews', 'review_errors', 'remaining', 'reason')},
                'technical_passed': manifest['technical_passed'], 'movie': manifest['movie'],
                'artifact': 'input/latest-finish.json', 'manifest': str(final / 'manifest.json'),
                'generation_counts': manifest['actual_video_counts']}
