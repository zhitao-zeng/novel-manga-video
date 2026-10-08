"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import os
from pathlib import Path
import shutil
import threading
import time

from novel_manga.application.agents.sandbox import load_config
from novel_manga.application.configuration import environment, repair_environment, repair_settings, RuntimePaths
from novel_manga.llm.config import ENDPOINTS
from novel_manga.util import atomic_write_json, read_json

from .bridge import EpisodeBridge, install_client
from .inputs import prepare_inputs
from .session import SESSION_FILE, run_session
from .tools import EpisodeTools
from novel_manga.application.production.artifacts import media_started

MAX_INVOCATIONS = 3
DEFAULT_OPERATION_SECONDS = 5400
DEFAULT_REPAIR_TURN_SECONDS = 600
DEFAULT_SOURCE_TURN_SECONDS = 1200


@contextmanager
def process_environment(values):
    """This sequential command owns its environment until its host jobs have settled."""
    before = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def runtime_environment(ctx, config):
    env = environment(ctx.project_root)
    pipeline = read_json(ctx.project_root / 'configs/pipeline.json', {})
    review_mode = str(env.get('NOVEL_REVIEW_MODE') or '').strip()
    if not review_mode:
        profile = read_json(ctx.novel_dir/'profile.json', None)
        if profile is None:
            profile = read_json(ctx.source_novel_dir/'profile.json', None)
        if profile is None:
            metadata = read_json(ctx.novel_dir/'novel.json', None)
            if metadata is None:
                metadata = read_json(ctx.source_novel_dir/'novel.json', {})
            profile = metadata.get('profile', {})
        review_mode = str(profile.get('review_mode') or repair_settings(ctx.project_root)['review_mode'])
    if not env.get('NOVEL_LOCAL_H3_URL', '').strip():
        env, settings = repair_environment(ctx.project_root, ctx.run_dir)
        # The pool belongs to the service, not this episode's sandbox directory.
        key = next((row for row in pipeline.get('resources', {}).get('video_keys', {}).values()
                    if row.get('pool') == settings['pool']), {})
        env['NOVEL_INFLIGHT_DIR'] = str(key.get('inflight_dir') or
            RuntimePaths(ctx.project_root).temporary / 'inflight' / settings['pool'])
        env.setdefault('NOVEL_RENDER_INFLIGHT', str((key.get('inflight') or {}).get('max', settings['inflight'])))
    elif not env.get('NOVEL_RENDER_INFLIGHT'):
        key = next((row for row in pipeline.get('resources', {}).get('video_keys', {}).values()
                    if row.get('base_url') == env['NOVEL_LOCAL_H3_URL']), {})
        env['NOVEL_RENDER_INFLIGHT'] = str((key.get('inflight') or {}).get('max', 24))
        env.setdefault('NOVEL_INFLIGHT_DIR', str(key.get('inflight_dir') or
            RuntimePaths(ctx.project_root).temporary / 'inflight' / (key.get('pool') or 'h3pool')))
    # Review policy follows configuration even when the H3 endpoint is explicit.
    env['NOVEL_REVIEW_MODE'] = review_mode
    from novel_manga.application.configuration import model_resources_environment
    for name, value in model_resources_environment(ctx.project_root, pipeline).items():
        env.setdefault(name, value)
    base = config['base_url'].rstrip('/')
    if not base.endswith('/v1'):
        base += '/v1'
    defaults = {'QWEN38_LOCAL_BASE_URL': base, 'QWEN38_LOCAL_MODEL': config['model'],
                'QWEN38_LOCAL_API_KEY_VAR': config['key_var'], 'QWEN38_LOCAL_STREAM': '0'}
    for key, value in defaults.items():
        env[key] = os.environ.get(key) or value
    preset = next((name for name, values in ENDPOINTS.items()
                   if all(values.get(key) == env[key] for key in
                          ('QWEN38_LOCAL_BASE_URL', 'QWEN38_LOCAL_MODEL',
                           'QWEN38_LOCAL_API_KEY_VAR'))), None)
    preset = preset or os.environ.get('SECOND_REVIEW_JUDGE') or os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE')
    if preset is None:
        raise ValueError('The configured sandbox endpoint has no existing QA preset; '
                         'set SECOND_REVIEW_JUDGE and NOVEL_REVIEW_VIDEO_JUDGE to existing presets.')
    from novel_manga.llm.config import TRANSPORT_ENV
    transport = ENDPOINTS.get(preset, {})
    env.update({name:transport[name] for name in TRANSPORT_ENV if name in transport})
    env['SECOND_REVIEW_JUDGE'] = os.environ.get('SECOND_REVIEW_JUDGE') or preset
    env['NOVEL_REVIEW_VIDEO_JUDGE'] = os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE') or preset
    from novel_manga.llm.config import endpoint_settings
    resources = None
    for field, variable in (('h3_translation_endpoint', 'NOVEL_H3_TRANSLATION_ENDPOINT'),
                            ('h3_request_check_endpoint', 'NOVEL_H3_REQUEST_CHECK_ENDPOINT')):
        selected = str(config.get(field, env.get(variable)) or '').strip()
        if field in config or variable in env:
            env[variable] = selected
        if not selected:
            continue
        endpoint = endpoint_settings(selected)
        # A preparation role using the primary endpoint retains its existing
        # shared capacity; only the separate helper model gets this new cap.
        if base.removesuffix('/v1') in {value.rstrip('/').removesuffix('/v1') for value in endpoint.endpoints}:
            continue
        slots = int(config.get('h3_preparation_parallel', 2))
        if slots <= 0:
            raise ValueError('h3_preparation_parallel must be positive')
        if resources is None:
            resources = json.loads(env.get('NOVEL_MODEL_RESOURCES') or '[]')
        target = ','.join(endpoint.endpoints)
        existing = next((row for row in resources if row['base'].rstrip('/') == target.rstrip('/')), None)
        if existing is None:
            resources.append({'base': target, 'slots': slots})
        else:
            existing['slots'] = min(int(existing['slots']), slots)
    if resources is not None:
        env['NOVEL_MODEL_RESOURCES'] = json.dumps(resources, separators=(',', ':'))
    env['NOVEL_PROJECT_ROOT'] = str(ctx.project_root)
    env['NOVEL_CLIP_SECONDS_MAX'] = str(ctx.clip_seconds_max)
    return env


