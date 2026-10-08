"""Synchronous host media operations called by the episode's sandbox agent."""
from __future__ import annotations

import copy
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError


class EpisodeTools:
    """Transport and task scheduling belong to the caller, not these handlers."""

    def __init__(self, ctx):
        self.ctx = ctx

    def state(self, phase, **details):
        record = {"phase": phase, "pid": os.getpid(),
                  "at": datetime.datetime.now().isoformat(),
                  "episode": str(self.ctx.episode_dir), **details}
        atomic_write_json(self.ctx.run_dir / "execution.json", record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def dispatch(self, action, payload=None):
        try:
            if not isinstance(payload, dict) and payload is not None:
                raise ValueError('tool payload requires an object')
            if action not in {'check', 'adopt', 'render', 'review', 'apply', 'finish', 'status', 'doctor'}:
                raise ValueError('unknown episode tool action')
            result = getattr(self, action)(payload or {})
            if action in {'apply', 'finish'}:
                from . import recovery
                diagnosis = read_json(self.ctx.run_dir/'recovery-diagnosis.json', {})
                if not diagnosis.get('issues'):
                    diagnosis = recovery.diagnose(self.ctx, self.status({}))
                decisions = self._input(payload or {}, 'output/repair.json').get('decisions', []) if action == 'apply' else []
                recovery.remember(self.ctx, diagnosis, {'action': action, **result}, decisions)
            return {'action': action, **result}
        except Exception as error:
            return {'ok': False, 'action': action,
                    'error': f'{type(error).__name__}: {error}'}

    def _input(self, payload, default):
        value = payload.get('file', default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('file requires a workspace path')
        path = Path(value)
        if path.is_relative_to('/work'):
            path = self.ctx.work_dir / path.relative_to('/work')
        elif path.is_absolute():
            raise ValueError('file must be relative to /work or start with /work/')
        else:
            path = self.ctx.work_dir / path
        if not path.resolve().is_relative_to(self.ctx.work_dir.resolve()):
            raise ValueError('file must belong to the episode workspace')
        if not path.is_file():
            raise ValueError(f'input file is missing: {value}')
        return json.loads(path.read_text(encoding='utf-8'))

    def _validate(self, draft):
        if self.ctx.input_mode == 'script':
            return self._validate_requests(draft)
        from novel_manga.application.agents.episode.contracts import adopt, collect_format_issues
        inputs = {'catalog': read_json(self.ctx.run_dir / 'catalog.json'),
                  'source_segments': read_json(self.ctx.episode_dir / 'segments.json'),
                  'profile': read_json(self.ctx.novel_dir / 'profile.json'),
                  'target_seconds': self.ctx.target_seconds or (120, 150),
                  'clip_seconds_max': self.ctx.clip_seconds_max}
        issues = collect_format_issues(draft, **inputs)
        if issues:
            return issues, None, None
        script, plan = adopt(draft, **inputs)
        return [], script, plan

    @staticmethod
    def _check_existing_request(clip, prompt):
        from .contracts import validate_existing_request
        validate_existing_request(clip, prompt)

    def _validate_requests(self, draft):
        from novel_manga.application.profiles import h3_stamp
        from .requests import compose_request
        path = self.ctx.episode_dir / 'clip_plan.json'
        if not path.is_file():
            raise ValueError('script mode requires a prepared clip_plan.json from the existing packer')
        plan = read_json(path)
        script = read_json(self.ctx.episode_dir / 'chapter_script.json')
        if not isinstance(draft, dict) or not isinstance(draft.get('prompts'), dict):
            return ['requests requires a prompts object keyed by prepared clip_id'], None, None
        prompts = draft['prompts']
        wanted = {clip['clip_id'] for clip in plan['clips'] if clip.get('kind') == 'video'}
        if set(prompts) != wanted:
            return ['prompts must name every prepared video clip exactly once'], None, None
        notes = read_json(self.ctx.episode_dir / 'review_feedback.json', {})
        candidate = copy.deepcopy(plan)
        issues = []
        for clip in candidate['clips']:
            cid = clip['clip_id']
            if cid not in wanted:
                continue
            try:
                prompt = compose_request(clip, prompts[cid])
                self._check_existing_request(clip, prompt)
                clip['prompt_h3'] = prompt.strip()
                clip.pop('prompt_h3_skip', None)
                clip['prompt_h3_of'] = h3_stamp(clip, notes.get(cid, ''))
            except (ValueError, KeyError, TypeError) as error:
                issues.append(f'{cid}: {error}')
        return issues, script if not issues else None, candidate if not issues else None

    def _draft_file(self):
        return 'output/requests.json' if self.ctx.input_mode == 'script' else 'output/episode.json'

    def _edit_prompt(self, clip, row):
        from .requests import compose_request, compose_repair
        from .contracts import validate_existing_request, validate_patch
        pending=read_json(self.ctx.run_dir/'prepared.json',{}).get('native_preparation_pending')
        if pending and not self._main_media_started():
            try:
                validate_existing_request(clip,clip.get('prompt_h3') or '')
            except (ValueError,KeyError):
                prompt=compose_request(clip,row.get('h3',row.get('prompt_h3')))
                validate_existing_request(clip,prompt)
                return prompt
        prompt=compose_repair(clip,row)
        validate_patch(clip,prompt)
        return prompt

    def check(self, payload):
        from novel_manga.story.h3 import positive_direction_issues
        draft = self._input(payload, self._draft_file())
        if isinstance(draft, dict) and isinstance(draft.get('decisions'), list):
            from .input_repair import normalize_arguments
            from .requests import compose_repair
            from .contracts import validate_patch
            clips = {c['clip_id']: c for c in read_json(self.ctx.episode_dir/'clip_plan.json', {}).get('clips', [])}
            provisional = (self.ctx.input_mode == 'source' and not (self.ctx.run_dir/'prepared.json').exists()
                           and not self._main_media_started())
            errors, seen = {}, set()
            for number, row in enumerate(draft['decisions']):
                cid = row.get('clip_id') if isinstance(row, dict) else None
                try:
                    if not isinstance(cid, str) or cid in seen or (not provisional and cid not in clips):
                        raise RepairArgumentError('choose each current clip ID at most once')
                    seen.add(cid)
                    if not row.get('reason'):
                        raise RepairArgumentError('repair decision needs a reason')
                    action = row.get('action')
                    if not provisional:
                        from .arguments import first_pass_scope,spent_video_budget
                        first_pass_scope(self.ctx,[cid])
                        if action in {'edit','retake','input','accept_risk'}:
                            spent=spent_video_budget(self.ctx,[cid])
                            if spent:raise RepairArgumentError(spent[cid])
                    if action in {'replan','recheck_input'}:
                        from novel_manga.application.production.native_episode import validate_editorial_request
                        validate_editorial_request(self.ctx,self,row)
                    elif provisional:
                        from .draft_assets import validate_request
                        if action != 'input':
                            raise RepairArgumentError('edit the provisional draft directly; apply accepts asset input requests before adoption')
                        validate_request(self.ctx, row)
                    elif action == 'input':
                        from .input_repair import repair
                        repair(self.ctx,row,check_only=True)
                    elif action == 'accept_risk':
                        from novel_manga.application.preparation.readiness import validate_render_risks
                        validate_render_risks(self.ctx.episode_dir,clips[cid],row.get('risks'),row['reason'])
                    elif action == 'edit':
                        self._edit_prompt(clips[cid],row)
                    elif action == 'retake' and not clips[cid].get('prompt_h3'):
                        raise RepairArgumentError('clip has no English request; use edit to supply it before retake')
                    elif action not in {'keep','stop','retake','accept_risk'}:
                        raise RepairArgumentError('choose input, edit, retake, keep, accept_risk or stop')
                except (ValueError, KeyError, TypeError, OSError) as error:
                    errors[str(cid or number)] = str(error)
            return {'ok': not errors, 'valid': not errors, 'kind': 'repair_arguments',
                    'argument_errors': errors, 'requires_input_preflight': True,
                    'next': 'Fix argument errors here; apply performs source checks and publishing, render performs actual-reference preflight.'}
        issues, script, plan = self._validate(draft)
        clips = (plan or {}).get('clips', [])
        return {'ok': not issues, 'valid': not issues, 'errors': issues,
                'style_warnings': {c['clip_id']: advice for c in clips
                    if (advice := positive_direction_issues(c.get('prompt_h3', '')))},
                'clip_count': len(clips),
                'requested_seconds': sum(c['request_seconds'] for c in clips if c.get('kind') == 'video')}

    def _runner(self):
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
        runner.context.first_pass_only = os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1'
        return runner

    @staticmethod
    def _clip_technical_issues(report):
        assembly = report.get('assembly') or {}
        if assembly.get('thin_passed') is not False:
            return {}
        checks = (assembly.get('media_qc') or {}).get('checks') or {}
        return {name: value for name, value in checks.items()
                if name in {'black_frames', 'long_freeze', 'long_silence', 'silence_ratio'}
                and value.get('passed') is False}

    @staticmethod
    def _media_summary(report):
        assembly = report.get('assembly') or {}
        quality = report.get('quality_review') or {}
        return {**{key: report.get(key) for key in
                ('status', 'failed_clips', 'gate_failed_clips', 'blocked_clips', 'elapsed_seconds')},
                'technical_passed': assembly.get('thin_passed'),
                'content_passed': quality.get('content_passed'),
                'technical_issues': EpisodeTools._clip_technical_issues(report),
                'timeline': assembly.get('timeline', []),
                'assembly_pending': report.get('status') == 'clips_ready',
                'quality_passed': quality.get('passed'), 'movie': assembly.get('final_video')}

    @staticmethod
    def _unreviewed_clips(report, review):
        """A saved verdict belongs to its selected file, not just the clip ID."""
        from novel_manga.review.storage import take_identity
        missing = []
        for row in report.get('clips', []):
            selected = row.get('selected') or {}
            video = selected.get('postmix_video') or selected.get('video')
            if not video or not (path := Path(video)).is_file():
                continue
            verdict = (review.get('clips') or {}).get(row['clip_id'], {})
            if verdict.get('video') != str(path) or verdict.get('take') != take_identity(path):
                missing.append(row['clip_id'])
        return missing

    def _current_review(self, review):
        from novel_manga.application.review.evidence import review_mode
        from novel_manga.review.contracts import current_review
        return current_review(review, review_mode(self.ctx.episode_dir/'work/review/current'))

    def _assemble(self, report, runner=None):
        """Encode current selected takes and run final QC; never generate or replan."""
        from novel_manga.application.rendering.reviewed import _load_current
        from novel_manga.application.review.execution import media_matches
        from novel_manga.application.repair import history
        plan = read_json(self.ctx.episode_dir/'clip_plan.json')
        if not media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
            raise ValueError('current requests need render before existing takes can be assembled')
        if runner is None:
            runner = self._runner()
            _load_current(runner)
        clips = {clip['clip_id']: clip for clip in plan['clips']}
        hydrated = False
        for row in report['clips']:
            selected = row.get('selected')
            if not selected or isinstance(selected.get('chunks'), list):
                continue
            # First-pass reports can retain only video/passed. The ordinary
            # analyser reads ASR beside this selected take and refreshes it for
            # the current dialogue; another take's cached path is never used.
            analysis = runner.analyse_clip(clips[row['clip_id']], Path(selected['video']))
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
        self.state('assembling', owner='episode_agent_tools')
        started = time.monotonic()
        assembly = runner.assemble(report['clips'])
        seconds = round(time.monotonic()-started, 3)
        report.update(status='assembled', assembly=assembly,
                      elapsed_seconds=round(float(report.get('elapsed_seconds') or 0)+seconds, 3))
        report.setdefault('timing', {})['assembly_s'] = seconds
        atomic_write_json(self.ctx.episode_dir/'thin_media_report.json', report)
        history.record_render(self.ctx.episode_dir, report)
        return report

    def _refresh_technical_policy(self, report, plan):
        """Recheck the same movie when its book's QC waivers change; never render."""
        from novel_manga.application.profiles import frame_spec, load_profile, media_qc_ignores, MAX_HOLD_SECONDS
        from novel_manga.application.review.execution import media_matches
        from novel_manga.config import Settings
        from novel_manga.qc import apply_check_policy, inspect_media
        assembly = report.get('assembly') or {}
        checks = (assembly.get('media_qc') or {}).get('checks') or {}
        ignored = set(media_qc_ignores(self.ctx.novel_dir, self.ctx.episode_dir))
        ignored.update(value.strip() for value in os.environ.get('NOVEL_QC_IGNORE', '').split(',') if value.strip())
        previous = {name for name, row in checks.items() if row.get('waived')}
        if not checks or ignored.intersection(checks) == previous:
            return report
        if not media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
            return report
        movie = Path(assembly.get('final_video') or '')
        if not movie.is_file():
            raise ValueError('existing movie is missing; cannot re-evaluate its quality policy')
        self.state('checking_existing_media', owner='episode_agent_tools')
        qc = apply_check_policy(checks, ignored)
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

    def _voice_recording_blocks(self, plan):
        from novel_manga.runtime_backends import normalize_text
        blocked = {}
        for clip in plan.get('clips', []):
            if clip.get('audio_delivery') != 'postmix':
                continue
            directory = self.ctx.episode_dir / 'work/inner_voice' / clip['clip_id']
            info = clip['inner_voice']
            cards = [str(self.ctx.novel_dir / r['path']) for r in info.get('character_references', [])[:1]]
            voices = [str(self.ctx.novel_dir / r['path']) for r in info.get('voice_references', [])]
            voice = directory / 'voice.wav'
            saved = read_json(directory / 'voice.json', {}).get('input', {})
            if (voice.is_file() and voice.stat().st_size > 0
                    and saved.get('speaker') == info['speaker'] and saved.get('text') == info['text']
                    and saved.get('voice_references') == voices
                    and saved.get('source_audio') == info.get('source_audio')):
                continue
            for take in directory.glob('voice_take_*'):
                request = read_json(take / 'request.json', {})
                spoken = ''.join(re.findall(r'<d>\[Chinese\]\s*(.*?)</d>', request.get('prompt', ''), re.S))
                if (normalize_text(spoken) != normalize_text(info['text'])
                        or request.get('references') != cards or request.get('reference_audios') != voices
                        or request.get('duration') != clip['request_seconds']):
                    continue
                asr = take / 'asr.json'
                task = take / 'voice.mp4.task.json'
                # A submitted task with no later ASR remains the SDK's recovery
                # responsibility; an older failure does not decide its result.
                if (read_json(task, {}).get('task_id') and
                        (not asr.is_file() or task.stat().st_mtime_ns > asr.stat().st_mtime_ns)):
                    continue
                if read_json(asr, {}).get('passed') is False and (take / 'voice.mp4').is_file():
                    blocked[clip['clip_id']] = 'independent voice recording failed ASR; choose stop or finish'
                    break
                failed = take / 'voice.mp4.task.failed.json'
                if read_json(failed, {}).get('task_id'):
                    blocked[clip['clip_id']] = 'independent voice task failed; choose stop or finish'
                    break
        return blocked

    def _main_media_started(self):
        from novel_manga.application.repair import history
        if any(history.generation_counts(self.ctx.episode_dir).values()):
            return True
        clips = self.ctx.episode_dir / 'work/clips'
        return (any(p.stat().st_size > 0 for p in clips.glob('*/attempt_*/clip.mp4'))
                or any(read_json(p, {}).get('task_id') for p in clips.glob('*/attempt_*/clip.mp4.task.json')))

    def _unfilmed_clips(self, plan, report):
        selected = {r['clip_id']: r.get('selected') or {} for r in report.get('clips', [])}
        result = set()
        for clip in plan.get('clips', []):
            cid = clip['clip_id']; row = selected.get(cid, {})
            if clip.get('kind') != 'video' or row.get('video') or row.get('postmix_video'):
                continue
            folder = self.ctx.episode_dir/'work/clips'/cid
            if (any(p.stat().st_size for p in folder.glob('attempt_*/clip.mp4'))
                    or any(read_json(p, {}).get('task_id') for p in folder.glob('attempt_*/clip.mp4.task.json'))):
                continue
            result.add(cid)
        return result

    def _archive_adoption(self):
        root = self.ctx.run_dir / 'adoption-history'
        directory = root / f'{sum(p.is_dir() for p in root.glob("*")) + 1:03d}'
        directory.mkdir(parents=True)
        for path in (self.ctx.episode_dir / 'chapter_script.json', self.ctx.episode_dir / 'clip_plan.json',
                     self.ctx.episode_dir / 'episode_review.json', self.ctx.episode_dir / 'review_feedback.json',
                     self.ctx.episode_dir / 'thin_media_report.json', self.ctx.episode_dir / 'episode_execution.json',
                     self.ctx.run_dir / 'prepared.json', self.ctx.run_dir / 'adopted-episode.json',
                     self.ctx.run_dir / 'adopted-requests.json',
                     self.ctx.run_dir / 'tool-repair-decisions.json', self.ctx.run_dir / 'result.json'):
            if path.is_file():
                shutil.copy2(path, directory / path.name)
        if (self.ctx.run_dir / 'final-candidate').is_dir():
            shutil.copytree(self.ctx.run_dir / 'final-candidate', directory / 'final-candidate')
        return directory

    def adopt(self, payload):
        """Accept a validated draft for input repair without submitting any media."""
        from novel_manga.application.profiles import h3_stamp
        from novel_manga.application.repair import history
        if self._main_media_started():
            raise ValueError('main video has started; apply repair decisions, then render without file')
        manifest=read_json(self.ctx.work_dir/'input/manifest.json',{})
        if (manifest.get('production_mode') in {'pipeline_review','batch_review'}
                and manifest.get('agent_phase') in {'input_repair','review'}
                and (manifest.get('production_mode')=='pipeline_review' or (self.ctx.episode_dir/'clip_plan.json').exists())):
            raise ValueError('use scoped input repair; an explicit replan request is required for a whole screenplay')
        prior = read_json(self.ctx.run_dir/'prior-generation-counts.json', None)
        if prior is not None and (not prior.get('authorization') or not prior.get('reason')
                or not isinstance(prior.get('clips'), dict)
                or any(type(n) is not int or n < 0 for n in prior['clips'].values())):
            raise ValueError('host prior counts require explicit replacement authorization and nonnegative clip counts')
        path = self.ctx.episode_dir/'clip_plan.json'
        draft = self._input(payload, self._draft_file())
        issues, script, plan = self._validate(draft)
        if issues:
            return {'ok': False, 'valid': False, 'errors': issues}
        if path.is_file():
            self._archive_adoption()
        if self.ctx.input_mode == 'source':
            for clip in plan['clips']:
                clip['prompt_h3_of'] = h3_stamp(clip, '')
            atomic_write_json(self.ctx.episode_dir / 'chapter_script.json', script)
            atomic_write_json(self.ctx.episode_dir / 'review_feedback.json', {})
        atomic_write_json(path, plan)
        adopted = 'adopted-requests.json' if self.ctx.input_mode == 'script' else 'adopted-episode.json'
        atomic_write_json(self.ctx.run_dir / adopted, draft)
        atomic_write_json(self.ctx.run_dir / 'prepared.json', {'ready': True, 'clips': len(plan['clips']),
            'seconds': sum(c['request_seconds'] for c in plan['clips']),
            'author_session': read_json(self.ctx.work_dir / 'episode-session.json', {}).get('session_id'),
            'input_mode': self.ctx.input_mode,
            'method': ('sandbox tool adoption' if (self.ctx.work_dir / 'episode-session.json').is_file()
                       else 'existing request adoption by host'),
                'repair_round_start': len(read_json(self.ctx.run_dir / 'tool-repair-decisions.json', []))})
        if prior is not None:
            record = history.load(self.ctx.episode_dir)
            record['prior_generation_counts'] = prior
            history.save(self.ctx.episode_dir, record)
            grants = read_json(self.ctx.episode_dir/'repair_budget_grants.json', {})
            for clip in plan['clips']:
                if clip.get('kind') != 'video':
                    continue
                cid = clip['clip_id']; used = prior['clips'].get(cid, 0)
                grants[cid] = {'limit': used, 'reason': 'Retain prior candidate physical counts',
                    'conditional_extra': {'authorization': prior['authorization'], 'reason': prior['reason'],
                        'limit': used+1, 'baseline_count': used, 'baseline_limit': used,
                        'input_limit': 2, 'recheck_limit': 1, 'prepared': True, 'activated': False,
                        'preparation': 'Validated replacement draft adopted; actual-reference preflight still required'}}
            atomic_write_json(self.ctx.episode_dir/'repair_budget_grants.json', grants)
        return {'ok': True, 'valid': True, 'adopted': True, 'clip_count': len(plan['clips']),
                'generation_counts': history.generation_counts(self.ctx.episode_dir),
                'next': 'Inspect references and apply source-backed input repairs, then render the prepared plan.'}

    def render(self, payload):
        from novel_manga.application.repair import history
        from novel_manga.application.rendering.reviewed import _load_current
        from .local_preparation import active, stage, before_render
        local_inputs = active(self.ctx) and not self._main_media_started()
        if local_inputs:
            stage(self.ctx)
        path = self.ctx.episode_dir/'clip_plan.json'
        pending_script = self.ctx.input_mode == 'script' and not (self.ctx.run_dir/'prepared.json').is_file()
        if not path.is_file() or payload.get('file') or pending_script:
            adopted = self.adopt(payload)
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
                and not self._main_media_started()):
            from novel_manga.application.preparation.store import inputs
            native=read_json(self.ctx.run_dir/'pipeline-production.json',{})
            prepared=read_json(self.ctx.run_dir/'prepared.json',{})
            if (native.get('status')!='ready' or not prepared.get('ready')
                    or prepared.get('native_preparation_pending') or native.get('inputs')!=inputs(self.ctx.episode_dir)):
                if read_json(self.ctx.work_dir/'input/manifest.json',{}).get('agent_phase')=='input_repair':
                    from novel_manga.application.production.native_episode import prepare
                    result=prepare(self.ctx,self)
                    if result.get('ready'):
                        native=result
                    else:
                        return {'ok':False,'error':'semantic_repair_required' if result.get('needs_semantic_repair') else 'native_preparation_required',
                                'preparation':result}
                else:
                    return {'ok':False,'error':'native_preparation_required',
                            'next':'Complete the native source, asset and request checks before first video.'}
        plan = read_json(path)
        voice_blocked = self._voice_recording_blocks(plan)
        first_pass = os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1'
        if first_pass:
            from novel_manga.application.preparation.store import unfilmed_video_clips
            pending = unfilmed_video_clips(self.ctx.episode_dir, plan)
            voice_blocked = {cid: why for cid, why in voice_blocked.items() if cid in pending}
        if voice_blocked:
            return {'ok': False, 'error': 'voice_recording_blocked',
                    'voice_recording_blocked': voice_blocked, 'blocked': voice_blocked,
                    'can_adopt_draft': not self._main_media_started(),
                    'generation_counts': history.generation_counts(self.ctx.episode_dir)}
        history.adopt_reviewed_run(self.ctx.episode_dir)
        history.begin_trial(self.ctx.episode_dir, {c['clip_id'] for c in plan['clips']},
            'episode_agent_tool_render', after_plan=plan,
            after_notes=read_json(self.ctx.episode_dir / 'review_feedback.json', {}))
        record = history.load(self.ctx.episode_dir)
        record['trials'][-1]['managed'] = True
        history.save(self.ctx.episode_dir, record)
        runner = self._runner()
        _load_current(runner)
        self.state('rendering', owner='episode_agent_tools',
                   workers=int(os.environ.get('NOVEL_RENDER_WORKERS', '2')),
                   inflight=int(os.environ.get('NOVEL_RENDER_INFLIGHT', '0')))
        report = runner.run(assemble=False)
        atomic_write_json(self.ctx.work_dir / 'input/latest-render.json', report)
        if first_pass:
            # Fill missing first takes before spending this host on film review.
            # Existing media stays on disk; the normal repair queue owns acceptance.
            self.state('first_pass_rendered', status=report.get('status'))
            return {'ok': True, 'artifact': 'input/latest-render.json', **self._media_summary(report),
                    'first_pass_remaining': sorted(unfilmed_video_clips(self.ctx.episode_dir, plan)),
                    'review_deferred': True, 'generation_counts': history.generation_counts(self.ctx.episode_dir)}
        # The base renderer supplies media/technical checks. Run the existing
        # independent review here; its cache reuses already-reviewed current takes.
        from novel_manga.application.review.execution import review_saved, save_result
        started = time.monotonic()
        verdicts = review_saved(self.ctx.episode_dir, fresh=False)
        report.setdefault('timing', {})['review_s'] = round(time.monotonic()-started, 3)
        report = save_result(self.ctx.episode_dir, report, verdicts, plan=read_json(path))
        if report.get('status') == 'clips_ready' and report['quality_review']['content_passed']:
            report = self._assemble(report, runner)
            report = save_result(self.ctx.episode_dir, report, verdicts, plan=read_json(path))
        atomic_write_json(self.ctx.work_dir / 'input/latest-render.json', report)
        review = self._review_evidence(verdicts, report, pending_only=True)
        self.state('rendered', status=report.get('status'))
        return {'ok': True, 'artifact': 'input/latest-render.json', **self._media_summary(report),
                'review': review,
                'generation_counts': history.generation_counts(self.ctx.episode_dir)}

    def review(self, payload):
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            raise ValueError('first pass defers film review; prepare and render the missing video clips')
        from novel_manga.application.review.execution import review_saved
        if not read_json(self.ctx.episode_dir / 'clip_plan.json'):
            raise ValueError('review requires an adopted episode')
        self.state('reviewing', owner='episode_agent_tools')
        review = review_saved(self.ctx.episode_dir, fresh=False)
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        return self._review_evidence(review, report)

    def _review_evidence(self, review, report, *, pending_only=False):
        from novel_manga.application.repair import agent, history
        from novel_manga.application.review.execution import media_matches
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json')
        selected = {r['clip_id']: r.get('selected') or {} for r in report.get('clips', [])}
        current_review = self._current_review(review)
        current = (current_review and media_matches(plan, report,
            notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})))
        unreviewed = set(self._unreviewed_clips(report, review))
        failed = (set(review.get('feedback') or {}) | set(report.get('failed_clips') or [])
                  | set(report.get('gate_failed_clips') or []) | set(report.get('blocked_clips') or {}))
        publication = []
        if current_review and review.get('review_mode') == 'verify' and (report.get('assembly') or {}).get('pending_publish'):
            from novel_manga.application.repair.delivery import precise_review_blockers
            from novel_manga.application.review.store import current_takes
            publication = precise_review_blockers(review, current_takes(self.ctx.episode_dir, plan, review),
                [c['clip_id'] for c in plan['clips'] if c.get('kind') == 'video'])
            failed.update(publication)
        technical = self._clip_technical_issues(report) if current else {}
        if technical:
            failed.update(c['clip_id'] for c in plan['clips'] if c.get('kind') == 'video')
        evidence = {'chapter_script': read_json(self.ctx.episode_dir / 'chapter_script.json'),
                    'generation_counts': history.generation_counts(self.ctx.episode_dir),
                    'episode_technical_issues': technical,
                    'publication_blockers': publication,
                    'technical_repair_guidance': ('Locate the responsible clips using current evidence; choose only '
                        'supported repairs. Technical inspection targets do not require keep decisions for every clip.'
                        if technical else ''),
                    'feedback': review.get('feedback') or {}, 'targets': [],
                    'retained_clips': [], 'attention_clips': []}
        characters = read_json(self.ctx.novel_dir/'story_bible.json', {}).get('characters', [])
        summaries = []
        for clip in plan['clips']:
            cid = clip['clip_id']
            material = selected.get(cid, {})
            verdict = (review.get('clips') or {}).get(cid, {})
            is_video = clip.get('kind') == 'video'
            selected_video = material.get('postmix_video') or material.get('video')
            retained = (is_video and current and cid not in failed and cid not in unreviewed
                        and verdict.get('severity') in {'pass', 'minor'} and not verdict.get('error')
                        and material.get('passed') is True and selected_video and Path(selected_video).is_file())
            if is_video:
                evidence['retained_clips' if retained else 'attention_clips'].append(cid)
            summary = {'clip_id': cid, 'kind': clip.get('kind'), 'severity': verdict.get('severity'),
                'feedback': verdict.get('feedback') or evidence['feedback'].get(cid),
                'input_block': (report.get('blocked_clips') or {}).get(cid),
                'speech_passed': material.get('passed'),
                'needs_attention': is_video and not bool(retained), 'frames': []}
            summaries.append(summary)
            if pending_only and (retained or not is_video):
                continue
            item = {'clip': clip, 'verdict': verdict,
                    'input_block': summary['input_block'],
                    'selected_audio': {key: material.get(key) for key in
                        ('reference', 'hypothesis', 'whole_hypothesis', 'cer', 'issues', 'passed',
                         'mean_volume_db', 'max_volume_db')},
                    'frames': [], 'asr_files': []}
            chunks = material.get('chunks') or []
            item['selected_audio'].update(
                raw_asr_available=bool(chunks and all('raw_hypothesis' in row for row in chunks))
                    or 'raw_hypothesis' in material,
                raw_hypothesis=''.join(str(row.get('raw_hypothesis', row.get('hypothesis', ''))) for row in chunks)
                    if chunks else material.get('raw_hypothesis', material.get('hypothesis', '')),
                corrections=[change for row in chunks for change in row.get('corrections', [])],
                chunks=chunks,
                protected_terms=[name for character in characters
                                 if (name := character.get('name')) and name in str(clip.get('spoken_text') or '')])
            video = material.get('video')
            if video and Path(video).is_file():
                directory = self.ctx.work_dir / 'input/latest-review' / cid
                frames, provenance = agent.provided_frames(self.ctx.episode_dir, cid, Path(video), directory / 'frames')
                item.update(frames=[str(p.relative_to(self.ctx.work_dir)) for p in frames],
                            frame_provenance=provenance)
                frame_evidence = read_json(directory/'frames/frame-evidence.json', {})
                item['frame_evidence'] = {**frame_evidence, 'frames': [
                    {**row, 'path': str(Path(row['path']).relative_to(self.ctx.work_dir))}
                    for row in frame_evidence.get('frames', [])]}
                for name in ('asr.json', 'asr_raw.json', 'chunks.json', 'native.wav'):
                    source = Path(video).parent / name
                    if source.is_file():
                        target = directory / name
                        shutil.copy2(source, target)
                        item['asr_files'].append(str(target.relative_to(self.ctx.work_dir)))
            evidence['targets'].append(item)
            summary.update({'asr': {key: str(material.get(key) or '')[:250] for key in
                        ('reference', 'hypothesis', 'whole_hypothesis')}, 'cer': material.get('cer'),
                'frames': item['frames']})
        atomic_write_json(self.ctx.work_dir / 'input/latest-review.json', evidence)
        self.state('reviewed', feedback_clips=sorted(evidence['feedback']))
        return {'ok': True, 'artifact': 'input/latest-review.json', 'evidence': 'input/latest-review.json', 'clips': summaries,
                'review_required': not current_review or bool(unreviewed),
                'retained_clips': evidence['retained_clips'], 'attention_clips': evidence['attention_clips'],
                'feedback': evidence['feedback'], 'generation_counts': evidence['generation_counts']}

    def apply(self, payload):
        from novel_manga.application.repair import history
        answer = self._input(payload, 'output/repair.json')
        if not isinstance(answer, dict) or not isinstance(answer.get('decisions'), list):
            raise ValueError('repair requires a decisions array')
        if any(not isinstance(row, dict) or not isinstance(row.get('clip_id'), str)
               for row in answer['decisions']):
            raise ValueError('each repair decision requires an object and clip_id')
        if len({row['clip_id'] for row in answer['decisions']}) != len(answer['decisions']):
            raise ValueError('repair decision must name each eligible clip at most once')
        if any(row.get('action') in {'replan','recheck_input'} for row in answer['decisions']):
            if len(answer['decisions'])!=1:
                raise ValueError('submit one editorial operation separately from clip patches')
            from novel_manga.application.production.native_episode import editorial_request
            return editorial_request(self.ctx,self,answer['decisions'][0])
        if (self.ctx.input_mode == 'source' and not (self.ctx.run_dir/'prepared.json').exists()
                and not self._main_media_started()):
            from .draft_assets import repair
            if any(row.get('action') != 'input' for row in answer['decisions']):
                raise ValueError('edit the provisional draft directly; apply accepts asset input requests before adoption')
            results, blocked = [], {}
            for row in answer['decisions']:
                try:
                    results.append(repair(self.ctx, row))
                except Exception as error:
                    blocked[row['clip_id']] = str(error)
            return {'ok': not blocked, 'changed': [], 'input_repaired': [], 'blocked': blocked,
                    'assets_added': list(dict.fromkeys(x for r in results for x in r['assets_added'])),
                    'assets': [x for r in results for x in r['assets']],
                    'next': 'Read new images in input/catalog.json and bind their IDs in the draft before render.'}
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        allowed = (set(review.get('feedback') or {}) | set(report.get('gate_failed_clips') or [])
            | set(report.get('failed_clips') or []) | set(report.get('blocked_clips') or {})
            | {cid for cid, row in (review.get('clips') or {}).items()
               if row.get('severity') == 'review_error'})
        if self._clip_technical_issues(report):
            from novel_manga.application.review.execution import media_matches
            plan = read_json(self.ctx.episode_dir/'clip_plan.json', {})
            if media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
                videos = {c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'}
                # The Agent locates the source defect. Unselected good clips do
                # not acquire an artificial mandatory keep/repair decision.
                allowed.update(row['clip_id'] for row in answer['decisions'] if row['clip_id'] in videos)
        if (review.get('review_mode') == 'verify' and self._current_review(review)
                and (report.get('assembly') or {}).get('pending_publish')):
            from novel_manga.application.repair.delivery import precise_review_blockers
            from novel_manga.application.review.store import current_takes
            plan = read_json(self.ctx.episode_dir/'clip_plan.json', {})
            expected = [c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video']
            allowed.update(precise_review_blockers(review, current_takes(self.ctx.episode_dir, plan, review), expected))
        counts = history.generation_counts(self.ctx.episode_dir)
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            from .arguments import first_pass_scope
            pending = first_pass_scope(self.ctx,[row['clip_id'] for row in answer['decisions']])
            allowed &= pending
        voice_blocks = self._voice_recording_blocks(read_json(self.ctx.episode_dir / 'clip_plan.json', {}))
        voice_blocked = {row['clip_id']: voice_blocks[row['clip_id']] for row in answer['decisions']
                         if row.get('action') in {'edit', 'retake'} and row['clip_id'] in voice_blocks}
        if voice_blocked:
            return {'ok': False, 'error': 'voice_recording_blocked', 'changed': [],
                    'voice_recording_blocked': voice_blocked, 'blocked': voice_blocked,
                    'generation_counts': counts}
        from .arguments import spent_video_budget
        exhausted = spent_video_budget(self.ctx,[row['clip_id'] for row in answer['decisions']
            if row.get('action') in {'edit','retake','input','accept_risk'}])
        # A spent clip must not veto a different clip's permitted repair.
        answer = {**answer, 'decisions': [row for row in answer['decisions'] if row['clip_id'] not in exhausted]}
        input_rows = [row for row in answer['decisions'] if row.get('action') == 'input']
        input_changed, input_handled, input_blocked, repaired = [], set(), {}, []
        input_reused = []
        input_argument_errors = {}
        if input_rows:
            from .input_repair import repair, effects, normalize_arguments
            unfilmed = self._unfilmed_clips(read_json(self.ctx.episode_dir/'clip_plan.json', {}), report)
            handled_effects = []
            reused_effects = []
            for row in input_rows:
                cid = row['clip_id']
                if cid in input_handled:
                    raise ValueError('input decision must name each eligible clip at most once')
                input_handled.add(cid)
                try:
                    if cid not in allowed and cid not in unfilmed:
                        raise RepairArgumentError(
                            f'{cid} has no eligible unresolved issue; choose an eligible target: '
                            + ', '.join(sorted(allowed)))
                    row = normalize_arguments(row)
                    if not row.get('resume') and effects(row) in handled_effects:
                        repaired.append(cid)
                        if effects(row) in reused_effects:
                            input_reused.append(cid)
                        continue
                    if not row.get('reason'):
                        raise ValueError('input repair needs a reason')
                    result = repair(self.ctx, row)
                    input_changed.extend(result['changed'])
                    repaired.extend(result['input_repaired'])
                    if result.get('reused'):
                        input_reused.extend(result['input_repaired'])
                        reused_effects.append(effects(row))
                    handled_effects.append(effects(row))
                except Exception as error:
                    input_blocked[cid] = str(error)
                    if isinstance(error, RepairArgumentError):
                        input_argument_errors[cid] = str(error)
        deferred = [row['clip_id'] for row in answer['decisions']
                    if row.get('action') != 'input' and row['clip_id'] in input_changed]
        ordinary = {'decisions': [row for row in answer['decisions']
                    if row.get('action') != 'input' and row['clip_id'] not in input_changed]}
        outcome = self.apply_decisions(ordinary, allowed-input_handled-set(input_changed))
        outcome['changed'] = list(dict.fromkeys([*input_changed, *outcome['changed']]))
        outcome['blocked'].update(input_blocked)
        outcome['blocked'].update(exhausted)
        if input_argument_errors:
            outcome.setdefault('argument_errors', {}).update(input_argument_errors)
        outcome['input_repaired'] = list(dict.fromkeys([*repaired, *outcome.get('risk_accepted', [])]))
        if input_reused:
            outcome['input_reused'] = list(dict.fromkeys(input_reused))
        # Merely accepting a soft input risk is not an extra-take repair plan.
        history.mark_extra_prepared(self.ctx.episode_dir, set(outcome['changed']) | set(repaired))
        if input_rows:
            from .input_repair import repair_status
            outcome['input_repairs'] = repair_status(self.ctx)
        if deferred:
            outcome['deferred'] = deferred
            outcome['deferred_reason'] = 'Input repair changed these requests; inspect the updated evidence before editing their old prompts.'
        path = self.ctx.run_dir / 'tool-repair-decisions.json'
        rounds = read_json(path, [])
        rounds.append(outcome)
        atomic_write_json(path, rounds)
        self.state('repair_applied', changed=outcome['changed'], blocked=outcome['blocked'])
        return {'ok': not outcome['blocked'], **outcome,
                'generation_counts': history.generation_counts(self.ctx.episode_dir)}

    def apply_decisions(self, answer, allowed):
        """Apply the agent's one candidate through existing publication and keep review."""
        from novel_manga.application.agents.episode.contracts import validate_patch
        from .requests import compose_repair
        from novel_manga.application.repair import history
        from novel_manga.application.repair.publication import publish_candidate
        from novel_manga.application.review.execution import recheck_current_clip
        from novel_manga.repair.proposal import RepairProposal
        from novel_manga.application.profiles import h3_stamp
        directory = self.ctx.episode_dir
        plan = read_json(directory / 'clip_plan.json')
        candidate = copy.deepcopy(plan)
        blocked, changed, kept, keep_checks, risk_accepted = {}, [], [], [], []
        argument_errors = {}
        from novel_manga.review.storage import take_identity
        review = read_json(directory/'episode_review.json', {})
        current_review = self._current_review(review)
        media = read_json(directory/'thin_media_report.json', {})
        selected = {r['clip_id']: r.get('selected') or {} for r in media.get('clips', [])}
        seen = set()
        for row in answer.get('decisions', []):
            cid = row.get('clip_id')
            if cid in seen:
                raise ValueError('repair decision must name each eligible clip at most once')
            seen.add(cid)
            verdict = (review.get('clips') or {}).get(cid, {})
            take = selected.get(cid, {})
            video = Path(take['video']) if take.get('video') else None
            if (row.get('action') == 'keep' and current_review
                    and verdict.get('severity') in {'pass', 'minor'} and cid not in (review.get('feedback') or {})
                    and take.get('passed') is True and video and video.is_file()
                    and verdict.get('take') == take_identity(video)):
                kept.append(cid)
                continue
            if cid not in allowed:
                blocked[cid] = 'clip has no eligible failure or current verified take to keep'
                continue
            clip = copy.deepcopy(next(c for c in candidate['clips'] if c['clip_id'] == cid))
            try:
                action = row['action']
                if not row.get('reason'):
                    raise ValueError('repair decision needs a reason')
                if action == 'keep':
                    if not video or not video.is_file():
                        raise ValueError('no current video to keep; use input to fix references or accept_risk for an explicit pre-render reference risk')
                    limit = history.recheck_limit(directory, cid)
                    prior = read_json(self.ctx.run_dir/'tool-repair-decisions.json', [])
                    if limit is not None and sum(cid in r.get('keep_checks', []) for r in prior) >= int(limit):
                        raise ValueError('independent recheck allowance used; stop unresolved clip')
                    keep_checks.append(cid)
                    checked = recheck_current_clip(directory, cid)
                    kept.extend(checked['accepted'])
                    if not checked['accepted']:
                        blocked[cid] = 'current video independent recheck still failed'
                elif action == 'accept_risk':
                    from novel_manga.application.preparation.readiness import accept_render_risks
                    if accept_render_risks(directory, clip, row.get('risks'), row['reason']):
                        risk_accepted.append(cid)
                elif action in {'edit', 'retake'}:
                    if action == 'edit':
                        prompt = self._edit_prompt(clip,row)
                        if prompt.strip() == (clip.get('prompt_h3') or '').strip():
                            raise ValueError('agent edit made no request change')
                        clip['prompt_h3'] = prompt.strip()
                    elif not clip.get('prompt_h3'):
                        raise ValueError('clip has no English request; use edit to supply h3 directions or prompt_h3 before retake')
                    clip['repair_take'] = int(clip.get('repair_take', 0)) + 1
                    notes = read_json(directory / 'review_feedback.json', {})
                    clip['prompt_h3_of'] = h3_stamp(clip, notes.get(cid, ''))
                    clip['prompt_h3_agent'] = {
                        'run': self.ctx.run_dir.name, 'reason': row['reason'],
                        'session': read_json(self.ctx.work_dir / 'episode-session.json', {})}
                    candidate['clips'] = [clip if c['clip_id'] == cid else c for c in candidate['clips']]
                    changed.append(cid)
                elif action == 'stop':
                    blocked[cid] = row['reason']
                else:
                    raise ValueError('unknown repair action')
            except (ValueError, KeyError) as error:
                blocked[cid] = str(error)
                if isinstance(error, RepairArgumentError):
                    argument_errors[cid] = str(error)
        for cid in allowed - seen:
            blocked[cid] = 'agent omitted a decision'
        if changed:
            proposal = RepairProposal(
                result={'changed': changed}, script=read_json(directory / 'chapter_script.json'),
                plan=candidate, notes=read_json(directory / 'review_feedback.json', {}),
                changes={cid: next(r for r in answer['decisions'] if r['clip_id'] == cid) for cid in changed})
            publish_candidate(directory, proposal, 'episode_agent', changed)
            record = history.load(directory)
            record['trials'][-1]['managed'] = True
            history.save(directory, record)
        return {'changed': changed, 'accepted': kept, 'blocked': blocked, 'risk_accepted': risk_accepted,
                **({'argument_errors': argument_errors} if argument_errors else {}),
                **({'keep_checks': keep_checks} if keep_checks else {})}

    def repair_evidence(self, report, review, targets, round_number):
        """Expose existing host observations and supported frames for the same session."""
        from novel_manga.application.repair import agent, history
        directory = self.ctx.work_dir / 'input' / f'review-{round_number}'
        directory.mkdir(parents=True, exist_ok=True)
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json')
        rows = {r['clip_id']: r for r in report.get('clips', [])}
        evidence = {'round': round_number, 'targets': [],
                    'generation_counts': history.generation_counts(self.ctx.episode_dir),
                    'chapter_script': read_json(self.ctx.episode_dir / 'chapter_script.json')}
        for clip in plan['clips']:
            cid = clip['clip_id']
            if cid not in targets:
                continue
            selected = rows.get(cid, {}).get('selected') or {}
            item = {'clip': clip, 'verdict': review.get('clips', {}).get(cid, {}),
                    'input_block': report.get('blocked_clips', {}).get(cid),
                    'selected_audio': {key: selected.get(key) for key in
                        ('reference', 'hypothesis', 'cer', 'issues', 'passed', 'whole_hypothesis')},
                    'frames': []}
            video = selected.get('video')
            if video and Path(video).is_file():
                frames, provenance = agent.provided_frames(
                    self.ctx.episode_dir, cid, Path(video), directory / cid / 'frames')
                item.update(frames=[str(p.relative_to(self.ctx.work_dir)) for p in frames],
                            frame_provenance=provenance)
            evidence['targets'].append(item)
        path = directory / 'evidence.json'
        atomic_write_json(path, evidence)
        return str(path.relative_to(self.ctx.work_dir))

    def finish(self, payload):
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            raise ValueError('first pass hands completed video to the review queue; finish belongs to that queue')
        from novel_manga.application.repair import history
        from novel_manga.application.review.execution import save_result, media_matches
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        rounds = read_json(self.ctx.run_dir / 'tool-repair-decisions.json', [])
        unreviewed = self._unreviewed_clips(report, review)
        if unreviewed:
            return {'ok': False, 'error': 'missing_review', 'required_action': 'review',
                    'clips': unreviewed, 'message': 'Generated takes need independent review before finishing.'}
        from novel_manga.review.contracts import REVIEW_POLICY
        if review.get('clips') and not self._current_review(review):
            return {'ok': False, 'error': 'stale_review', 'required_action': 'review',
                    'message': 'Run review with the current policy and configured mode before finishing existing video.',
                    'review_policy': review.get('policy'), 'expected_review_policy': REVIEW_POLICY}
        if report.get('status') == 'clips_ready':
            if not media_matches(plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
                return {'ok': False, 'error': 'stale_media', 'required_action': 'render',
                        'message': 'Current requests need render before existing takes can be assembled.'}
            report = self._assemble(report)
        report = self._refresh_technical_policy(report, plan)
        rounds = rounds[read_json(self.ctx.run_dir / 'prepared.json', {}).get('repair_round_start', 0):]
        blocked = {}
        for row in rounds:
            for cid in (row.get('changed', []) + row.get('accepted', [])
                        + row.get('input_repaired', []) + row.get('risk_accepted', [])):
                blocked.pop(cid, None)
            blocked.update(row.get('blocked') or {})
        report = save_result(self.ctx.episode_dir, report, review, plan=plan, rounds=rounds,
            corrections=[cid for row in rounds for cid in row.get('changed', [])], blocked=blocked)
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
            'voice_recording_counts': voices, 'voice_recording_blocked': self._voice_recording_blocks(plan),
            'author_session': author_session,
            'movie': str(copied_movie) if copied_movie else None, 'frozen': read_json(self.ctx.run_dir / 'frozen.json')}
        atomic_write_json(final / 'manifest.json', manifest)
        atomic_write_json(self.ctx.work_dir / 'input/latest-finish.json', manifest)
        self.state('finished' if manifest['passed'] else 'finished_with_issues', movie=manifest['movie'])
        return {'ok': True, 'passed': manifest['passed'],
                'quality_review': {key: quality.get(key) for key in
                                  ('passed', 'missing_reviews', 'review_errors', 'remaining', 'reason')},
                'technical_passed': manifest['technical_passed'], 'movie': manifest['movie'],
                'artifact': 'input/latest-finish.json', 'manifest': str(final / 'manifest.json'),
                'generation_counts': manifest['actual_video_counts']}

    def doctor(self, payload):
        from .doctor import run_doctor
        report = run_doctor(self.ctx.novel_dir)
        self.state('doctor', ok=report['ok'])
        return {'ok': True, 'report': report}

    def status(self, payload):
        from collections import Counter
        from novel_manga.application.repair import history
        from .input_repair import repair_status, remaining_input_attempts, SHOT_FIELDS
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        review = read_json(self.ctx.episode_dir/'episode_review.json', {})
        media = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        unreviewed = self._unreviewed_clips(media, review)
        counts = history.generation_counts(self.ctx.episode_dir)
        clip_ids = [c['clip_id'] for c in plan.get('clips', [])]
        rechecks = Counter(cid for row in read_json(self.ctx.run_dir/'tool-repair-decisions.json', [])
                           for cid in row.get('keep_checks', []))
        limits = {cid: history.recheck_limit(self.ctx.episode_dir, cid) for cid in clip_ids}
        extra = {cid: history.conditional_extra(self.ctx.episode_dir, cid) for cid in clip_ids}
        input_left = remaining_input_attempts(self.ctx, clip_ids)
        input_states = repair_status(self.ctx)
        take_left = {cid: max(0, history.preparation_limit(self.ctx.episode_dir, cid)-counts.get(cid, 0))
                     for cid in clip_ids}
        check_left = {cid: max(0, int(limit)-rechecks[cid]) for cid, limit in limits.items() if limit is not None}
        eligible = (set(review.get('feedback') or {}) | set(media.get('failed_clips') or [])
                    | set(media.get('gate_failed_clips') or []) | set(media.get('blocked_clips') or {})
                    | set((media.get('quality_review') or {}).get('remaining') or []))
        publication = []
        if (review.get('review_mode') == 'verify' and self._current_review(review)
                and (media.get('assembly') or {}).get('pending_publish')):
            from novel_manga.application.repair.delivery import precise_review_blockers
            from novel_manga.application.review.store import current_takes
            publication = precise_review_blockers(review, current_takes(self.ctx.episode_dir, plan, review),
                [c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'])
            eligible.update(publication)
        if self._clip_technical_issues(media):
            eligible.update(c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video')
        videos = {r['clip_id'] for r in media.get('clips', []) if (r.get('selected') or {}).get('video')}
        voice_blocks = self._voice_recording_blocks(plan)
        actions = {}
        risk_choices={}
        from novel_manga.application.preparation.readiness import render_risk_choices
        unfilmed = self._unfilmed_clips(plan, media)
        for clip in plan.get('clips', []):
            cid = clip['clip_id']; allowed = []
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
            if (cid in eligible or cid in unfilmed) and take_left[cid] and (
                    input_left[cid] or input_states.get(cid, {}).get('resume_action')
                    or input_states.get(cid, {}).get('revision_available')):
                allowed.append('input')
            actions[cid] = allowed
        from novel_manga.application.production.native_episode import semantic_pending
        editorial = semantic_pending(self.ctx) and not self._main_media_started()
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
                'can_adopt_draft': not self._main_media_started() and not scoped_editor,
                'review_required': bool(unreviewed) or (bool(review.get('clips')) and not self._current_review(review)),
                'unreviewed_clips': unreviewed,
                'review_command': 'review before finish when current video reviews are missing or stale',
                'retry_policy': {'scope': 'current per-clip permissions include approved conditional extras',
                    'instruction': 'Use repair_actions to submit apply now. A pending extra is permission to prepare '
                                   'an edit/input/retake; the host activates generation after validation and input preflight. '
                                   'Do not infer a hard limit from historical counts or previous stop messages.'},
                'repair_actions': actions,
                'risk_choices':{cid:v for cid,v in risk_choices.items() if 'accept_risk' in actions[cid]},
                'publication_blockers': publication,
                'remaining_input_attempts': input_left,
                'remaining_rechecks': check_left,
                'conditional_extra_takes': {cid: {'limit': int(row['limit']),
                    'remaining': max(0, int(row['limit'])-counts.get(cid, 0)),
                    'prepared': bool(row.get('prepared')), 'activated': bool(row.get('activated')),
                    'next': 'Review current evidence and submit a valid edit/input/retake; generation activates only after final input preflight.'}
                    for cid, row in extra.items() if row},
                'input_repairs': input_states,
                'adopt_draft_command': ('Use scoped input repair or an explicitly offered replan operation.' if scoped_editor else
                    f'adopt {self._draft_file()} to prepare inputs without video; existing history is retained'),
                'generation_counts': counts,
                'voice_recording_blocked': voice_blocks,
                'remaining_takes': take_left,
                'media': self._media_summary(media)}
