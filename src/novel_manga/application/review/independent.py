"""Adopt independent picture evidence and re-observe each repaired movie in the sandbox."""
from __future__ import annotations
import copy
import shutil
import time
from pathlib import Path

from novel_manga.application.agents.sandbox import load_config, run_agent
from novel_manga.util import atomic_write_json, read_json

POLICY = 'independent-movie-v2-offending-clip-attribution'


def required(directory):
    return (Path(directory)/'independent_review/registration.json').is_file()


def current(directory, final):
    if not required(directory):
        return True
    independent = final.get('independent_review')
    saved = read_json(Path(directory)/'independent_review.json', {})
    return bool(independent and independent == saved and independent.get('policy') == POLICY
                and independent.get('context') == final.get('context'))


def validate(answer, frames, *, previous=()):
    if (not isinstance(answer.get('passed'), bool) or answer.get('complete') is not True
            or not isinstance(answer.get('observations'), list) or not answer['observations']
            or not isinstance(answer.get('issues'), list)
            or answer['passed'] != (not answer['issues'])):
        raise ValueError('independent review requires complete observations and consistent passage')
    by_path = {f['path']: f for f in frames}
    for issue in answer['issues']:
        paths = issue.get('frame_paths') or []
        if (not issue.get('clip_ids') or not paths or not issue.get('problem')
                or not issue.get('evidence') or not issue.get('source_basis')
                or any(p not in by_path or not Path(p).is_file() for p in paths)
                or not set(issue['clip_ids']) <= {by_path[p]['clip_id'] for p in paths}):
            raise ValueError('independent finding lacks actual frames for its clips/source')
    dispositions = {r.get('id'): r for r in answer.get('prior_findings', [])}
    for issue in previous:
        row = dispositions.get(issue['id'], {})
        if (row.get('status') not in {'resolved', 'present'} or not row.get('evidence')
                or not row.get('frame_paths') or any(p not in by_path for p in row['frame_paths'])
                or not set(issue['clip_ids']) <= {by_path[p]['clip_id'] for p in row['frame_paths']}):
            raise ValueError('independent recheck omitted a prior finding or its current picture evidence')
        if row['status'] == 'present' and not any(r.get('prior_id') == issue['id'] for r in answer['issues']):
            raise ValueError('independent recheck dropped a finding it still observes')


def register(directory, media, report):
    """Adopt a completed independent report against the exact already-reviewed movie."""
    from .movie_context import POLICY as FINAL_POLICY, context
    directory = Path(directory)
    raw = read_json(Path(report)) if not isinstance(report, dict) else copy.deepcopy(report)
    final = read_json(directory/'final_cut_review.json', {})
    if (final.get('policy') != FINAL_POLICY
            or final.get('context') != context(directory, media, final.get('movie', ''))
            or final.get('automatic_review', final).get('complete') is not True
            or raw.get('movie') != final.get('movie')):
        raise ValueError('independent report must identify the current reviewed movie')
    known = {f['path']: f for f in final.get('frames', [])}
    frames = raw.get('frames', [])
    if not frames or any(f != known.get(f.get('path')) for f in frames):
        raise ValueError('independent frames do not match the actual current movie review')
    validate(raw, frames)
    root = directory/'independent_review'; root.mkdir(exist_ok=True)
    target = root/f'intake-{time.time_ns()}'; target.mkdir()
    (root/'owner').write_text('Independent movie findings: existing automatic repair loop, original take limits\n')
    atomic_write_json(target/'report.json', raw)
    result = {**raw, 'policy': POLICY, 'context': final['context'], 'errors': [], 'origin': str(target/'report.json')}
    for i, issue in enumerate(result['issues'], 1):
        issue.setdefault('id', f'independent-{i}')
        issue['reviewer'] = 'independent'
    atomic_write_json(root/'registration.json', {'report': str(target/'report.json'), 'policy': POLICY})
    atomic_write_json(directory/'independent_review.json', result)
    return result