def initial_prompt(ctx):
    drafted = ((ctx.work_dir/'output/episode.json').is_file()
               and read_json(ctx.run_dir/'source-draft-call.json', {}).get('status') == 'candidate')
    common = ((
        'Use the supplied check results and existing production draft. Consult the relevant '
        'episode-production contract sections when a repair requires them. '
        'Inspect actual reference images relevant to the shots you change. ' if drafted else
        'Read input/manifest.json and the episode-production skill/output contract before writing requests. '
        'Inspect the actual reference images you select. ') +
        'Prefer structured H3 shot bodies: the host composes all fixed syntax. '
        'Use the retained production profile, '
        'speech rules, source evidence and remaining generation budgets. '
        'Commands are python3 /work/tools/episode.py check, render, apply, review, finish. '
        'check/render take an optional file; apply defaults to output/repair.json. '
        'Use doctor to request host service diagnostics when an endpoint fails; '
        'yield for its result like other host jobs. Diagnostics do not prove content acceptance. '
        'Use these commands to check, render, review, apply supported repairs, '
        'and finish this episode. When a tool returns yield=true, end this CLI turn immediately. '
        'The host will execute it and resume this same session with the result; it owns waiting. '
        'The current host state and previous operation result are supplied below; do not query '
        'status again unless you need a later state. Render validates your draft and returns '
        'the existing independent review with its evidence; use check for draft diagnostics, '
        'and review when evidence is missing/stale or full media context is needed. '
        'Automatic render evidence expands attention_clips only; retained_clips already '
        'pass current checks and need no repair decision. If episode_technical_issues is present, locate the responsible '
        'clips and repair only those; the technical inspection targets do not all need keep decisions. '
        'Use the complete screenplay '
        'and all-clip summary for adjacent context; explicit review exports all clips. '
        'When render returns clips_ready with assembly_pending=true, use its clip evidence '
        'to repair or finish; whole-episode encoding waits for repairs, so a missing movie '
        'alone is not a reason to render again. Finish assembles the final candidate and '
        'runs technical QC; content_passed alone does not mean final acceptance. '
        'If a prepared plan or previous takes exist, reuse that plan and media; '
        'Follow status.retry_policy and remaining_takes: once an allowed correction or recheck '
        'is exhausted, stop that clip and finish with the actual unresolved result; do not loop '
        'on the same rejection or reopen a finished episode. '
        'Use remaining_input_attempts, remaining_rechecks, conditional_extra_takes and input_repairs for current permissions; '
        'old blocked messages are history. Historical tool_error records do not spend input attempts. '
        'remaining_takes includes unspent conditional extras, which require a validated repair and input preflight before generation. '
        'Review the current evidence first: keep requests an independent recheck if a verdict may be wrong. '
        'For a real defect, submit a concrete edit/input/retake; an extra take activates only after actual-input preflight. '
        'Zero new input attempts can still allow an explicitly offered continuation or one revision. '
        'For argument_errors, correct the named JSON fields using the returned shape/scope; '
        'resuming or rendering cannot fix malformed arguments. Keep existing revision and take limits. '
        'Batch related file reads and independent image inspections. Save the draft before testing. '
        'Use one check for all clips, fix reported issues together, then submit render. '
        'Do not probe the checker with many alternate phrasings: use structured h3.shots and '
        '<DIALOGUE_n> slots so the host supplies protected bindings and timing. '
        'review and apply supported repairs instead of resubmitting a whole episode file. '
        'Read the actual latest frames, raw/corrected ASR and '
        'independent verdicts. Finish with the actual accepted or blocked outcome.\n'
    )
    if ctx.input_mode == 'source' and (ctx.work_dir/'input/reading/character-phases.json').is_file():
        common += ('Use input/reading/character-phases.json for current chapter appearance and clothing; '
                   'these explicit phase fields override the base Bible look. Inspect the corresponding '
                   'selected cards rather than reverting a current phase to its older base design.\n')
    if ctx.input_mode == 'script':
        from .local_preparation import active
        if active(ctx):
            return common + (
                'This is an existing provisional screenplay with a local preparation failure. '
                'Read input/preparation/exception.json and input/latest-preparation.json when present. '
                'Keep its cuts, durations, dialogue and valid choices. Do not write a new episode.json. '
                'The host stages the existing inputs without video; use apply action=input for '
                'source-backed speaker, scene, character/reference and wearing corrections, '
                'action=edit for one invalid English request, or accept_risk for an explicitly '
                'offered reference uncertainty. Input may build needed scene/character cards. '
                'render rechecks the current source, fills missing references and translates only '
                'missing/invalid requests before normal H3 and media gates. Old preliminary findings '
                'are evidence, not proof of a current defect. Use current results and finish normally.\n')
        return common + (
            'This is script input mode. The existing approved chapter_script.json, clip_plan.json '
            'and selected assets are authoritative. Preserve every shot, story event, exact dialogue '
            'and speaker, timing, camera, reference selection/order and source binding. '
            'Keep these accepted choices except for a concrete source/input defect corrected through the input tool. '
            'Do not replan the whole episode or impose a new episode length. '
            'For a confirmed source/input conflict, use apply with action=input, a verbatim source_quote '
            'and supported cast/speaker/scene/appearance/wearing/sound corrections. '
            'shots can correct in_frame/extras/listeners/actions, event/start_state/end_state, '
            'speaker_map and sfx. characters requests a missing source actor card after adoption too. '
            'The host checks source support, builds scoped cards and recompiles dependent clips retaining cuts and words. '
            'output/requests.json is already seeded with the existing final requests. '
            'Read manifest.request_draft: reusable lists structurally valid requests and needs_work '
            'names missing or invalid clips. Preserve reusable text unless you find a concrete '
            'source, image or request defect. Edit only those defects and missing requests; '
            'style_warnings alone do not require a rewrite. Use the host check diagnostics '
            'instead of writing duplicate validators or shell probes. Perform your semantic/image '
            'review, and execute the existing episode through the supplied tools. '
            'Keep repairs within the existing protected request contract.\n'
        )
    if (ctx.run_dir/'prepared.json').is_file():
        return common + (
            'This source episode already has an adopted plan. You are handling an exception '
            'in that existing episode. Read the current status and '
            'input/latest-render.json and input/latest-review.json when present; they describe '
            'the current requests and actual takes. Do not redo initial source drafting or '
            'treat an old source-draft-check as a new failure. Preserve existing media and its '
            'physical-take budget. If can_adopt_draft is false, use apply for remaining issues '
            'instead of rewriting the screenplay or resubmitting the initial draft. '
            'Use source evidence for cast/speaker/appearance/wardrobe/scene/sound corrections. '
            'Input can correct references and request missing source actor cards with characters. '
            'Use shots.in_frame/extras/listeners/actions, speaker_map and sfx as needed; '
            'unnamed crowds remain extras unless an actual speech owner requires a card. '
            'accept_risk can explicitly acknowledge a '
            'current wearer-card or scene-people uncertainty for one clip before rendering; '
            'it does not pass any media gate. keep is only for an actual existing video.\n'
        )
    if read_json(ctx.work_dir/'input/manifest.json', {}).get('preparation_exception'):
        return common + (
            'This unfilmed chapter failed normal planning or preparation. Read '
            'input/preparation/exception.json and its listed original candidate and audit files. '
            'The failed screenplay is provisional, not an approved frozen script. Diagnose and '
            'repair the recorded cause, preserving its valid story choices instead of repeating '
            'the normal writer. Use input/reading/locations.json when present: these names and '
            'chapter memberships come from the book reading; their descriptions are evidence, '
            'not ready-made empty-scene prompts. A missing picture is work to perform: write '
            'the corrected provisional output/episode.json and request source-backed missing '
            'characters/locations through apply action=input using the draft-assets contract. '
            'Do not substitute an unrelated available scene or remove a required source actor '
            'to avoid creating a reference. Use the existing source output contract, check and '
            'render; the host retains source, reference, request and media acceptance gates.\n')
    if (ctx.run_dir/'source-draft-call.json').is_file():
        return common + (
            'This is source input mode with a normal production draft already attempted. '
            'You are handling an exception. Read input/source-draft-check.json when present '
            'and output/episode.json; correct the reported issues while keeping valid story '
            'choices. The draft is provisional: you may repair its screenplay, references, '
            'cutting and H3 bodies against the complete original source and actual assets. '
            'input/source-draft-raw.json, when present, preserves the normal writer response '
            'as ordered clip/shot beats; reuse its valid choices if host binding failed. '
            'If the model call produced no usable draft, author it from the supplied source. '
            'Preserve essential causes, dialogue ownership and the ending within the manifest '
            'duration target. Use the existing source output contract and execute the tools.\n'
        )
    target = ctx.target_seconds
    duration = (f'{target[0]:g}–{target[1]:g} seconds' if target else
                'the duration specified in input/manifest.json')
    return common + (
        'This is source input mode. Read the complete original chapter. Create the episode '
        'screenplay, ordered shots and English shot directions yourself from that source and '
        f'the supplied assets. Aim for {duration}, with each clip no longer than '
        f'{ctx.clip_seconds_max:g} seconds. Preserve source-to-shot evidence and meaningful '
        'adaptation choices. Prefer clips[].h3={shots:[English body with <DIALOGUE_n> slots],soundscape:English}. '
        'The host fills reference definitions, dialogue, voice IDs, durations and cuts. '
        'Write output/episode.json using the output contract, self-review '
        'the complete episode, and execute it through the supplied tools.\n'
    )


