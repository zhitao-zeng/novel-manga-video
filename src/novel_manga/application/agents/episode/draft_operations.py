"""DraftOperations: existing episode operations with explicit service dependencies."""
from __future__ import annotations

import copy

from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError
from novel_manga.application.production.artifacts import media_started, archive_adoption


from .tool_inputs import input_file, draft_file


class DraftOperations:
    def __init__(self, ctx):
        self.ctx = ctx

    def validate(self, draft):
        if self.ctx.input_mode == 'script':
            return self.validate_requests(draft)
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
    def check_existing_request(clip, prompt):
        from .contracts import validate_existing_request
        validate_existing_request(clip, prompt)


    def validate_requests(self, draft):
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
                self.check_existing_request(clip, prompt)
                clip['prompt_h3'] = prompt.strip()
                clip.pop('prompt_h3_skip', None)
                clip['prompt_h3_of'] = h3_stamp(clip, notes.get(cid, ''))
            except (ValueError, KeyError, TypeError) as error:
                issues.append(f'{cid}: {error}')
        return issues, script if not issues else None, candidate if not issues else None


    def edit_prompt(self, clip, row):
        from .requests import compose_request, compose_repair
        from .contracts import validate_existing_request, validate_patch
        pending=read_json(self.ctx.run_dir/'prepared.json',{}).get('native_preparation_pending')
        if pending and not media_started(self.ctx.episode_dir):
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
        draft = input_file(self.ctx, payload, draft_file(self.ctx))
        if isinstance(draft, dict) and isinstance(draft.get('decisions'), list):
            from .input_contracts import normalize_arguments
            from .requests import compose_repair
            from .contracts import validate_patch
            clips = {c['clip_id']: c for c in read_json(self.ctx.episode_dir/'clip_plan.json', {}).get('clips', [])}
            provisional = (self.ctx.input_mode == 'source' and not (self.ctx.run_dir/'prepared.json').exists()
                           and not media_started(self.ctx.episode_dir))
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
                    if action in {'repair_assets','repack'}:
                        if len(draft['decisions'])!=1:raise RepairArgumentError('submit one upstream operation separately')
                        if action=='repair_assets':
                            from .upstream_assets import validate
                        else:
                            from .scoped_repack import validate
                        validate(self.ctx,row)
                        continue
                    if not provisional:
                        from .arguments import first_pass_scope,spent_video_budget
                        first_pass_scope(self.ctx,[cid])
                        if action in {'edit','retake','input','accept_risk'}:
                            spent=spent_video_budget(self.ctx,[cid])
                            if spent:
                                from .bounded_recovery import input_operation
                                from .repair_targets import verified_input
                                if action!='input' or not(input_operation(self.ctx,cid,row)['allowed']
                                        or verified_input(self.ctx,normalize_arguments(row))):
                                    raise RepairArgumentError(spent[cid])
                    if action in {'replan','recheck_input'}:
                        from novel_manga.application.agents.episode.pipeline_preparation import validate_editorial_request
                        validate_editorial_request(self.ctx,row)
                    elif provisional:
                        from .draft_assets import validate_request
                        if action != 'input':
                            raise RepairArgumentError('edit the provisional draft directly; apply accepts asset input requests before adoption')
                        validate_request(self.ctx, row)
                    elif action == 'input':
                        from .repair_targets import validate_target
                        validate_target(self.ctx,normalize_arguments(row))
                        from .input_repair import repair
                        repair(self.ctx,row,check_only=True)
                    elif action == 'accept_risk':
                        from .repair_targets import validate_target
                        validate_target(self.ctx,row)
                        from novel_manga.application.preparation.readiness import validate_render_risks
                        validate_render_risks(self.ctx.episode_dir,clips[cid],row.get('risks'),row['reason'])
                    elif action == 'edit':
                        from .repair_targets import validate_target
                        validate_target(self.ctx,row)
                        self.edit_prompt(clips[cid],row)
                    elif action == 'retake' and not clips[cid].get('prompt_h3'):
                        raise RepairArgumentError('clip has no English request; use edit to supply it before retake')
                    elif action not in {'keep','stop','retake','accept_risk'}:
                        raise RepairArgumentError('choose input, edit, retake, keep, accept_risk or stop')
                    elif action in {'keep','stop','retake'}:
                        from .repair_targets import validate_target
                        validate_target(self.ctx,row)
                except (ValueError, KeyError, TypeError, OSError) as error:
                    errors[str(cid or number)] = str(error)
            return {'ok': not errors, 'valid': not errors, 'kind': 'repair_arguments',
                    'argument_errors': errors, 'requires_input_preflight': True,
                    'next': 'Fix argument errors here; apply performs source checks and publishing, render performs actual-reference preflight.'}
        issues, script, plan = self.validate(draft)
        clips = (plan or {}).get('clips', [])
        return {'ok': not issues, 'valid': not issues, 'errors': issues,
                'style_warnings': {c['clip_id']: advice for c in clips
                    if (advice := positive_direction_issues(c.get('prompt_h3', '')))},
                'clip_count': len(clips),
                'requested_seconds': sum(c['request_seconds'] for c in clips if c.get('kind') == 'video')}


    def adopt(self, payload):
        """Accept a validated draft for input repair without submitting any media."""
        from novel_manga.application.profiles import h3_stamp
        from novel_manga.application.repair import history
        if media_started(self.ctx.episode_dir):
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
        draft = input_file(self.ctx, payload, draft_file(self.ctx))
        issues, script, plan = self.validate(draft)
        if issues:
            return {'ok': False, 'valid': False, 'errors': issues}
        if path.is_file():
            archive_adoption(self.ctx.episode_dir, self.ctx.run_dir)
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