def observe(directory, final, *, config=None):
    """The independent reviewer reads images, never production files or repair outputs."""
    directory = Path(directory)
    previous = read_json(directory/'independent_review.json', {})
    if (previous.get('context') == final['context'] and previous.get('policy') == POLICY
            and previous.get('complete') is True):
        validate(previous, previous['frames'])
        return previous
    config = config or load_config()
    body = {**(config.get('extra_body') or {}), **(config.get('repair_extra_body') or {})}
    body['tool_choice'] = {'type': 'auto', 'disable_parallel_tool_use': True}
    config = {**config, 'extra_body': body, 'effort': 'low', 'max_thinking_tokens': 0,
              'extra_args': [*config.get('extra_args', []), '--tools', 'Read,Write', '--include-partial-messages']}
    run = f'independent-{directory.name}-{time.time_ns()}'
    work = Path(config['runs_root'])/run
    inputs = work/'input'; inputs.mkdir(parents=True)
    (work/'owner').write_text('Independent movie review; observation only; no production writes or generation\n')
    frames, originals = [], {}
    for i, frame in enumerate(final['frames']):
        source = Path(frame['path'])
        if not source.is_file():
            raise ValueError('independent reviewer movie frame unavailable')
        target = inputs/f'frame-{i:03d}.jpg'; shutil.copy2(source, target)
        relative = str(target.relative_to(work))
        originals[relative] = frame['path']
        frames.append({**frame, 'path': relative})
    primary = [f for f in frames if f['label'] == 'middle' or 'interaction card' in f['label']]
    source = {k: final['context'].get(k) for k in ('script', 'bible', 'layout')}
    prior = [{k: v for k, v in issue.items() if k != 'frame_paths'} for issue in previous.get('issues', [])]
    atomic_write_json(inputs/'review.json', {'frames': frames, 'primary_frames': primary,
        'approved_source': source, 'prior_findings': prior,
        'caption_policy': 'expected_captions and approved_labels are valid postproduction layers; report actual extra/duplicate text only'})
    (work/'prompt.txt').write_text(
        'Independently review the actual movie pictures. Read input/review.json then open every primary_frames JPEG '
        'with Read. Open other frames to check a contradiction or prior finding. Judge approved source, goals and '
        'physical/camera/wardrobe/prop/spatial continuity across clips. Approved captions and labels are allowed; '
        'actual duplicate dialogue layers or extra text are defects. Do not infer audio from images. Reports from '
        'other reviewers are hypotheses: verify each prior finding in actual current pictures. Do not change source, '
        'requests, timing, or footage, and do not generate video. Only Read and Write are available. '
        'Write output/review.json immediately after observing the required pictures, then stop. Use one tool call per response. '
        'JSON fields: complete (true only when assigned picture review finished), passed (issues is empty), '
        'observations (array of {frame_path, observation}, covering every primary frame), '
        'issues (array of {id, clip_ids, problem, evidence, source_basis, frame_paths, optional prior_id}), '
        'prior_findings (one {id, status: resolved or present, evidence, frame_paths} for EVERY previous issue). '
        'All frame paths must be input/frame-NNN.jpg from the supplied list. Each issue/disposition must cite actual '
        'current pictures for every clip it names. If still present, include an issue with prior_id equal to that prior id. '
        'issues.clip_ids names ONLY the clips that actually violate the approved source and need correction. '
        'Correct comparison/anchor clips must stay out of clip_ids; retain their JPEGs in frame_paths as evidence. '
        'For wardrobe discontinuity, compare each clip to the approved outfit: do not label a correct gray-outfit '
        'anchor as defective merely because another clip changes to a wrong color. '
        'Use complete=false and explain missing evidence if unable to finish; never claim passage without reading pictures. '
        'Keep old issue ids when still present. This is one bounded 600-second review, not a repair task.\n')
    deadline = time.monotonic()+600
    attempt = run_agent(run, config=config, timeout=600)
    produced = set(attempt.produced)
    from novel_manga.application.agents.sandbox import protocol_failure
    remaining = int(deadline-time.monotonic())
    if protocol_failure(attempt) and remaining > 0:
        prompt = work/'continue-protocol.txt'
        prompt.write_text('Continue this same independent movie review using evidence already read. '
                          'The last tool response was malformed. Use one valid tool call at a time, '
                          'finish observing required images, write output/review.json, and stop.')
        attempt = run_agent(run, config={**config, 'extra_args': [*config['extra_args'], '--continue']},
                            timeout=remaining, prompt=prompt)
        produced.update(attempt.produced)
    result = {'policy': POLICY, 'context': final['context'], 'movie': final['movie'],
              'complete': False, 'passed': False, 'issues': [], 'frames': final['frames'],
              'errors': [], 'run': run, 'attempt': attempt.directory.name}
    try:
        if not attempt.ok or 'review.json' not in produced:
            raise ValueError('independent reviewer did not produce a current review')
        answer = read_json(work/'output/review.json', {})
        observed = {r.get('frame_path') for r in answer.get('observations', []) if isinstance(r, dict) and r.get('observation')}
        if not {f['path'] for f in primary} <= observed:
            raise ValueError('independent reviewer omitted required current movie pictures')
        for issue in [*answer.get('issues', []), *answer.get('prior_findings', [])]:
            issue['frame_paths'] = [originals.get(p, p) for p in issue.get('frame_paths', [])]
        validate(answer, final['frames'], previous=previous.get('issues', []))
        for i, issue in enumerate(answer['issues'], 1):
            issue.setdefault('id', f'{run}-{i}')
            issue['reviewer'] = 'independent'
        result.update(answer)
    except ValueError as error:
        result['errors'].append(str(error))
        # Keep earlier findings as recheck targets after a service/protocol failure.
        result['issues'] = previous.get('issues', [])
    atomic_write_json(directory/'independent_review.json', result)
    atomic_write_json(work/'result.json', result)
    return result


def relocate(directory, final):
    """A controlled publication copy changes location, not the observed movie content."""
    if not final.get('independent_review'):
        return
    child = final['independent_review']
    child.update(movie=final['movie'], context=copy.deepcopy(final['context']))
    atomic_write_json(Path(directory)/'independent_review.json', child)


def combine(directory, final):
    if not required(directory):
        return final
    result = copy.deepcopy(final)
    automatic = result.get('automatic_review') or {k: result[k] for k in ('complete', 'passed', 'issues', 'errors')}
    result.update(copy.deepcopy(automatic))
    result['automatic_review'] = copy.deepcopy(automatic)
    if automatic['complete'] is not True:
        return result
    try:
        independent = observe(directory, result)
    except (ValueError, OSError) as error:
        independent = {'complete': False, 'passed': False, 'issues': [], 'frames': [], 'errors': [str(error)]}
    result['independent_review'] = independent
    result['complete'] = automatic['complete'] and independent['complete']
    result['passed'] = result['complete'] and automatic['passed'] and independent['passed']
    result['errors'] = [*automatic['errors'], *independent['errors']]
    result['issues'] = [*automatic['issues'], *independent['issues']]
    by_path = {f['path']: f for f in result['frames']}
    by_path.update({f['path']: f for f in independent.get('frames', [])})
    result['frames'] = list(by_path.values())
    return result