def continuation_prompt():
    return (
        'Continue this same saved episode session and existing workflow. The previous CLI turn '
        'ended and the host operation has now settled. The current host state and operation '
        'result are supplied below; continue directly from them. Read the saved evidence '
        'when making a creative or repair decision. Render already exports its independent '
        'review evidence; call review again only for missing/stale evidence or when full media context is needed. '
        'Current repair API: an edit may use h3={shots:[English bodies with <DIALOGUE_n> slots], '
        'soundscape:English} instead of prompt_h3. Prefer this short form; the host preserves '
        'reference bindings, original speech, durations, cuts and music. Read only the evidence '
        'needed for the current unresolved clips. Use attention_clips for repair decisions; '
        'retained_clips need no decision or repeated inspection. '
        'For episode_technical_issues, use the inspection targets to locate and repair only responsible clips; '
        'do not submit keep decisions merely to cover all technical inspection targets. '
        'Batch independent decisions in one apply. A clip with no remaining take allowance '
        'does not cancel eligible repairs for other clips. '
        'Current remaining_input_attempts, remaining_rechecks, conditional_extra_takes and input_repairs override historical blocked messages. '
        'repair_actions lists apply operations permitted NOW, including edit/retake/input preparation for a pending extra. '
        'Submit a listed supported correction; the host handles later activation. A missing planned line can be repaired '
        'with edit or a justified retake while preserving its words; it does not require a source rewrite. '
        'A previous stopped result is not acceptance and does not revoke a current allowance. '
        'Use current repair_capabilities: submit a supported correction/resume, or identify the exact '
        'missing capability, rejected source decision, exhausted allowance or external dependency. '
        'Do not repeat the old completion narrative instead of an operation. '
        'remaining_takes includes unspent conditional extras; prepare a validated repair before those can generate. '
        'Inspect current evidence; use keep for an independent recheck when a verdict is doubtful, '
        'otherwise submit a concrete edit/input/retake. Extra generation activates only after input preflight; '
        'risk acceptance alone is not a repair plan. First run python3 /work/tools/episode.py check output/repair.json to catch '
        'argument errors in this turn without spending an input attempt or handing off to a slow tool. '
        'A historical tool_error did not spend an input attempt. Use an offered continuation or revision '
        'when available; do not repeat a missing or malformed saved decision through resume. '
        'For an already registered character state, inspect input/phases.json and its actual card; '
        'input phases.characters.<name>={reuse_asset_id:<existing phase asset ID>} reuses that saved design. '
        'Do not rewrite appearance or wardrobe merely to apply the same state to another clip. '
        'After adoption, action=input supports scoped shots.in_frame/extras/listeners/actions and '
        'event/start_state/end_state/sfx corrections, plus speaker_map from old to source-correct speech owners. '
        'characters can request a missing named visible actor or actual speech owner with appearance/wardrobe. '
        'For source-authored speech_overflow, input shots[].turn_texts may shorten each existing turn in order; '
        'the host preserves the speaker/mode and independently checks essential meaning against the source. '
        'Aim for 4–5 Chinese characters per second, never more than 6. This permission does not apply to frozen script input. '
        'A character missing from the old selected references is not prohibited when corrected shots '
        'and original source establish that identity. Existing cards use phases for changed clothing. '
        'Do not put a missing identity into phases; request characters instead. Unpictured crowds can remain extras. '
        'Before source adoption, missing assets can be requested with apply action=input, '
        'a verbatim source_quote, and characters (name to appearance/wardrobe/gender/age) '
        'or locations (name to empty-setting description). Read the returned actual images '
        'and bind their IDs in the provisional draft; asset creation does not start video. '
        'When a tool returns yield=true, end this turn so the host can execute it. '
        'Complete the remaining checks, review or supported repair within the existing budget '
        'and input authority. Run finish when accepted or when the episode must stop, and '
        'end this turn after its acceptance; the host will complete it.\n'
    )


def _pending_input_continuations(ctx):
    """Continue already-approved interrupted work once, before opening another CLI."""
    from .input_repair import effects, repair_status
    from .input_records import latest_input_records, source_review_recoverable
    available = repair_status(ctx)
    script = read_json(ctx.episode_dir/'chapter_script.json', {})
    shots = {s.get('index', i): s for i, s in enumerate(script.get('shots', []), 1)}
    decisions, seen = [], []
    first_pending = None
    if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
        from novel_manga.application.preparation.store import unfilmed_video_clips
        first_pending = unfilmed_video_clips(ctx.episode_dir)
    for path, record in latest_input_records(ctx.episode_dir).values():
        if first_pending is not None and record.get('clip_id') not in first_pending:
            continue
        resume = available.get(record.get('clip_id'), {}).get('resume_action')
        timing_fix = (ctx.input_mode == 'source' and record.get('step') == 'compile'
                      and 'input repair changed protected timing or shot addresses' in str(record.get('error') or ''))
        if not resume:
            continue
        reserved_source = record.get('step') == 'source' and bool(record.get('previous_source_review'))
        if not (source_review_recoverable(record) or reserved_source) and (
                (record.get('source') or {}).get('consistent') is not True or not (
                    record.get('step') == 'assets'
                    or str(record.get('step') or '').startswith(('translate:', 'check:')) or timing_fix)):
            continue
        decision = copy.deepcopy(record.get('decision') or {})
        patches = [{'index': p['index'], **{k:v for k,v in p.items() if k != 'index'
                    and shots.get(p['index'], {}).get(k) != v}} for p in decision.get('shots') or []]
        decision['shots'] = [p for p in patches if len(p) > 1]
        change = effects(decision)
        if change in seen:
            continue
        decisions.append(resume)
        seen.append(change)
    if decisions:
        atomic_write_json(ctx.work_dir/'output/resume-inputs.json', {'decisions': decisions})
    return bool(decisions)


def _stale_request_blocks(ctx):
    from novel_manga.application.preparation.request_check import POLICY
    report = read_json(ctx.episode_dir/'thin_media_report.json', {})
    for cid, problems in (report.get('blocked_clips') or {}).items():
        if not any(str(p).startswith('request:') for p in problems):
            continue
        saved = read_json(ctx.episode_dir/f'work/request_checks/{cid}.json', {})
        if ((saved.get('inputs') or {}).get('policy') not in (None, POLICY)
                and (saved.get('answer') or {}).get('consistent') is False):
            return True
    return False


