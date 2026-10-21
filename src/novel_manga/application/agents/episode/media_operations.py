"""MediaOperations: existing episode operations with explicit service dependencies."""
from __future__ import annotations

import os
from pathlib import Path
import time

from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.production.artifacts import media_started


from .tool_state import record_state
from novel_manga.application.production import episode_state
from novel_manga.media.results import has_analysis


class MediaOperations:
    def __init__(self, ctx, drafts, evidence):
        self.ctx = ctx
        self.drafts = drafts
        self.evidence = evidence
        self.begin_generation = None

    def runner(self):
        from novel_manga.application.rendering.flow import ThinMediaRunner
        from novel_manga.config import Settings
        from novel_manga.models.bible import StoryBible
        workers = int(os.environ.get('NOVEL_RENDER_WORKERS', '2'))
        inflight = int(os.environ.get('NOVEL_RENDER_INFLIGHT', '0'))
        if workers < 0 or inflight < 0:
            raise ValueError('NOVEL_RENDER_WORKERS and NOVEL_RENDER_INFLIGHT must be nonnegative')
        if os.environ.get('NOVEL_AGENT_MANAGED') == '1' and inflight <= 0:
            raise ValueError('managed episode rendering requires a positive NOVEL_RENDER_INFLIGHT')
        runner = ThinMediaRunner(novel_dir=self.ctx.novel_dir, episode_dir=self.ctx.episode_dir,
            settings=Settings.from_env(provider='phanrouter', output_root=self.ctx.run_dir,
                                       admission_mode='preview'),
            bible=StoryBible.model_validate(read_json(self.ctx.novel_dir / 'story_bible.json')),
            workers=workers, inflight=inflight, max_attempts=1,
            profile=read_json(self.ctx.novel_dir / 'profile.json'))
        runner.context.asr_helper = self.ctx.project_root / 'scripts/thin_asr_segments.py'
        runner.context.source_novel_dir = getattr(self.ctx, 'source_novel_dir', None) or self.ctx.novel_dir
        runner.context.options.first_pass_only = os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1'
        if self.begin_generation is not None:
            probe = runner.context.provider.before_video_submit
            def before_submit():
                probe()
                self.begin_generation()
            runner.context.provider.before_video_submit = before_submit
        return runner


    def hydrate_analysis(self, report, runner=None):
        """Restore current first-pass audio evidence before review or assembly."""
        from novel_manga.application.rendering.reviewed import _load_current
        from novel_manga.application.review.execution import media_matches
        plan = read_json(self.ctx.episode_dir/'clip_plan.json')
        def needs_analysis(selected):
            video = selected.get('postmix_video') or selected.get('video')
            return bool(video and (not has_analysis(selected) or not (Path(video).parent/'native.wav').is_file()))
        if not (any(needs_analysis(row.get('selected') or {}) for row in report.get('clips', []))
                and media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {}))):
            return report
        if runner is None:
            runner = self.runner()
            _load_current(runner)
        clips = {clip['clip_id']: clip for clip in plan['clips']}
        hydrated = False
        for row in report['clips']:
            selected = row.get('selected')
            if not selected or not needs_analysis(selected):
                continue
            # First-pass reports can retain only video/passed. The ordinary
            # analyser reads ASR beside this selected take and refreshes it for
            # the current dialogue; another take's cached path is never used.
            cached_only = runner.context.options.cache_only
            black_checks = runner.context.state.black_checks
            runner.context.options.cache_only = True
            runner.context.state.black_checks = set()
            try:
                analysis = runner.analyse_clip(clips[row['clip_id']], Path(selected['video']))
            finally:
                runner.context.options.cache_only = cached_only
                runner.context.state.black_checks = black_checks
            row['selected'] = {**selected, **analysis}
            failed = report.setdefault('gate_failed_clips', [])
            if analysis.get('passed') is False:
                if row['clip_id'] not in failed:
                    failed.append(row['clip_id'])
            elif analysis.get('passed') is True:
                report['gate_failed_clips'] = [cid for cid in failed if cid != row['clip_id']]
            hydrated = True
        if hydrated:
            # Keep restored evidence even if a later encoding/QC step fails.
            atomic_write_json(self.ctx.episode_dir/'thin_media_report.json', report)
        return report


    def assemble(self, report, runner=None):
        """Encode current selected takes and run final QC; never generate or replan."""
        from novel_manga.application.rendering.reviewed import _load_current
        from novel_manga.application.review.execution import media_matches
        from novel_manga.application.repair import history
        plan = read_json(self.ctx.episode_dir/'clip_plan.json')
        if not media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
            raise ValueError('current requests need render before existing takes can be assembled')
        if runner is None:
            runner = self.runner()
            _load_current(runner)
        report = self.hydrate_analysis(report, runner)
        record_state(self.ctx, 'assembling', owner='episode_agent_tools')
        started = time.monotonic()
        assembly = runner.assemble(report['clips'])
        seconds = round(time.monotonic()-started, 3)
        report.update(status='assembled', assembly=assembly,
                      elapsed_seconds=round(float(report.get('elapsed_seconds') or 0)+seconds, 3))
        report.setdefault('timing', {})['assembly_s'] = seconds
        atomic_write_json(self.ctx.episode_dir/'thin_media_report.json', report)
        history.record_render(self.ctx.episode_dir, report)
        return self.review_final(report, runner)


    def review_final(self, report, runner=None):
        """Review an assembled movie and publish its feedback without generating a take."""
        if not (report.get('assembly') or {}).get('final_video'):
            return report
        from novel_manga.application.rendering.reviewed import _load_current
        from novel_manga.application.review import final_cut
        from novel_manga.application.review.execution import save_result
        from novel_manga.application.review.execution import media_matches
        from novel_manga.application.review.movie_context import assembly_current, assembly_inputs
        if not assembly_current(self.ctx.episode_dir, report):
            plan = read_json(self.ctx.episode_dir/'clip_plan.json', {})
            inputs = assembly_inputs(plan, report)
            if (inputs['takes'] and all(take for cid, take in inputs['takes'])
                    and media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {}))):
                return self.assemble(report, runner)
            # An old movie is not evidence about missing/replaced current takes.
            review = final_cut.apply_feedback(self.ctx.episode_dir,
                read_json(self.ctx.episode_dir/'episode_review.json', {}), None)
            atomic_write_json(self.ctx.episode_dir/'episode_review.json', review)
            prior = report.get('quality_review') or {}
            return save_result(self.ctx.episode_dir, report, review,
                rounds=prior.get('repair_rounds', []), corrections=prior.get('automatic_corrections', []),
                blocked=prior.get('blocked', {}))
        if not final_cut.required(self.ctx.episode_dir):
            review = final_cut.apply_feedback(self.ctx.episode_dir,
                read_json(self.ctx.episode_dir/'episode_review.json', {}), None)
            prior = report.get('quality_review') or {}
            return save_result(self.ctx.episode_dir, report, review,
                rounds=prior.get('repair_rounds', []), corrections=prior.get('automatic_corrections', []),
                blocked=prior.get('blocked', {}))
        final = report.get('final_cut_review') or read_json(self.ctx.episode_dir/'final_cut_review.json', None)
        if not (final_cut.current(self.ctx.episode_dir,report,final) and final.get('complete') is True):
            if runner is None:
                runner = self.runner()
                _load_current(runner)
            final = final_cut.review_assembly(runner, report)
        if final is not None:
            report['final_cut_review'] = final
            review = final_cut.apply_feedback(self.ctx.episode_dir,
                read_json(self.ctx.episode_dir/'episode_review.json', {}), final)
            atomic_write_json(self.ctx.episode_dir/'episode_review.json', review)
            prior = report.get('quality_review') or {}
            report = save_result(self.ctx.episode_dir, report, review,
                rounds=prior.get('repair_rounds', []), corrections=prior.get('automatic_corrections', []),
                blocked=prior.get('blocked', {}))
        return report


    def refresh_technical_policy(self, report, plan):
        """Recheck the same movie when its book's QC waivers change; never render."""
        from novel_manga.application.profiles import frame_spec, load_profile, media_qc_ignores, MAX_HOLD_SECONDS
        from novel_manga.application.review.execution import media_matches
        from novel_manga.config import Settings
        from novel_manga.media.quality import apply_check_policy, inspect_media, black_frame_check
        assembly = report.get('assembly') or {}
        checks = (assembly.get('media_qc') or {}).get('checks') or {}
        ignored = set(media_qc_ignores(self.ctx.novel_dir, self.ctx.episode_dir))
        ignored.update(value.strip() for value in os.environ.get('NOVEL_QC_IGNORE', '').split(',') if value.strip())
        previous = {name for name, row in checks.items() if row.get('waived')}
        black = checks.get('black_frames') or {}
        detail = black.get('detail') or {}
        duration = float(assembly.get('duration') or 0)
        outro = float(assembly.get('silent_outro_seconds') or 0)
        scope_changed = (0 < outro < duration and black.get('passed') is False
                         and detail.get('scope') != 'story'
                         and checks.get('ending_screen', {}).get('passed') is True)
        if not checks or (ignored.intersection(checks) == previous and not scope_changed):
            return report
        if not media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
            return report
        movie = Path(assembly.get('final_video') or '')
        if not movie.is_file():
            raise ValueError('existing movie is missing; cannot re-evaluate its quality policy')
        record_state(self.ctx, 'checking_existing_media', owner='episode_agent_tools')
        if scope_changed:
            intervals = detail.get('full_video_intervals', detail.get('intervals'))
            if intervals or (intervals == [] and detail.get('max_black_seconds') == 0):
                checks = {**checks, 'black_frames': black_frame_check(intervals, duration, outro)}
                scope_changed = False
        qc = None if scope_changed else apply_check_policy(checks, ignored)
        if qc is None:
            # Older waived reports did not retain the measured boolean. Only
            # withdrawing one of those waivers needs another decode of the movie.
            frame = frame_spec(load_profile(self.ctx.novel_dir))
            settings = Settings(width=frame['width'], height=frame['height'])
            qc = inspect_media(movie, Path(assembly['cover']), Path(assembly['ending']), Path(assembly['ass']),
                settings, self.ctx.episode_dir/'media_qc_report.json',
                silent_outro_seconds=float(assembly.get('silent_outro_seconds', 0)),
                ignore_checks=tuple(sorted(ignored)),
                subtitles_required=bool(checks.get('subtitles', {}).get('required', True)))
        else:
            atomic_write_json(self.ctx.episode_dir/'media_qc_report.json', qc)
        freeze = float(qc.get('checks', {}).get('long_freeze', {}).get('detail', {}).get('max_freeze_seconds', 0))
        others = [row.get('passed') for name, row in qc.get('checks', {}).items() if name != 'long_freeze']
        assembly.update(media_qc=qc, media_qc_passed=bool(qc.get('passed')), max_hold_seconds=freeze,
                        thin_passed=bool(others) and all(others) and freeze <= MAX_HOLD_SECONDS)
        report['assembly'] = assembly
        atomic_write_json(self.ctx.episode_dir/'thin_media_report.json', report)
        return report


    def render(self, payload, *, repairs):
        from novel_manga.application.repair import history
        from novel_manga.application.rendering.reviewed import _load_current
        from .local_preparation import active, stage, before_render
        local_inputs = active(self.ctx) and not media_started(self.ctx.episode_dir)
        if local_inputs:
            stage(self.ctx)
        path = self.ctx.episode_dir/'clip_plan.json'
        pending_script = self.ctx.input_mode == 'script' and not (self.ctx.run_dir/'prepared.json').is_file()
        if not path.is_file() or payload.get('file') or pending_script:
            adopted = self.drafts.adopt(payload)
            if adopted.get('ok') is not True:
                return adopted
        if read_json(self.ctx.work_dir/'input/manifest.json',{}).get('production_mode')=='batch_review':
            from novel_manga.application.production.workspaces import pending_production
            if pending_production(self.ctx):
                return {'ok':True,'pipeline_pending':True,
                        'next':'Updated first-pass inputs return to standard preparation and generation.'}
        if local_inputs:
            blocked=before_render(self.ctx)
            if blocked is not None:
                return blocked
        if (read_json(self.ctx.work_dir/'input/manifest.json',{}).get('production_mode') == 'pipeline_review'
                and not media_started(self.ctx.episode_dir)):
            from novel_manga.application.preparation.store import inputs
            native=read_json(self.ctx.run_dir/'pipeline-production.json',{})
            prepared=read_json(self.ctx.run_dir/'prepared.json',{})
            if (native.get('status')!='ready' or not prepared.get('ready')
                    or prepared.get('native_preparation_pending') or native.get('inputs')!=inputs(self.ctx.episode_dir)):
                if read_json(self.ctx.work_dir/'input/manifest.json',{}).get('agent_phase')=='input_repair':
                    from novel_manga.application.agents.episode.pipeline_preparation import prepare
                    result=prepare(self.ctx,self.drafts,repairs)
                    if result.get('ready'):
                        native=result
                    else:
                        return {'ok':False,'error':'semantic_repair_required' if result.get('needs_semantic_repair') else 'native_preparation_required',
                                'preparation':result}
                else:
                    return {'ok':False,'error':'native_preparation_required',
                            'next':'Complete the native source, asset and request checks before first video.'}
        plan = read_json(path)
        voice_blocked = self.evidence.voice_recording_blocks(plan)
        first_pass = os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1'
        if first_pass:
            from novel_manga.application.preparation.store import unfilmed_video_clips
            pending = unfilmed_video_clips(self.ctx.episode_dir, plan)
            voice_blocked = {cid: why for cid, why in voice_blocked.items() if cid in pending}
        if voice_blocked:
            return {'ok': False, 'error': 'voice_recording_blocked',
                    'voice_recording_blocked': voice_blocked, 'blocked': voice_blocked,
                    'can_adopt_draft': not media_started(self.ctx.episode_dir),
                    'generation_counts': history.generation_counts(self.ctx.episode_dir)}
        history.adopt_reviewed_run(self.ctx.episode_dir)
        history.begin_trial(self.ctx.episode_dir, {c['clip_id'] for c in plan['clips']},
            'episode_agent_tool_render', after_plan=plan,
            after_notes=read_json(self.ctx.episode_dir / 'review_feedback.json', {}))
        record = history.load(self.ctx.episode_dir)
        record['trials'][-1]['managed'] = True
        history.save(self.ctx.episode_dir, record)
        runner = self.runner()
        _load_current(runner)
        record_state(self.ctx, 'rendering', owner='episode_agent_tools',
                   workers=int(os.environ.get('NOVEL_RENDER_WORKERS', '2')),
                   inflight=int(os.environ.get('NOVEL_RENDER_INFLIGHT', '0')))
        report = runner.run(assemble=False)
        atomic_write_json(self.ctx.work_dir / 'input/latest-render.json', report)
        if first_pass:
            # Fill missing first takes before spending this host on film review.
            # Existing media stays on disk; the normal repair queue owns acceptance.
            record_state(self.ctx, 'first_pass_rendered', status=report.get('status'))
            return {'ok': True, 'artifact': 'input/latest-render.json', **self.evidence.media_summary(report),
                    'first_pass_remaining': sorted(unfilmed_video_clips(self.ctx.episode_dir, plan)),
                    'review_deferred': True, 'generation_counts': history.generation_counts(self.ctx.episode_dir)}
        # The base renderer supplies media/technical checks. Run the existing
        # independent review here; its cache reuses already-reviewed current takes.
        from novel_manga.application.review.execution import review_saved, save_result
        started = time.monotonic()
        report = self.hydrate_analysis(report, runner)
        verdicts = review_saved(self.ctx.episode_dir, fresh=False)
        episode_state.record(self.ctx.run_dir, 'review_saved',
                             checked=len(verdicts.get('clips') or {}), remaining=len(verdicts.get('feedback') or {}))
        report.setdefault('timing', {})['review_s'] = round(time.monotonic()-started, 3)
        report = save_result(self.ctx.episode_dir, report, verdicts, plan=read_json(path))
        if report.get('status') == 'clips_ready' and report['quality_review']['native_content_passed']:
            report = self.assemble(report, runner)
            verdicts = read_json(self.ctx.episode_dir/'episode_review.json', verdicts)
            report = save_result(self.ctx.episode_dir, report, verdicts, plan=read_json(path))
        atomic_write_json(self.ctx.work_dir / 'input/latest-render.json', report)
        review = self.evidence.review_evidence(verdicts, report, pending_only=True)
        record_state(self.ctx, 'rendered', status=report.get('status'))
        return {'ok': True, 'artifact': 'input/latest-render.json', **self.evidence.media_summary(report),
                'review': review,
                'generation_counts': history.generation_counts(self.ctx.episode_dir)}


    def review(self, payload):
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            raise ValueError('first pass defers film review; prepare and render the missing video clips')
        from novel_manga.application.review.execution import review_saved
        if not read_json(self.ctx.episode_dir / 'clip_plan.json'):
            raise ValueError('review requires an adopted episode')
        self.hydrate_analysis(read_json(self.ctx.episode_dir/'thin_media_report.json', {}))
        record_state(self.ctx, 'reviewing', owner='episode_agent_tools')
        review = review_saved(self.ctx.episode_dir, fresh=False)
        episode_state.record(self.ctx.run_dir, 'review_saved',
                             checked=len(review.get('clips') or {}), remaining=len(review.get('feedback') or {}))
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        report = self.review_final(report)
        review = read_json(self.ctx.episode_dir/'episode_review.json', review)
        from novel_manga.application.review.execution import save_result
        prior = report.get('quality_review') or {}
        report = save_result(self.ctx.episode_dir, report, review,
            rounds=prior.get('repair_rounds', []), corrections=prior.get('automatic_corrections', []),
            blocked=prior.get('blocked', {}))
        if report.get('status') == 'clips_ready' and report['quality_review']['native_content_passed']:
            report = self.assemble(report)
            review = read_json(self.ctx.episode_dir/'episode_review.json', review)
        return {'ok': True, **self.evidence.review_evidence(review, report),
                **self.evidence.media_summary(report)}
