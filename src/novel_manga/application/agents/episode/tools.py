"""Synchronous host media operations called by the episode's sandbox agent."""
from __future__ import annotations

import copy
import datetime
import json
import os
from pathlib import Path
import re
import shutil

from novel_manga.util import atomic_write_json, read_json


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
            if action not in {'check', 'render', 'review', 'apply', 'finish', 'status'}:
                raise ValueError('unknown episode tool action')
            result = getattr(self, action)(payload or {})
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
        """Bind a new final request to the prepared plan, without rewriting that plan."""
        from novel_manga.application.agents.episode.contracts import SPEECH, _reference_bindings
        from novel_manga.application.repair.agent import sections
        from novel_manga.story.dialogue import subject_map
        from novel_manga.story.h3 import request_issues
        from novel_manga.story.voice_delivery import visual_request
        visual = visual_request({**clip, 'prompt_h3': prompt})
        if 'dialogue_bindings' not in visual:
            raise ValueError('prepared clip requires dialogue_bindings')
        blocks = sections(prompt)
        _reference_bindings(visual, blocks)
        problems = request_issues(visual)
        if problems:
            raise ValueError('; '.join(problems))
        stages = re.findall(r'\[Shot (\d+)\]', blocks['detailed_description'])
        count = int(clip.get('stage_count') or len(clip.get('shot_indexes', [])))
        if stages != [str(i) for i in range(1, count + 1)]:
            raise ValueError('final request must preserve the prepared shot order and count')
        subjects = subject_map(visual)
        bindings = visual['dialogue_bindings']
        speech = list(SPEECH.finditer(blocks['detailed_description']))
        if len(speech) != len(bindings):
            raise ValueError('final request must preserve every prepared dialogue once')
        speakers = list(dict.fromkeys(row['speaker_name'] for row in bindings))
        voices = {ref['name']: i for i, ref in enumerate(
            (ref for ref in visual['references'] if ref.get('role') == 'voice'), 1)}
        heard = set()
        for number, (said, row) in enumerate(zip(speech, bindings)):
            owner = row['speaker_name']
            subject = int(said[1]) if said[1] else None
            if (said[4].strip() != row['text'] or int(said[2]) != speakers.index(owner) + 1
                    or subject != subjects.get(owner)
                    or bool(said[3]) != (row['delivery_mode'] == 'offscreen_dialogue')):
                raise ValueError('final request changed protected dialogue or speaker binding')
            next_speech = speech[number + 1].start() if number + 1 < len(speech) else len(said.string)
            tail = said.string[said.end():next_speech].split('[Shot ', 1)[0]
            audio = re.findall(r'The speaking voice follows(?: the timbre referenced from)?\s+<Audio (\d+)>\.', tail)
            if audio or (owner in voices and owner not in heard):
                if audio != ([str(voices[owner])] if owner in voices else []):
                    raise ValueError('final request changed a prepared speaker audio reference')
            heard.add(owner)
        timing = clip.get('shot_timing') or []
        if timing:
            elapsed = 0.0
            bodies = re.split(r'\[Shot \d+\]', blocks['detailed_description'])[1:]
            for i, (body, row) in enumerate(zip(bodies, timing), 1):
                cuts = re.findall(r'At (\d\d):(\d\d\.\d+),', body)
                if i > 1 and (len(cuts) != 1 or abs(int(cuts[0][0]) * 60 + float(cuts[0][1]) - elapsed) > .001):
                    raise ValueError('final request changed a prepared shot cut time')
                duration = re.findall(r'Planned duration: ([\d.]+) seconds\.', body)
                if duration and (len(duration) != 1 or abs(float(duration[0]) - row['seconds']) > .001):
                    raise ValueError('final request changed a prepared shot duration')
                elapsed += float(row['seconds'])

    def _validate_requests(self, draft):
        from novel_manga.application.profiles import h3_stamp
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
                prompt = prompts[cid]
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError('final request requires a nonempty English string')
                self._check_existing_request(clip, prompt)
                clip['prompt_h3'] = prompt.strip()
                clip.pop('prompt_h3_skip', None)
                clip['prompt_h3_of'] = h3_stamp(clip, notes.get(cid, ''))
            except (ValueError, KeyError, TypeError) as error:
                issues.append(f'{cid}: {error}')
        return issues, script if not issues else None, candidate if not issues else None

    def _draft_file(self):
        return 'output/requests.json' if self.ctx.input_mode == 'script' else 'output/episode.json'

    def check(self, payload):
        draft = self._input(payload, self._draft_file())
        issues, script, plan = self._validate(draft)
        clips = (plan or {}).get('clips', [])
        return {'ok': not issues, 'valid': not issues, 'errors': issues,
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
        return runner

    @staticmethod
    def _media_summary(report):
        assembly = report.get('assembly') or {}
        quality = report.get('quality_review') or {}
        return {**{key: report.get(key) for key in
                ('status', 'failed_clips', 'gate_failed_clips', 'blocked_clips', 'elapsed_seconds')},
                'technical_passed': assembly.get('thin_passed'),
                'quality_passed': quality.get('passed'), 'movie': assembly.get('final_video')}

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

    def render(self, payload):
        from novel_manga.application.profiles import h3_stamp
        from novel_manga.application.repair import history
        from novel_manga.application.rendering.reviewed import _load_current
        path = self.ctx.episode_dir / 'clip_plan.json'
        if path.is_file() and payload.get('file') and self._main_media_started():
            raise ValueError('main video has started; apply repair decisions, then render without file')
        pending_script = self.ctx.input_mode == 'script' and not (self.ctx.run_dir / 'prepared.json').is_file()
        if not path.is_file() or payload.get('file') or pending_script:
            if self._main_media_started():
                raise ValueError('main video has started; apply repair decisions, then render without file')
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
                'method': 'sandbox tool adoption',
                'repair_round_start': len(read_json(self.ctx.run_dir / 'tool-repair-decisions.json', []))})
        plan = read_json(path)
        voice_blocked = self._voice_recording_blocks(plan)
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
        report = runner.run()
        atomic_write_json(self.ctx.work_dir / 'input/latest-render.json', report)
        self.state('rendered', status=report.get('status'))
        return {'ok': True, 'artifact': 'input/latest-render.json', **self._media_summary(report),
                'generation_counts': history.generation_counts(self.ctx.episode_dir)}

    def review(self, payload):
        from novel_manga.application.repair import agent, history
        from novel_manga.application.review.execution import review_saved
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json')
        if not plan:
            raise ValueError('review requires an adopted episode')
        self.state('reviewing', owner='episode_agent_tools')
        review = review_saved(self.ctx.episode_dir, fresh=False)
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        selected = {r['clip_id']: r.get('selected') or {} for r in report.get('clips', [])}
        evidence = {'chapter_script': read_json(self.ctx.episode_dir / 'chapter_script.json'),
                    'generation_counts': history.generation_counts(self.ctx.episode_dir),
                    'feedback': review.get('feedback') or {}, 'targets': []}
        summaries = []
        for clip in plan['clips']:
            cid = clip['clip_id']
            material = selected.get(cid, {})
            verdict = (review.get('clips') or {}).get(cid, {})
            item = {'clip': clip, 'verdict': verdict,
                    'input_block': (report.get('blocked_clips') or {}).get(cid),
                    'selected_audio': {key: material.get(key) for key in
                        ('reference', 'hypothesis', 'whole_hypothesis', 'cer', 'issues', 'passed')},
                    'frames': [], 'asr_files': []}
            video = material.get('video')
            if video and Path(video).is_file():
                directory = self.ctx.work_dir / 'input/latest-review' / cid
                frames, provenance = agent.provided_frames(self.ctx.episode_dir, cid, Path(video), directory / 'frames')
                item.update(frames=[str(p.relative_to(self.ctx.work_dir)) for p in frames],
                            frame_provenance=provenance)
                for name in ('asr.json', 'asr_raw.json', 'chunks.json'):
                    source = Path(video).parent / name
                    if source.is_file():
                        target = directory / name
                        shutil.copy2(source, target)
                        item['asr_files'].append(str(target.relative_to(self.ctx.work_dir)))
            evidence['targets'].append(item)
            summaries.append({'clip_id': cid, 'severity': verdict.get('severity'),
                'feedback': verdict.get('feedback') or evidence['feedback'].get(cid),
                'input_block': item['input_block'], 'speech_passed': material.get('passed'),
                'asr': {key: str(material.get(key) or '')[:250] for key in
                        ('reference', 'hypothesis', 'whole_hypothesis')}, 'cer': material.get('cer'),
                'frames': item['frames']})
        atomic_write_json(self.ctx.work_dir / 'input/latest-review.json', evidence)
        self.state('reviewed', feedback_clips=sorted(evidence['feedback']))
        return {'ok': True, 'artifact': 'input/latest-review.json', 'evidence': 'input/latest-review.json', 'clips': summaries,
                'feedback': evidence['feedback'], 'generation_counts': evidence['generation_counts']}

    def apply(self, payload):
        from novel_manga.application.repair import history
        answer = self._input(payload, 'output/repair.json')
        if not isinstance(answer, dict) or not isinstance(answer.get('decisions'), list):
            raise ValueError('repair requires a decisions array')
        if any(not isinstance(row, dict) or not isinstance(row.get('clip_id'), str)
               for row in answer['decisions']):
            raise ValueError('each repair decision requires an object and clip_id')
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        allowed = (set(review.get('feedback') or {}) | set(report.get('gate_failed_clips') or [])
            | set(report.get('failed_clips') or []) | set(report.get('blocked_clips') or {})
            | {cid for cid, row in (review.get('clips') or {}).items()
               if row.get('severity') == 'review_error'})
        counts = history.generation_counts(self.ctx.episode_dir)
        voice_blocks = self._voice_recording_blocks(read_json(self.ctx.episode_dir / 'clip_plan.json', {}))
        voice_blocked = {row['clip_id']: voice_blocks[row['clip_id']] for row in answer['decisions']
                         if row.get('action') in {'edit', 'retake'} and row['clip_id'] in voice_blocks}
        if voice_blocked:
            return {'ok': False, 'error': 'voice_recording_blocked', 'changed': [],
                    'voice_recording_blocked': voice_blocked, 'blocked': voice_blocked,
                    'generation_counts': counts}
        exhausted = {row['clip_id']: 'effective clip retry budget used'
            for row in answer['decisions'] if row.get('action') in {'edit', 'retake'}
            and counts.get(row['clip_id'], 0) >= history.generation_limit(self.ctx.episode_dir, row['clip_id'])}
        if exhausted:
            return {'ok': False, 'changed': [], 'blocked': exhausted, 'generation_counts': counts}
        outcome = self.apply_decisions(answer, allowed)
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
        from novel_manga.application.repair import history
        from novel_manga.application.repair.publication import publish_candidate
        from novel_manga.application.review.execution import recheck_current_clip
        from novel_manga.repair.proposal import RepairProposal
        from novel_manga.application.profiles import h3_stamp
        directory = self.ctx.episode_dir
        plan = read_json(directory / 'clip_plan.json')
        candidate = copy.deepcopy(plan)
        blocked, changed, kept = {}, [], []
        seen = set()
        for row in answer.get('decisions', []):
            cid = row.get('clip_id')
            if cid not in allowed or cid in seen:
                raise ValueError('repair decision must name each eligible clip at most once')
            seen.add(cid)
            clip = copy.deepcopy(next(c for c in candidate['clips'] if c['clip_id'] == cid))
            try:
                action = row['action']
                if not row.get('reason'):
                    raise ValueError('repair decision needs a reason')
                if action == 'keep':
                    checked = recheck_current_clip(directory, cid)
                    kept.extend(checked['accepted'])
                    if not checked['accepted']:
                        blocked[cid] = 'current video independent recheck still failed'
                elif action in {'edit', 'retake'}:
                    if action == 'edit':
                        validate_patch(clip, row['prompt_h3'])
                        if row['prompt_h3'].strip() == clip['prompt_h3'].strip():
                            raise ValueError('agent edit made no request change')
                        clip['prompt_h3'] = row['prompt_h3'].strip()
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
        return {'changed': changed, 'accepted': kept, 'blocked': blocked}

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
        from novel_manga.application.repair import history
        from novel_manga.application.review.execution import save_result
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        rounds = read_json(self.ctx.run_dir / 'tool-repair-decisions.json', [])
        rounds = rounds[read_json(self.ctx.run_dir / 'prepared.json', {}).get('repair_round_start', 0):]
        blocked = {}
        for row in rounds:
            for cid in row.get('changed', []) + row.get('accepted', []):
                blocked.pop(cid, None)
            blocked.update(row.get('blocked') or {})
        report = save_result(self.ctx.episode_dir, report, review, plan=plan, rounds=rounds,
            corrections=[cid for row in rounds for cid in row.get('changed', [])], blocked=blocked)
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
        manifest = {'method': 'one persistent sandbox agent owns authoring and host tool calls',
            'passed': quality.get('passed') is True, 'quality_review': quality,
            'technical_passed': (report.get('assembly') or {}).get('thin_passed') is True,
            'actual_video_counts': history.generation_counts(self.ctx.episode_dir),
            'voice_recording_counts': voices, 'voice_recording_blocked': self._voice_recording_blocks(plan),
            'author_session': read_json(self.ctx.work_dir / 'episode-session.json', {}),
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

    def status(self, payload):
        from novel_manga.application.repair import history
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        counts = history.generation_counts(self.ctx.episode_dir)
        return {'ok': True, 'execution': read_json(self.ctx.run_dir / 'execution.json', {}),
                'adopted': bool(plan.get('clips')), 'clip_count': len(plan.get('clips', [])),
                'can_adopt_draft': not self._main_media_started(),
                'adopt_draft_command': f'render {self._draft_file()} before main video starts; existing history is retained',
                'generation_counts': counts,
                'voice_recording_blocked': self._voice_recording_blocks(plan),
                'remaining_takes': {c['clip_id']: max(0, history.generation_limit(self.ctx.episode_dir, c['clip_id'])
                    - counts.get(c['clip_id'], 0)) for c in plan.get('clips', [])},
                'media': self._media_summary(read_json(self.ctx.episode_dir / 'thin_media_report.json', {}))}
