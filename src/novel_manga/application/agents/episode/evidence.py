"""EpisodeEvidence: existing episode operations with explicit service dependencies."""
from __future__ import annotations

from pathlib import Path
import re
import shutil

from novel_manga.util import atomic_write_json, read_json


from .tool_state import record_state


class EpisodeEvidence:
    def __init__(self, ctx):
        self.ctx = ctx

    @staticmethod
    def clip_technical_issues(report):
        assembly = report.get('assembly') or {}
        if assembly.get('thin_passed') is not False:
            return {}
        checks = (assembly.get('media_qc') or {}).get('checks') or {}
        return {name: value for name, value in checks.items()
                if name in {'black_frames', 'long_freeze', 'long_silence', 'silence_ratio'}
                and value.get('passed') is False}


    @staticmethod
    def media_summary(report):
        assembly = report.get('assembly') or {}
        quality = report.get('quality_review') or {}
        return {**{key: report.get(key) for key in
                ('status', 'failed_clips', 'gate_failed_clips', 'blocked_clips', 'elapsed_seconds')},
                'technical_passed': assembly.get('thin_passed'),
                'content_passed': quality.get('content_passed'),
                'technical_issues': EpisodeEvidence.clip_technical_issues(report),
                'timeline': assembly.get('timeline', []),
                'assembly_pending': report.get('status') == 'clips_ready',
                'quality_passed': quality.get('passed'), 'movie': assembly.get('final_video')}


    @staticmethod
    def unreviewed_clips(report, review):
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


    def current_review(self, review):
        from novel_manga.application.review.evidence import review_mode
        from novel_manga.review.contracts import current_review
        return current_review(review, review_mode(self.ctx.episode_dir/'work/review/current'))


    def voice_recording_blocks(self, plan):
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


    def unfilmed_clips(self, plan, report):
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


    def review_evidence(self, review, report, *, pending_only=False):
        from novel_manga.application.repair import agent, history
        from novel_manga.application.review.execution import media_matches
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json')
        selected = {r['clip_id']: r.get('selected') or {} for r in report.get('clips', [])}
        current_review = self.current_review(review)
        current = (current_review and media_matches(plan, report,
            notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})))
        unreviewed = set(self.unreviewed_clips(report, review))
        failed = (set(review.get('feedback') or {}) | set(report.get('failed_clips') or [])
                  | set(report.get('gate_failed_clips') or []) | set(report.get('blocked_clips') or {}))
        publication = []
        if current_review and review.get('review_mode') == 'verify' and (report.get('assembly') or {}).get('pending_publish'):
            from novel_manga.application.repair.delivery import precise_review_blockers
            from novel_manga.application.review.store import current_takes
            publication = precise_review_blockers(review, current_takes(self.ctx.episode_dir, plan, review),
                [c['clip_id'] for c in plan['clips'] if c.get('kind') == 'video'])
            failed.update(publication)
        technical = self.clip_technical_issues(report) if current else {}
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
        record_state(self.ctx, 'reviewed', feedback_clips=sorted(evidence['feedback']))
        return {'ok': True, 'artifact': 'input/latest-review.json', 'evidence': 'input/latest-review.json', 'clips': summaries,
                'review_required': not current_review or bool(unreviewed),
                'retained_clips': evidence['retained_clips'], 'attention_clips': evidence['attention_clips'],
                'feedback': evidence['feedback'], 'generation_counts': evidence['generation_counts']}


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