def _pending_structured_repairs(ctx):
    """Reuse a saved proposal rejected solely by the former missing-field bug."""
    from .requests import compose_repair
    from .contracts import validate_patch
    rounds = read_json(ctx.run_dir/'tool-repair-decisions.json', [])
    if not rounds:
        return False
    rejected = {cid for cid, reason in rounds[-1].get('blocked', {}).items() if reason == "'prompt_h3'"}
    if not rejected:
        return False
    source = ctx.work_dir/'output/repair.json'
    draft = read_json(source, {})
    if not isinstance(draft, dict) or not isinstance(draft.get('decisions'), list):
        return False  # Let the Agent correct its saved draft; never replay malformed output.
    clips = {c['clip_id']: c for c in read_json(ctx.episode_dir/'clip_plan.json', {}).get('clips', [])}
    evidence = {r['clip']['clip_id']: r for r in read_json(ctx.work_dir/'input/latest-review.json', {}).get('targets', [])}
    media = read_json(ctx.episode_dir/'thin_media_report.json', {})
    takes = {r['clip_id']: (r.get('selected') or {}).get('video') for r in media.get('clips', [])}
    selected = []
    for row in draft['decisions']:
        cid = row.get('clip_id')
        old = evidence.get(cid, {})
        if (cid not in rejected or row.get('action') != 'edit' or not isinstance(row.get('h3'), dict)
                or cid not in clips or old.get('clip') != clips[cid] or old.get('input_block')):
            continue
        video = Path(takes[cid]) if takes.get(cid) else None
        if video and video.exists() and video.stat().st_mtime_ns > source.stat().st_mtime_ns:
            continue  # the saved proposal predates the current take
        try:
            validate_patch(clips[cid], compose_repair(clips[cid], row))
        except (ValueError, KeyError, TypeError):
            continue
        selected.append(row)
    if selected:
        atomic_write_json(ctx.work_dir/'output/resume-structured-repairs.json', {'decisions': selected})
    return bool(selected)


def _fill_missing_requests(ctx, *, timeout=300):
    """Prepare the normal model draft before asking an Agent to handle exceptions."""
    from .local_preparation import active
    if active(ctx):
        return None  # The native local preparation gate runs before video.
    if ctx.input_mode == 'source':
        if (ctx.run_dir/'prepared.json').is_file():
            return None  # current adopted requests, not a stale initial draft, own continuations
        if read_json(ctx.work_dir/'input/manifest.json', {}).get('preparation_exception'):
            return None  # The failed normal candidate already belongs to the exception Agent.
        if not read_json(ctx.work_dir/'input/manifest.json', {}).get('ready'):
            return None
        from .authoring import propose
        result = propose(ctx, timeout=timeout)
        if (ctx.work_dir/'output/episode.json').is_file():
            checked = EpisodeTools(ctx).dispatch('check', {})
        else:
            checked = {'ok': False, 'valid': False, 'error': result.get('error', 'source draft unavailable')}
        atomic_write_json(ctx.work_dir/'input/source-draft-check.json', checked)
        return checked
    if ctx.input_mode != 'script' or (ctx.work_dir / SESSION_FILE).exists():
        return
    from .contracts import validate_existing_request
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.story.h3 import positive_direction_issues
    path = ctx.work_dir / 'output/requests.json'
    draft = read_json(path, {})
    info_path = ctx.work_dir / 'input/request-draft.json'
    info = read_json(info_path, {})
    if not info or not isinstance(draft.get('prompts'), dict):
        return
    plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    targets = [c for c in plan.get('clips', []) if c.get('kind') == 'video'
               and not draft['prompts'].get(c['clip_id']) and not c.get('prompt_h3')
               and c['clip_id'] not in info.get('draft_attempted', [])]
    if not targets:
        return

    def one(clip):
        candidate, problems = copy.deepcopy(clip), []
        try:
            convert(candidate, tries=1, episode_dir=ctx.episode_dir, problems=problems)
            if not candidate.get('prompt_h3'):
                raise ValueError('; '.join(problems) or 'initial English draft incomplete; Agent must resolve it')
            validate_existing_request(clip, candidate['prompt_h3'])
            return candidate['prompt_h3'], None
        except (ValueError, KeyError, OSError) as error:
            return None, str(error)

    info.setdefault('draft_attempted', []).extend(c['clip_id'] for c in targets)
    atomic_write_json(info_path, info)
    with ThreadPoolExecutor(max_workers=min(2, len(targets))) as pool:
        jobs = {pool.submit(one, clip): clip['clip_id'] for clip in targets}
        for future in as_completed(jobs):
            cid = jobs[future]
            prompt, error = future.result()
            current = read_json(path)
            # A user-supplied or Agent-authored request always keeps its ownership.
            if current['prompts'].get(cid):
                continue
            if prompt:
                current['prompts'][cid] = prompt
                atomic_write_json(path, current)
                info['reusable'].append(cid)
                info['needs_work'].pop(cid, None)
                if advice := positive_direction_issues(prompt):
                    info['style_warnings'][cid] = advice
            else:
                info['needs_work'][cid] = error
            atomic_write_json(info_path, info)
    manifest_path = ctx.work_dir / 'input/manifest.json'
    manifest = read_json(manifest_path)
    manifest['request_draft'] = info
    atomic_write_json(manifest_path, manifest)


def _file_stamp(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_mtime_ns, info.st_size


def _progress(ctx, status):
    paths = [ctx.work_dir / name for name in (
        'output/episode.json', 'output/requests.json', 'output/repair.json',
        'input/latest-render.json', 'input/latest-review.json', 'input/latest-finish.json')]
    paths.extend(ctx.run_dir / name for name in ('prepared.json', 'tool-repair-decisions.json'))
    # These small JSON outputs carry actual progress. Equal-size edits can keep
    # the same timestamp, and touching an unchanged draft is not progress.
    outputs = []
    for path in paths:
        try:
            outputs.append(path.read_bytes())
        except FileNotFoundError:
            outputs.append(None)
    job = status.get('job') or {}
    return tuple(outputs), job.get('id'), job.get('status')


def _made_progress(ctx, before, before_status, status):
    """New job IDs and reworded rejected proposals are not productive work."""
    job = status.get('job') or {}
    prior = before_status.get('job') or {}
    outcome = job.get('result') or {}
    if job.get('id') != prior.get('id') and job.get('status') in {'completed', 'failed'}:
        if (outcome.get('changed') or outcome.get('assets_added')
                or set(outcome.get('input_repaired') or []) - set(outcome.get('input_reused') or [])):
            return True
        if set(outcome.get('keep_checks') or []) & set(outcome.get('accepted') or []):
            return True
        if outcome.get('argument_errors'):
            return False
        if job.get('status') == 'failed' or outcome.get('ok') is False:
            # A new failure explanation is evidence, not a repaired input.
            # Partial published changes were handled above, before this branch.
            return False
        if job.get('action') == 'apply' and 'changed' in outcome:
            return False
        if job.get('action') == 'doctor':
            return before[0] != _progress(ctx, status)[0]
        if job.get('action') == 'review':
            return before[0] != _progress(ctx, status)[0] or outcome != (prior.get('result') or {})
        if job.get('action') == 'render' and 'generation_counts' in before_status and 'generation_counts' in status:
            def media_state(row):
                media = row.get('media') or {}
                return (row.get('adopted'), row['generation_counts'],
                        tuple((key, media.get(key)) for key in
                              ('status', 'quality_passed', 'technical_passed', 'failed_clips',
                               'gate_failed_clips', 'movie')),
                        sorted(media.get('blocked_clips') or {}))
            return media_state(before_status) != media_state(status)
    return before != _progress(ctx, status)


def stop_details(status):
    """Retain the last concrete obstacle instead of reporting only the loop guard."""
    job = status.get('job') or {}
    outcome = job.get('result') or {}
    repairs = status.get('input_repairs') or {}
    states = {r.get('status') for r in repairs.values()}
    kind = ('argument_error' if outcome.get('argument_errors') else
            'tool_error' if 'tool_error' in states else
            'source_rejected' if 'rejected' in states else
            'input_repair_incomplete' if states & {'checking', 'incomplete', 'published'} else
            'no_effective_operation')
    return {'kind': kind, 'last_operation': job.get('action'),
            'blocked': outcome.get('blocked') or {}, 'input_repairs': repairs,
            'remaining_takes': status.get('remaining_takes') or {},
            'remaining_rechecks': status.get('remaining_rechecks') or {}}


def _wait_for_idle(bridge):
    status = bridge.handle('status', {})
    next_update = time.monotonic() + 30
    while status.get('busy'):
        if time.monotonic() >= next_update:
            job = status.get('job') or {}
            print(f"Waiting for episode operation {job.get('id')}: {job.get('action')}", flush=True)
            next_update = time.monotonic() + 30
        time.sleep(0.2)
        status = bridge.handle('status', {})
    return status


def _paused():
    path = os.environ.get('NOVEL_AGENT_PAUSE_FILE')
    return bool(path and Path(path).exists())


@contextmanager
def _episode_lock(ctx):
    """Use the production episode marker so a second executor cannot claim it."""
    from novel_manga.application.production.common import pid_alive
    path = ctx.source_episode_dir / '.render.lock'
    ctx.source_episode_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            owner = int(path.read_text().strip() or 0)
        except ValueError:
            owner = 0
        if owner and pid_alive(owner):
            raise RuntimeError(f'episode is already owned by pid {owner}')
        path.unlink(missing_ok=True)
    with path.open('x') as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def _current_finish(ctx, previous_stamp, job):
    receipt = (job or {}).get('result') or {}
    if ((job or {}).get('action') != 'finish' or (job or {}).get('status') != 'completed'
            or type(receipt.get('passed')) is not bool):
        return None
    path = ctx.work_dir / 'input/latest-finish.json'
    stamp = _file_stamp(path)
    if stamp is None or stamp == previous_stamp:
        return None
    manifest = read_json(path, None)
    if not isinstance(manifest, dict):
        return None
    current = read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id')
    if not current or (manifest.get('author_session') or {}).get('session_id') != current:
        return None
    if manifest.get('passed') is not receipt['passed']:
        return None
    return manifest


def _record_finish(result, manifest):
    passed = manifest.get('passed') is True
    result.update(status='finished' if passed else 'finished_with_issues', phase='finished',
                  passed=passed, exit_code=0 if passed else 1, finish=manifest, reason=None)


def _applied_repair(job):
    return (job.get('action') == 'apply' and job.get('status') == 'completed'
            and bool((job.get('result') or {}).get('changed')
                     or set((job.get('result') or {}).get('input_repaired') or [])
                     - set((job.get('result') or {}).get('input_reused') or [])))


def read_status(ctx):
    """Read a prepared or running episode; never start a model, bridge or media task."""
    return {'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
            'run_dir': str(ctx.run_dir), 'execution': read_json(ctx.run_dir / 'execution.json', {}),
            'agent_run': read_json(ctx.run_dir / 'agent-run.json', {}),
            'session': read_json(ctx.work_dir / SESSION_FILE, {}),
            'finish': read_json(ctx.work_dir / 'input/latest-finish.json', None)}


def run_episode(ctx, *, config=None, timeout=None, retry_issues=False):
    """One agent owns creative decisions; this controller only waits and resumes operations."""
    with _episode_lock(ctx):
        return _run_episode(ctx, config=config, timeout=timeout, retry_issues=retry_issues)


def _finish_existing(ctx, saved, runtime):
    """The ordinary host acceptance operation, without authoring or generation."""
    result = {**saved, 'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
              'run_dir': str(ctx.run_dir), 'pid': os.getpid(), 'passed': False,
              'status': 'running', 'phase': 'checking_existing_media', 'reason': None,
              'updated_at': time.strftime('%F %T'),
              'method': 'existing media acceptance under current book policy'}
    result.pop('finish', None)
    with process_environment(runtime):
        if _paused():
            result.update(status='paused', phase='paused', exit_code=0, reason='production dispatch paused')
        else:
            atomic_write_json(ctx.run_dir/'agent-run.json', result)
            answer = EpisodeTools(ctx).dispatch('finish', {})
            number = int(((saved.get('job') or {}).get('id') or 'job-0').rsplit('-', 1)[-1])+1
            result['job'] = {'id': f'job-{number}', 'action': 'finish', 'status': 'completed', 'result': answer}
            manifest = read_json(ctx.work_dir/'input/latest-finish.json', {})
            if answer.get('ok') and type(answer.get('passed')) is bool and manifest.get('passed') is answer['passed']:
                _record_finish(result, manifest)
            else:
                result.update(status='blocked', phase='blocked', exit_code=2,
                              reason=answer.get('error') or 'existing media acceptance incomplete')
        result['updated_at'] = time.strftime('%F %T')
        atomic_write_json(ctx.run_dir/'agent-run.json', result)
    return result


def _run_episode(ctx, *, config=None, timeout=None, retry_issues=False):
    from novel_manga.llm.resources import model_slot
    from . import recovery
    config = load_config(ctx.project_root / 'configs/agent_sandbox.json') if config is None else config
    config = {**config, **{field: os.environ[var] for field, var in
        (('base_url', 'NOVEL_AGENT_BASE_URL'), ('model', 'NOVEL_AGENT_MODEL'),
         ('key_var', 'NOVEL_AGENT_KEY_VAR')) if os.environ.get(var)}}
    seconds = int(timeout if timeout is not None else config.get('timeout_seconds', DEFAULT_OPERATION_SECONDS))
    if seconds <= 0:
        raise ValueError('timeout must be positive')
    runtime = runtime_environment(ctx, config)
    runtime['NOVEL_RESOURCE_WAIT_FILE']=str(ctx.run_dir/'resource-waits.jsonl')
    if config.get('refusal_fallback'):
        runtime['NOVEL_REFUSAL_FALLBACK'] = config['refusal_fallback'].get('endpoint', 'flashnext')
    external_pipeline = config.get('production_mode') == 'batch_review'
    native_mode = config.get('production_mode') in {'pipeline_review','batch_review'}
    production_only=native_mode and os.environ.get('NOVEL_PRODUCTION_ONLY')=='1'
    first_pass_only=native_mode and os.environ.get('NOVEL_FIRST_PASS_ONLY')=='1'
    if native_mode:
        runtime['NOVEL_AGENT_MANAGED'] = '1'
    prepare_inputs(ctx)
    if native_mode:
        manifest=read_json(ctx.work_dir/'input/manifest.json',{})
        phase=('review' if (ctx.run_dir/'pipeline-review-started.json').exists() else
               'input_repair' if (ctx.run_dir/'pipeline-input-repair-started.json').exists() else 'production')
        manifest.update(production_mode=config['production_mode'],memory_scope='repair_only',agent_phase=phase,
            authority='Fixed pipeline owns normal production. Agent repairs confirmed semantic input defects and reviews/repairs generated video.')
        atomic_write_json(ctx.work_dir/'input/manifest.json',manifest)
    from .local_preparation import stage
    stage(ctx)
    install_client(ctx.work_dir)
    saved = read_json(ctx.run_dir / 'agent-run.json', {})
    if not _paused():
        saved = recovery.resume_source_review(ctx.run_dir, ctx.episode_dir, saved) or saved
        from .input_repair import repair as validate_input
        saved = recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,saved,validate_input=validate_input) or saved
        config,saved=recovery.protocol_fallback(ctx,config,saved)
    if saved.get('model_switch') and config.get('refusal_fallback'):
        config = {**config, 'model_slots_reserved_for_tools': 0, **config['refusal_fallback']}
    if config.get('finish_existing'):
        return _finish_existing(ctx, saved, runtime)
    if retry_issues:
        recovered = recovery.resume_summary_loop(ctx, config, saved)
        if recovered is not None:
            config, saved = recovered
    if saved.get('status') == 'deferred':
        # Waiting behind other episodes consumes no active operation allowance.
        # Media, retry counters and the persisted Agent UUID are unchanged.
        saved['deadline'] = time.time() + max(0, float(saved['remaining_seconds']))
    elif saved.get('status') == 'paused' and saved.get('updated_at') and saved.get('deadline'):
        # An operator pause is idle time, just like a scheduler deferral. Older
        # paused rows may still carry remaining_seconds from a previous slice.
        stopped_at = time.mktime(time.strptime(saved['updated_at'], '%Y-%m-%d %H:%M:%S'))
        saved['deadline'] = time.time() + max(0, float(saved['deadline'])-stopped_at)
    expired_operation = (saved.get('status') in {'running', 'retrying'}
                         and float(saved.get('deadline') or 0) <= time.time())
    if retry_issues and (saved.get('status') in {'finished_with_issues', 'blocked'} or expired_operation):
        # A requested retry never renews video budgets. An interrupted author
        # can explicitly keep its session instead of rereading the same work.
        archive = ctx.run_dir/'retries'/str(time.time_ns())
        archive.mkdir(parents=True)
        atomic_write_json(archive/'agent-run.json', saved)
        session = ctx.work_dir/SESSION_FILE
        if session.is_file():
            if config.get('resume_session_on_retry'):
                shutil.copy2(session, archive/'episode-session.json')
            else:
                session.rename(archive/'episode-session.json')
        pending = saved.get('job') or {}
        saved = {**saved, 'status': 'retrying', 'deadline': time.time()+seconds,
                 'fallback_invocations': 0, 'no_progress': 0,
                 'stop_detail': None,
                 'job': pending if pending.get('status') in {'running', 'accepted'} else None}
        atomic_write_json(ctx.run_dir/'agent-run.json', saved)
    if saved.get('status') in {'finished', 'finished_with_issues'}:
        return saved
    previous_job = saved.get('job') or {}
    if (previous_job.get('action') == 'finish' and previous_job.get('status') == 'completed'
            and type((previous_job.get('result') or {}).get('passed')) is bool):
        manifest = read_json(ctx.work_dir / 'input/latest-finish.json', {})
        current_session = read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id')
        if (current_session and (manifest.get('author_session') or {}).get('session_id') == current_session
                and manifest.get('passed') is previous_job['result']['passed']):
            _record_finish(saved, manifest)
            atomic_write_json(ctx.run_dir / 'agent-run.json', saved)
            return saved
    finish_stamp = _file_stamp(ctx.work_dir / 'input/latest-finish.json')
    result = {'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
              'run_dir': str(ctx.run_dir), 'status': 'blocked', 'passed': False,
              'exit_code': 2, 'attempts': list(saved.get('attempts', [])), 'pid': os.getpid(),
              'deadline': saved.get('deadline') or time.time() + seconds,
              'fallback_invocations': saved.get('fallback_invocations', 0),
              'no_progress': saved.get('no_progress', 0), 'reason': None,
              'summary_only_turns': saved.get('summary_only_turns', 0),
              'idle_turns': saved.get('idle_turns',0),
              'agent_seconds': saved.get('agent_seconds', 0),
              'model_wait_seconds': saved.get('model_wait_seconds', 0)}
    if saved.get('model_switch'):
        result['model_switch'] = saved['model_switch']
    if saved.get('source_review_recovery'):
        result['source_review_recovery'] = saved['source_review_recovery']
    if saved.get('protocol_recovery'):
        result['protocol_recovery']=saved['protocol_recovery']
    if saved.get('batch_handoff_at') is not None:
        result['batch_handoff_at'] = saved['batch_handoff_at']
    deadline = time.monotonic() + max(0, result['deadline'] - time.time())
    slice_limit = config.get('agent_invocations_per_slice')
    if slice_limit is not None and int(slice_limit) <= 0:
        raise ValueError('agent_invocations_per_slice must be positive')
    slice_seconds = config.get('agent_slice_seconds', DEFAULT_REPAIR_TURN_SECONDS)
    if slice_seconds is not None and int(slice_seconds) <= 0:
        raise ValueError('agent_slice_seconds must be positive')
    slice_invocations = 0
    from novel_manga.resource_waits import wait_seconds
    wait_started=time.monotonic()
    wait_credited=0.0

    def account_resources():
        nonlocal deadline,wait_credited
        waited=wait_seconds(ctx.run_dir/'resource-waits.jsonl',since=wait_started)
        extra=max(0,waited-wait_credited)
        wait_credited=waited
        deadline+=extra
        result['deadline']+=extra
        result['resource_wait_seconds']=round(saved.get('resource_wait_seconds',0)+waited,3)

    def defer(reason):
        account_resources()
        result.update(status='deferred', phase='deferred', passed=False, exit_code=0,
                      reason=reason, remaining_seconds=max(0, deadline-time.monotonic()))
    # bind's pathname is limited to 108 bytes on Linux. The socket still lives in
    # the mounted workspace; a directory fd keeps deep episode paths usable.
    directory_fd = os.open(ctx.work_dir, os.O_RDONLY | os.O_DIRECTORY)
    bridge = EpisodeBridge(EpisodeTools(ctx), Path(f'/proc/self/fd/{directory_fd}/episode-tools.sock'))
    bridge.bind_state(ctx.run_dir / 'agent-run.json')
    bridge.update_state(**{**result, 'status': 'running'}, phase='ready_agent')
    socket_path = ctx.work_dir / 'episode-tools.sock'
    from novel_manga.application.production.common import pid_alive
    if saved.get('pid') and not pid_alive(saved['pid']):
        if socket_path.is_socket():
            socket_path.unlink()
    errors = []

    def serve():
        try:
            bridge.serve()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=serve, name=f'episode-tools-{ctx.source_episode_dir.name}')
    with process_environment(runtime):
        try:
            thread.start()
            startup_deadline = time.monotonic() + 5
            while not bridge.ready.wait(0.05):
                if errors:
                    raise errors[0]
                if not thread.is_alive() or time.monotonic() >= startup_deadline:
                    raise RuntimeError('episode tool bridge did not start')
            # Recover a submitted operation before asking the agent to do more.
            job = bridge.handle('status', {}).get('job') or {}
            if job.get('status') == 'accepted' and not _paused():
                if (job['action'] == 'render' and previous_job.get('status') == 'running'
                        and (ctx.run_dir / 'prepared.json').is_file()
                        and media_started(ctx.episode_dir)):
                    bridge._job['payload'] = {}
                bridge.end_turn()
            continued_inputs = False
            draft_check = None
            if (job.get('status') not in {'accepted', 'running'} and not _paused()
                    and time.monotonic() < deadline
                    and _pending_input_continuations(ctx)):
                bridge.handle('apply', {'file': 'output/resume-inputs.json'})
                continuation = _wait_for_idle(bridge)
                applied = (continuation.get('job') or {}).get('result') or {}
                continued_inputs = True
                if (time.monotonic() < deadline and (applied.get('changed') or applied.get('input_repaired'))
                        and (not native_mode or media_started(ctx.episode_dir))):
                    bridge.handle('render', {})
                    bridge.update_state(phase='tool_running')
            if (not continued_inputs and (bridge.handle('status', {}).get('job') or {}).get('status')
                    not in {'accepted', 'running'} and not _paused() and time.monotonic() < deadline):
                if _stale_request_blocks(ctx) and (not native_mode or media_started(ctx.episode_dir)):
                    bridge.handle('render', {})
                    bridge.update_state(phase='tool_running')
                    continued_inputs = True
                elif _pending_structured_repairs(ctx):
                    bridge.handle('apply', {'file': 'output/resume-structured-repairs.json'})
                    bridge.update_state(phase='tool_running')
                    continued_inputs = True
            if not native_mode and not continued_inputs and (not saved or saved.get('status') == 'retrying') and not _paused():
                bridge.update_state(phase='preparing_requests')
                if ((remaining := deadline-time.monotonic()) > 0 and not
                        (ctx.input_mode == 'source' and config.get('agent_instructions')
                         and not (ctx.run_dir/'prepared.json').is_file())):
                    draft_check = _fill_missing_requests(ctx, timeout=min(300, remaining))
                if (ctx.input_mode == 'script' and not (ctx.work_dir / SESSION_FILE).exists()
                        and time.monotonic() < deadline):
                    from .local_preparation import active
                    checked = {'valid':True} if active(ctx) else bridge.handle('check', {})
                    if checked.get('valid') is True:
                        payload = {} if media_started(ctx.episode_dir) or active(ctx) else {'file': 'output/requests.json'}
                        bridge.handle('render', payload)
                        bridge.update_state(phase='tool_running')
            if (not native_mode and not continued_inputs and ctx.input_mode == 'source' and not _paused()
                    and time.monotonic() < deadline
                    and (bridge.handle('status', {}).get('job') or {}).get('status') not in {'accepted', 'running'}
                    and (ctx.work_dir/'output/episode.json').is_file()
                    and not (ctx.run_dir/'prepared.json').is_file()):
                checked = draft_check if draft_check is not None else bridge.handle('check', {})
                if checked.get('valid') is True:
                    # Editorial constraints or known asset corrections need an Agent
                    # decision before media. Adoption alone does not submit video.
                    bridge.handle('adopt' if config.get('agent_instructions') else 'render',
                                  {'file': 'output/episode.json'})
                    bridge.update_state(phase='tool_running')
            native_block = None
            native_wait = None
            if (native_mode and not external_pipeline and not _paused() and (first_pass_only or not (ctx.run_dir/'pipeline-review-started.json').exists())
                    and (not continued_inputs or not media_started(ctx.episode_dir))):
                from novel_manga.application.agents.episode import pipeline_preparation as native_episode
                _wait_for_idle(bridge)
                bridge.update_state(phase='pipeline_preparation',waiting_for='native_pipeline')
                ready=native_episode.prepare(ctx,bridge.tools)
                account_resources()
                if ready.get('status')=='waiting_assets':
                    native_wait=ready
                elif ready.get('ready'):
                    bridge.handle('render',{})
                    bridge.update_state(phase='pipeline_generation',waiting_for='video')
                elif not ready.get('needs_semantic_repair'):
                    native_block=ready
            no_progress = result['no_progress']
            fallbacks = result['fallback_invocations']
            while True:
                if _paused() and (bridge.handle('status', {}).get('job') or {}).get('status') != 'running':
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                status = _wait_for_idle(bridge)
                account_resources()
                if external_pipeline:
                    from novel_manga.application.production.workspaces import pending_production
                    if pending_production(ctx):
                        defer('Updated first-pass inputs return to standard preparation and generation')
                        result.update(phase='waiting_pipeline',pipeline_phase='production',waiting_for='pipeline')
                        break
                if first_pass_only:
                    from novel_manga.application.preparation.store import unfilmed_video_clips
                    first_plan = read_json(ctx.episode_dir/'clip_plan.json', {})
                    if first_plan.get('clips') and not unfilmed_video_clips(ctx.episode_dir, first_plan):
                        defer('First videos complete; hand off film review to the repair queue')
                        result['pipeline_phase'] = 'review'
                        break
                if native_wait is not None:
                    defer('Shared batch assets pending; release the production host')
                    result.update(phase='waiting_assets',pipeline_phase='production',waiting_for='assets',
                                  waiting_assets=native_wait['waiting_assets'])
                    break
                if native_block is not None:
                    result.update(reason='native pipeline preparation blocked',pipeline_failure=native_block)
                    break
                manifest = _current_finish(ctx, finish_stamp, status.get('job'))
                if manifest is not None:
                    _record_finish(result, manifest)
                    break
                if _paused():
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                job = status.get('job') or {}
                if (not native_mode and job.get('action') == 'render' and job.get('status') == 'completed'
                        and (job.get('result') or {}).get('quality_passed') is True):
                    # The normal host operation already completed independent acceptance.
                    # Reserve an Agent conversation for an actual unresolved decision.
                    bridge.handle('finish', {})
                    status = _wait_for_idle(bridge)
                    completed = status.get('job') or {}
                    manifest = read_json(ctx.work_dir / 'input/latest-finish.json', {})
                    if (completed.get('action') == 'finish' and completed.get('status') == 'completed'
                            and (completed.get('result') or {}).get('passed') is True
                            and manifest.get('passed') is True):
                        _record_finish(result, manifest)
                        result['method'] = 'existing requests executed and accepted by host'
                        break
                if (_applied_repair(job)
                        and (time.monotonic() < deadline or config.get('complete_started_repairs'))):
                    bridge.handle('render', {})
                    bridge.update_state(phase='tool_running')
                    continue
                before = _progress(ctx, status)
                before_status = status
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    result['reason'] = 'episode operation timeout exhausted'
                    break
                if slice_limit is not None and slice_invocations >= int(slice_limit):
                    defer('Agent slice completed; resume this session after other production work')
                    break
                if native_mode:
                    from novel_manga.application.agents.episode.pipeline_preparation import (
                        reviewable, semantic_pending, semantic_prompt, begin_semantic_repair, review_prompt, begin_review)
                    batch_failure=external_pipeline and read_json(ctx.episode_dir/'batch-production.json',{}).get('status')=='blocked'
                    semantic=(batch_failure and first_pass_only) or (
                        not reviewable(ctx) and (semantic_pending(ctx) or batch_failure))
                    if not reviewable(ctx) and not semantic:
                        result.update(reason='native pipeline generation produced no reviewable video',pipeline_failure=job.get('result'))
                        break
                    if (begin_semantic_repair(ctx) if semantic else begin_review(ctx)):
                        no_progress=fallbacks=0
                        result.update(no_progress=0,fallback_invocations=0)
                    prompt=semantic_prompt() if semantic else review_prompt()
                    draft_exception=external_pipeline and semantic and not (ctx.episode_dir/'clip_plan.json').exists()
                    if draft_exception:
                        prompt=initial_prompt(ctx)+'\nThe standard planner already failed. Repair its existing draft and recorded errors; do not repeat completed book preparation.'
                    if first_pass_only:
                        missing = sorted(unfilmed_video_clips(ctx.episode_dir))
                        if not draft_exception:
                            prompt = (semantic_prompt() if semantic else
                                'Complete only the missing first video clips using the adopted screenplay and current input checks. '
                                'Fix confirmed input issues through apply, then render. Existing videos wait for later review. ')
                        prompt += ('\nThis is first video production. Targets: '+json.dumps(missing)+'. '
                            'Do not review/finish the film, keep/recheck filmed clips, or order retakes. '
                            'When no legal input correction remains, report the concrete blocker; do not repeat exhausted operations.')
                    phase='input_repair' if semantic else 'review'
                    result['pipeline_phase']=phase
                    manifest=read_json(ctx.work_dir/'input/manifest.json',{})
                    atomic_write_json(ctx.work_dir/'input/manifest.json',{**manifest,'agent_phase':phase})
                    bridge.update_state(pipeline_phase=phase)
                    if production_only and not first_pass_only:
                        defer('Native production handed off to the independent repair queue')
                        break
                else:
                    resume = (ctx.work_dir / SESSION_FILE).is_file()
                    prompt = continuation_prompt() if resume or (ctx.run_dir/'prepared.json').is_file() else initial_prompt(ctx)
                resume = (ctx.work_dir / SESSION_FILE).is_file()
                if config.get('agent_instructions'):
                    prompt += '\nTask-specific instructions:\n' + str(config['agent_instructions'])
                diagnosis = recovery.diagnose(ctx, status)
                atomic_write_json(ctx.run_dir/'recovery-diagnosis.json', diagnosis)
                memories = recovery.recall(ctx, diagnosis)
                from .current_task import prepare as current_task
                task=current_task(ctx,status,diagnosis,memories)
                prompt += ('\nCurrent task (input/current-task.json); full state is in input/current-status.json. '
                           'Use current targets and exact choices, not historical instructions:\n'
                           +json.dumps(task,ensure_ascii=False))
                waiting_since = None
                def waiting():
                    nonlocal waiting_since
                    waiting_since = time.monotonic()
                    bridge.update_state(phase='waiting_resource', waiting_for='agent_model')
                    if _paused():
                        raise InterruptedError('production dispatch paused')
                def account_wait():
                    nonlocal deadline, waiting_since
                    if waiting_since is None:
                        return
                    waited = max(0, time.monotonic() - waiting_since)
                    waiting_since = None
                    # Shared capacity is queue time, not additional author work.
                    # Keep all previously used operation time and retry counters.
                    deadline += waited
                    result['deadline'] += waited
                    result['model_wait_seconds'] = round(result.get('model_wait_seconds', 0) + waited, 3)
                    bridge.update_state(deadline=result['deadline'],
                                        model_wait_seconds=result['model_wait_seconds'])
                bridge.update_state(phase='ready_agent', waiting_for=None)
                try:
                    with model_slot(config['base_url'], on_wait=waiting, cancelled=_paused,
                                    reserve=int(config.get('model_slots_reserved_for_tools', 0)),record_queue=False):
                        account_wait()
                        if _paused():
                            result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                            break
                        remaining = int(deadline - time.monotonic())
                        if remaining <= 0:
                            raise TimeoutError('episode operation timeout exhausted')
                        bridge.update_state(phase='agent_running', waiting_for=None)
                        bridge.begin_turn()
                        started = time.monotonic()
                        # Initial adaptation includes reading the source and pictures and
                        # writing a whole screenplay. It is not a short repair turn.
                        direct_draft = ((ctx.work_dir/'output/episode.json').is_file()
                                        and read_json(ctx.run_dir/'source-draft-call.json', {}).get('status') == 'candidate')
                        authoring = (ctx.input_mode == 'source' and not (ctx.run_dir/'prepared.json').is_file()
                                     and not direct_draft)
                        turn_config = dict(config)
                        turn_seconds = config.get('source_agent_slice_seconds', config.get(
                            'agent_slice_seconds', DEFAULT_SOURCE_TURN_SECONDS)) if authoring else slice_seconds
                        if authoring and config.get('source_agent_max_turns') is not None:
                            turn_config['max_turns'] = config['source_agent_max_turns']
                        session_timeout = min(remaining, int(turn_seconds)) if turn_seconds is not None else remaining
                        attempt = run_session(ctx.work_dir, prompt, resume=resume, timeout=session_timeout,
                            config=turn_config, enable_tools=True, project_root=ctx.project_root,
                            expected_output='requests.json' if ctx.input_mode == 'script' else 'episode.json')
                        slice_invocations += 1
                        result['agent_seconds'] += round(time.monotonic() - started, 3)
                finally:
                    account_wait()
                    # The external CLI is gone and its model slot is now free.
                    # A review may call that very same model service.
                    bridge.end_turn(dispatch=not _paused())
                result['attempts'].append(str(attempt.directory))
                bridge.update_state(attempts=result['attempts'], phase='tool_running',
                                    agent_seconds=result['agent_seconds'],
                                    session_id=read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id'))
                if _paused() and (bridge.handle('status', {}).get('job') or {}).get('status') == 'accepted':
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                status = _wait_for_idle(bridge)
                if _paused():
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                replacement = recovery.switch_for_refusal(ctx, config, result, attempt, before_status, status)
                if replacement is not None:
                    config = replacement
                    no_progress = 0
                    fallbacks = result['fallback_invocations']
                    bridge.update_state(model_switch=result['model_switch'], no_progress=0)
                    continue
                if config.get('complete_started_repairs') and _applied_repair(status.get('job') or {}):
                    # The submitted repair is already validated and published.
                    # Let the host close its media chain even if the author CLI
                    # ended at its timeout while handing off that operation.
                    no_progress = 0
                    result['no_progress'] = 0
                    bridge.update_state(no_progress=0)
                    continue
                if not attempt.ok:
                    timed_out = attempt.exit_code == 124 and session_timeout < remaining
                    if timed_out or attempt.record.get('turn_limit_reached'):
                        no_progress = 0 if _made_progress(ctx, before, before_status, status) else no_progress + 1
                        result['no_progress'] = no_progress
                        if no_progress >= 2:
                            result['reason'] = 'episode agent ended twice without operation or output progress'
                        else:
                            defer('Agent reached this slice '+('time' if timed_out else 'turn')+' limit; continue the saved session')
                        break
                    result.update(reason='episode agent invocation failed', session_exit=attempt.exit_code)
                    break
                manifest = _current_finish(ctx, finish_stamp, status.get('job'))
                if manifest is not None:
                    _record_finish(result, manifest)
                    break
                job_before = before[-2]
                job = status.get('job') or {}
                handed_off = bool(job.get('id') and job['id'] != job_before)
                if not handed_off:
                    fallbacks += 1
                no_progress = 0 if _made_progress(ctx, before, before_status, status) else no_progress + 1
                result.update(no_progress=no_progress, fallback_invocations=fallbacks)
                bridge.update_state(no_progress=no_progress, fallback_invocations=fallbacks)
                if no_progress >= 2:
                    result['reason'] = 'episode agent ended twice without operation or output progress'
                    break
                if fallbacks >= MAX_INVOCATIONS:
                    result['reason'] = 'episode agent reached the bounded operation continuation limit'
                    break
        except InterruptedError:
            result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
        except Exception as error:
            result['reason'] = f'{type(error).__name__}: {error}'
        finally:
            bridge.end_turn(dispatch=not _paused())
            bridge.stop()
            if thread.ident is not None:
                # Bridge shutdown waits for its one accepted host job before removing its socket.
                thread.join()
            os.close(directory_fd)
        if result.get('reason') in {'episode agent ended twice without operation or output progress',
                                   'episode agent reached the bounded operation continuation limit'}:
            result['stop_detail'] = stop_details(status)
        if result.get('status') != 'deferred':
            recovery.remember(ctx, read_json(ctx.run_dir/'recovery-diagnosis.json', {}),
                {'action': 'finish' if result.get('passed') else 'stop',
                 'ok': bool(result.get('passed')), 'passed': bool(result.get('passed')),
                 'error': result.get('reason')})
        result['phase'] = result.get('phase') or ('finished' if result['status'].startswith('finished') else 'blocked')
        account_resources()
        result['remaining_seconds'] = max(0, deadline-time.monotonic())
        bridge.update_state(**result)
        result = read_json(ctx.run_dir / 'agent-run.json')
    return result
