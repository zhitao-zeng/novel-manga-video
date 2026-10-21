"""Evidence export and final-request proposals through the existing sandbox executor."""
from __future__ import annotations
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import shutil
import time
from pathlib import Path

from novel_manga.application.agents.sandbox import load_config, run_agent, protocol_failure
from novel_manga.application.configuration import project_root
from novel_manga.application.profiles import h3_stamp
from novel_manga.repair.proposal import RepairProposal
from novel_manga.story.h3 import request_issues
from novel_manga.story.dialogue import dialogue_punctuation
from novel_manga.application.review.evidence import clip_frames
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json, atomic_write_json

FRAME_HANDOFF_POLICY = 'same-take-frames-v10-request-lane-upstream-budget-feedback'
MAX_CANDIDATES = 3


class AgentUnavailable(ValueError):
    """SDK execution/service failed; another explicit resume may retry unchanged inputs."""

SECTIONS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
            'overall_soundscape', 'non_diegetic_music')


class CandidateRejected(ValueError):
    """An actual candidate failed content validation, unlike an unavailable service/output."""
    def __init__(self, reason, decision, *, stage, check=None):
        super().__init__(reason)
        self.feedback = {'stage':stage, 'reason':str(reason), 'candidate':copy.deepcopy(decision)}
        if check is not None:
            self.feedback['check'] = copy.deepcopy(check)


def enabled(directory: Path) -> bool:
    return read_json(directory.parent/'profile.json', {}).get('repair_backend') == 'sandbox_agent'


_REQUEST_LANE = ContextVar('repair_request_lane', default=None)


@contextmanager
def request_scope(lane):
    """Use the actual renderer's request grammar without changing global settings."""
    if lane not in {None, 'h3', 'sd25'}:
        raise ValueError('Unsupported repair request lane')
    token = _REQUEST_LANE.set(lane) if lane is not None else None
    try:
        yield
    finally:
        if token is not None:
            _REQUEST_LANE.reset(token)


def request_lane(clip):
    return _REQUEST_LANE.get() or ('h3' if clip.get('prompt_h3') else 'sd25')


def check_request(clip, novel_dir):
    from novel_manga.application.preparation.request_check import request_consistency
    if request_lane(clip) == 'sd25':
        clip = {**clip, 'prompt_h3': ''}
    return request_consistency(clip, novel_dir)


def validate_request(clip, prompt):
    if request_lane(clip) == 'sd25':
        from novel_manga.application.repair.sd_request import validate
        validate(clip, prompt)
    else:
        validate_prompt(clip, prompt)


def sections(prompt):
    matches = list(re.finditer(r'(?m)^('+'|'.join(SECTIONS)+r'):\s*$', prompt))
    if tuple(m.group(1) for m in matches) != SECTIONS:
        raise ValueError('agent request must preserve the six H3 sections')
    return {m.group(1): prompt[m.end():matches[i+1].start() if i+1<len(matches) else len(prompt)].strip()
            for i,m in enumerate(matches)}


def validate_prompt(clip, prompt, *, dialogue_bindings=None):
    old, new = sections(clip['prompt_h3']), sections(prompt)
    def definition_bindings(text):
        rows = list(re.finditer(r'(?m)^(<(?:Subject|Audio) \d+>)', text))
        return [(row[1], sorted(set(token for token in re.findall(r'<(?:Picture|Subject|Audio) \d+>|\(S\d+\)',
                    text[row.end():rows[i+1].start() if i+1<len(rows) else len(text)]) if token != row[1])))
                for i,row in enumerate(rows)]
    if definition_bindings(old['subject_definitions']) != definition_bindings(new['subject_definitions']):
        raise ValueError('agent changed protected reference binding: subject_definitions')
    def retention_bindings(text):
        rows = list(re.finditer(r'(?m)^(<(?:Subject|Audio) \d+>): (\w+)', text))
        return [(row[1], row[2], sorted(set(re.findall(r'<Picture \d+>',
                    text[row.end():rows[i+1].start() if i+1<len(rows) else len(text)]))))
                for i,row in enumerate(rows)]
    # The agent may clarify the actual clothes/visible state. Lock the reference
    # associations and retention mode, not every word of their descriptions.
    if retention_bindings(old['retention_analysis']) != retention_bindings(new['retention_analysis']):
        raise ValueError('agent changed protected reference binding: retention_analysis')
    old_dialogue = [dialogue_punctuation(text) for text in re.findall(r'<d>(.*?)</d>', clip['prompt_h3'], re.S)]
    new_dialogue = [dialogue_punctuation(text) for text in re.findall(r'<d>(.*?)</d>', prompt, re.S)]
    if dialogue_bindings is not None:
        # Adopted repairs use the plan's bound words, not legacy English that
        # may have lost punctuation during compilation. Other callers retain
        # their original request contract.
        from novel_manga.story.dialogue import BOUND_SPEECH
        old_dialogue = [dialogue_punctuation(row['text']) for row in dialogue_bindings]
        new_dialogue = [dialogue_punctuation(match[4]) for match in BOUND_SPEECH.finditer(prompt)]
    if old_dialogue != new_dialogue:
        raise ValueError('agent changed dialogue, delivery binding or shot timing')
    for pattern in (r'<Subject \d+> \(S\d+\) says(?: in an off-screen voiceover)?',
                    r'\[Shot \d+\]', r'At \d\d:\d\d\.\d+',
                    r'The speaking voice follows the timbre referenced from <Audio \d+>\.'):
        if re.findall(pattern, clip['prompt_h3'], re.S) != re.findall(pattern, prompt, re.S):
            raise ValueError('agent changed dialogue, delivery binding or shot timing')
    duration_pattern = r'Planned duration: ([\d.]+) seconds\.'
    old_durations = [float(value) for value in re.findall(duration_pattern, clip['prompt_h3'])]
    new_durations = [float(value) for value in re.findall(duration_pattern, prompt)]
    if old_durations != new_durations:
        timing = clip.get('shot_timing') or []
        expected = [float(row['seconds']) for row in timing]
        # Older requests can include a duration header for only some shots.
        # Filling those headers must still use the complete saved shot timing.
        if not expected or new_durations != expected:
            raise ValueError('agent changed planned durations; preserve the saved shot timing')
    for pattern in (r'<Subject \d+>', r'<Picture \d+>', r'<Audio \d+>', r'\(S\d+\)'):
        if set(re.findall(pattern, clip['prompt_h3'])) != set(re.findall(pattern, prompt)):
            raise ValueError('agent changed reference/speaker tags')
    problems = request_issues({**clip, 'prompt_h3': prompt})
    if problems:
        raise ValueError('; '.join(problems))


def provided_frames(directory: Path, clip_id: str, video: Path, target: Path):
    """Copy the explicit same-take frame list, including late actions, or extract."""
    review_path = directory/'episode_review.json'
    reviewed = (read_json(review_path, {}).get('clips') or {}).get(clip_id, {})
    identity = take_identity(video)
    target.mkdir(parents=True, exist_ok=True)
    for frame in target.glob('frame_*.jpg'):
        frame.unlink()
    cache = directory/'work/review'/clip_id
    evidence = read_json(cache/'frame-evidence.json', {})
    rows = evidence.get('frames') or []
    cached = [Path(row['path']) for row in rows]
    same_review = (reviewed.get('video') == str(video) and identity
                   and reviewed.get('take') == identity and reviewed.get('severity') != 'review_error'
                   and evidence.get('video') == str(video) and evidence.get('take') == identity)
    # A glob cannot distinguish stale tails whose timestamps look plausible.
    if (same_review and cached and all(p.is_file() and p.stat().st_size > 0
                            and identity[2] <= p.stat().st_mtime_ns <= review_path.stat().st_mtime_ns
                            for p in cached)):
        frames = []
        for source in cached:
            frame = target/source.name
            shutil.copy2(source, frame)
            frames.append(frame)
        atomic_write_json(target/'frame-evidence.json', {
            **evidence, 'frames': [{**row, 'path': str(frame)} for row, frame in zip(rows, frames)]})
        return frames, 'same_take_review_report_supported'
    return clip_frames(video, target, 4), 'host_uniform_4_to_98_percent'


def unique_takes(videos):
    """An archived copy of the same managed render is not another comparison take."""
    paths, seen, skipped = [], [], 0
    for video in dict.fromkeys(videos):
        marker = read_json(video.parent / 'request.json', {}).get('managed_generation')
        if isinstance(marker, dict) and marker:
            if marker in seen:
                skipped += 1
                continue
            seen.append(marker)
        paths.append(video)
    return paths, skipped


def export_final_cut(directory, inputs, clip, verdict, *, work_root=None):
    """Copy final-movie frames into either a repair run or the persistent episode sandbox."""
    final = verdict.get('final_cut')
    if not final:
        return None
    issues = [copy.deepcopy(row) for row in final.get('issues',[]) if clip['clip_id'] in row.get('clip_ids',[])]
    wanted = {path for row in issues for path in row.get('frame_paths',[])}
    frames, copied, missing = [], {}, []
    target = inputs/'final-cut';target.mkdir(parents=True,exist_ok=True)
    base = work_root or inputs.parent
    for i,row in enumerate(final.get('frames',[])):
        path = row['path']
        if row.get('clip_id')!=clip['clip_id'] and path not in wanted:
            continue
        source = Path(path)
        if not source.is_absolute():source=directory/source
        if not source.is_file():
            missing.append(path);continue
        saved = target/f'frame-{i:03d}{source.suffix}'
        shutil.copy2(source,saved)
        copied[path] = str(saved.relative_to(base))
        frames.append({**row,'path':copied[path]})
    missing.extend(path for path in wanted if path not in copied and path not in missing)
    for row in issues:
        row['frame_paths'] = [copied[path] for path in row.get('frame_paths',[]) if path in copied]
    return {'issues':issues,'frames':frames,'missing_frames':missing,
            'source_movie':Path(final.get('movie') or '').name,
            'seconds_timebase':'global seconds in the reviewed final movie, not local native-take seconds'}


def propose(directory: Path, clip: dict, verdict: dict, *, config=None, rejections=None):
    """No production writes. Container sees evidence copies, never the production directory."""
    config = config or load_config()
    if config.get('repair_extra_body'):
        config = {**config, 'extra_body': {**(config.get('extra_body') or {}),
                                         **config['repair_extra_body']}}
        repair=config['repair_extra_body']
        if (repair.get('thinking') or {}).get('type')=='disabled':
            config={**config,'max_thinking_tokens':0}
        if (repair.get('output_config') or {}).get('effort'):
            config={**config,'effort':repair['output_config']['effort']}
    config = {**config, 'extra_body':{**(config.get('extra_body') or {}),
                'tool_choice':(config.get('extra_body') or {}).get('tool_choice') or
                              {'type':'auto','disable_parallel_tool_use':True}},
              'extra_args': [*config.get('extra_args', []), '--tools', 'Read,Bash,Write', '--include-partial-messages']}
    lane = request_lane(clip)
    field = 'prompt_h3' if lane == 'h3' else 'prompt'
    if lane == 'h3':
        sections(clip.get(field, ''))
    run = f'repair-{directory.name}-{clip["clip_id"]}-{time.time_ns()}'
    work = Path(config['runs_root'])/run
    inputs = work/'input'; inputs.mkdir(parents=True)
    (work/'owner').write_text(f'Existing bounded repair / {directory.name} / {clip["clip_id"]} / proposal only\n')
    skill = work/'.claude/skills/video-repair'; skill.mkdir(parents=True)
    shutil.copy2(project_root()/'skills/video-repair/SKILL.md', skill/'SKILL.md')
    (inputs/'prompt.txt').write_text(clip[field], encoding='utf-8')
    feedback = copy.deepcopy(rejections or [])
    for i,row in enumerate(feedback,1):
        rejected = row['candidate']
        if 'prompt' in rejected:
            target = inputs/f'rejected-candidate-{i}.txt'
            target.write_text(rejected.pop('prompt'),encoding='utf-8')
            rejected['prompt_path'] = str(target.relative_to(work))
    atomic_write_json(inputs/'rejections.json',feedback)
    script = read_json(directory/'chapter_script.json', {})
    indexes = clip.get('shot_indexes') or []
    near = {i+d for i in indexes for d in (-1,0,1)}
    shots = [s for i,s in enumerate(script.get('shots', []),1) if s.get('index',i) in near]
    media = read_json(directory/'thin_media_report.json', {})
    row = next((r for r in media.get('clips',[]) if r['clip_id']==clip['clip_id']), {})
    history = read_json(directory/'repair_history/history.json', {})
    from novel_manga.story.h3 import REQUEST_CONTENT_RULES
    evidence = {'frame_handoff_policy':FRAME_HANDOFF_POLICY, 'provider':lane, 'request_field':field,
                'request_rules': REQUEST_CONTENT_RULES if lane == 'h3' else 'Preserve non-stage lines, stage headers, reference tags, all dialogue and sound clauses; edit only visual stage directions.', 'clip': clip, 'shots_and_neighbours': shots, 'segments':read_json(directory/'segments.json', []),
                'verdict':verdict, 'media':row, 'preparation_problems':media.get('blocked_clips',{}).get(clip['clip_id'],[]), 'feedback':read_json(directory/'review_feedback.json', {}).get(clip['clip_id'],''),
                'observations':history.get('observations',{}).get(clip['clip_id'],[]),
                'trials':[{k:t.get(k) for k in ('id','method','changes','expected_notes')}
                          for t in history.get('trials',[]) if clip['clip_id'] in t.get('clips',[])],
                'references':[], 'takes':[], 'rejections':feedback,
                'candidate_revision':len(feedback)+1, 'candidate_limit':MAX_CANDIDATES}
    final_cut = export_final_cut(directory,inputs,clip,verdict)
    if final_cut is not None:
        evidence['final_cut'] = final_cut
        evidence['verdict'] = {**verdict,'final_cut':final_cut}
        if final_cut['issues'] and (not final_cut['frames'] or final_cut['missing_frames']):
            atomic_write_json(inputs/'evidence.json',evidence)
            raise ValueError('repair agent cannot verify final-cut findings: required movie frames unavailable')
    for i,ref in enumerate(clip.get('references',[]),1):
        source = directory.parent/ref['path']; target = inputs/f'reference-{i}{source.suffix}'
        shutil.copy2(source,target)
        scope = read_json(source.with_suffix(source.suffix + '.origin.json'), {}).get('reference_scope')
        evidence['references'].append({**ref,'path':str(target.relative_to(work)),
                                       'source_path':str(source.resolve()),
                                       **({'reference_scope':scope} if scope else {})})
    video = (row.get('selected') or {}).get('video') or verdict.get('video')
    videos = ([Path(video)] if video else []) + sorted((directory/'repair_history/takes'/clip['clip_id']).glob('*/clip.mp4'))[-3:]
    videos, skipped = unique_takes(videos)
    evidence['duplicate_generation_copies_skipped'] = skipped
    for i,source in enumerate(videos,1):
        if not source.is_file(): continue
        target=inputs/f'take-{i}'; target.mkdir()
        for name in ('clip.mp4','request.json','asr.json'):
            path=source if name=='clip.mp4' else source.parent/name
            if path.is_file():shutil.copy2(path,target/name)
        frames, provenance = provided_frames(directory,clip['clip_id'],source,target/'frames')
        frame_record=read_json(target/'frames/frame-evidence.json',{})
        times={Path(r['path']).name:r.get('seconds') for r in frame_record.get('frames',[])}
        supplied=[{'path':str(frame.relative_to(work)),'seconds':times.get(frame.name)} for frame in frames]
        atomic_write_json(target/'frames/frame-evidence.json', {
            'video':str((target/'clip.mp4').relative_to(work)), 'source_video':str(source),
            'source_take':take_identity(source), 'provenance':provenance, 'frames':supplied})
        evidence['takes'].append({'path':str(target.relative_to(work)), 'current':str(source)==video,
            'request':str((target/'request.json').relative_to(work)) if (target/'request.json').is_file() else None,
            'asr':str((target/'asr.json').relative_to(work)) if (target/'asr.json').is_file() else None,
            'frame_provenance':provenance, 'frames':supplied})
    EPISODE_FILES = ('chapter_script.json', 'segments.json', 'source_trace.json')
    BOOK_FILES = ('bible.json', 'entity_index.json', 'bible_aliases.json')
    evidence['source_documents']=[]
    files=[(directory/name,Path(name)) for name in (*EPISODE_FILES,'source.txt')]
    trace=read_json(directory/'source_trace.json',{})
    for key in ('original','storyboard'):
        if trace.get(key):
            source=Path(trace[key])
            if source.is_file():files.append((source,Path('source_documents')/(key+source.suffix)))
    checks=directory/'work/request_checks'
    files += [(source,Path('request_checks')/source.name) for source in checks.glob(clip['clip_id']+'.*.json')]
    files.append((checks/(clip['clip_id']+'.json'),Path('request_checks')/(clip['clip_id']+'.json')))
    files += [(directory.parent/name,Path(name)) for name in (*BOOK_FILES,'story_bible.json','profile.json')]
    files += [(directory/row['path'],Path(row['path']))
              for row in read_json(directory/'intake.json',{}).get('documents',[])]
    for source,relative in files:
        if not source.is_file():continue
        target=inputs/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        evidence['source_documents'].append(str(target.relative_to(work)))
    tools=work/'tools';tools.mkdir()
    shutil.copy2(Path(__file__).with_name('video_frames.py'),tools/'video_frames.py')
    evidence['video_reader']='python3 tools/video_frames.py take-1 --seconds 2.0'
    current = next((t for t in evidence['takes'] if t['current']), {})
    from novel_manga.application.repair import upstream
    choices=upstream.catalog_for_clip(directory,clip)
    for number,asset in enumerate(choices.get('assets',[]),1):
        source=Path(asset['path'])
        if not source.is_absolute():source=directory.parent/source
        target=inputs/f'candidate-asset-{number}{source.suffix}'
        shutil.copy2(source,target)
        asset['path']=str(target.relative_to(work))
        binding=asset.get('binding')or asset
        asset['selected_reference_paths']=[ref['path'] for ref in evidence['references']
            if ref['source_path']==asset['source_path'] and
            (ref.get('role'),ref.get('name'))==(binding.get('role'),binding.get('name'))]
    from novel_manga.application.repair.history import generation_counts,generation_limit
    choices['video_remaining']=max(0,generation_limit(directory,clip['clip_id'])-generation_counts(directory).get(clip['clip_id'],0))
    choices['upstream_remaining']=max(0,upstream.limit(directory,clip['clip_id'])-len(upstream.records(directory,clip['clip_id'])))
    from novel_manga.application.review.final_dispute import available as dispute_available
    choices['final_dispute_remaining']=int(dispute_available(directory,clip['clip_id']))
    choices['source_patch_fields']=sorted(upstream.SHOT_FIELDS)
    atomic_write_json(inputs/'upstream.json',choices)
    evidence['primary_reading'] = {
        'request': 'input/prompt.txt',
        'current_frames': current.get('frames', []),
        'reference_images': [r['path'] for r in evidence['references'] if r.get('role') != 'voice'],
        'source': 'Use inline shots_and_neighbours and corresponding segments first; read extra source documents only for a specific unresolved fact.'}
    evidence['reading_scope'] = 'One failed request; history and full-episode documents are optional evidence, not a mandatory reading list.'
    atomic_write_json(inputs/'evidence.json',evidence)
    related_segments = {str(value) for value in clip.get('segment_ids', [])}
    related_segments.update(str(shot['segment_id']) for shot in shots if shot.get('segment_id'))
    atomic_write_json(inputs/'primary.json', {
        'current_frames': current.get('frames', []),
        'provider': lane, 'request_field': field,
        'current_request': current.get('request'),
        'request': 'input/prompt.txt',
        'shots_and_neighbours': shots,
        'source_passages': [row for row in evidence['segments']
                            if str(row.get('segment_id')) in related_segments],
        'references': evidence['references'],
        'verdict': evidence['verdict'], 'rejections':feedback,
        'upstream':'input/upstream.json',
        **({'final_cut':final_cut} if final_cut is not None else {}),
        'candidate_revision':len(feedback)+1, 'candidate_limit':MAX_CANDIDATES})
    (work/'prompt.txt').write_text('Read input/primary.json first. When primary.final_cut is present, first open its frames[].path JPEGs '
                                 'and verify the related final-cut findings against those actual pictures and source_basis. '
                                 'Their seconds are global within the reviewed final movie, and may include a neighbouring clip boundary. '
                                 'Native footage cannot substitute for final-movie continuity. Reports are findings to verify, not picture facts to assume. '
                                 'Then open primary.current_frames[].path JPEGs; without final_cut, start with those current native-take images. '
                                 'Read .claude/skills/video-repair/SKILL.md. Diagnose this failed clip from input/. '
                                 'Read input/upstream.json for reviewed alternative assets, source correction suggestions, audio candidates and current permissions. '
                                 'Choose input for source/reference corrections, asset for a necessary missing corrected reference, audio for interaction ambience assembly, '
                                 'edit only for a proven final-English-only defect, retake only for already-correct input with unused video permission, keep or stop. '
                                 'Write output/decision.json and, only for edit, output/prompt.txt. '
                                 'Actual native video frames and local timestamps are in evidence.takes[].frames. '
                                 'Use python3 tools/video_frames.py take-1 to list them. Do not read MP4 with the text reader, '
                                 'install packages, or prepare a decoder. If supplied evidence is insufficient, write stop with the missing evidence. '
                                 'This is one request with a 600-second execution window. Use input/primary.json and its local shot/source facts first. Full evidence and original novel documents remain in input/evidence.json. '
                                 'Read history or whole-episode documents only when they resolve a specific contradiction; do not exhaust the file list. '
                                 'Once current frames, request and selected reference support a decision, write the final output immediately. '
                                 'For edit, write the complete prompt.txt and decision.json, then finish immediately. Use one tool call per response; sequential Write calls are allowed. '
                                 'Read any host rejection in primary.rejections: each contains the exact failed validation, '
                                 'the prior decision and, for edits, an input/rejected-candidate-N.txt file with the complete rejected request. '
                                 'Use that feedback to correct the specific conflict while preserving source, dialogue, bindings and timing. '
                                 'Do not generate video. Write one proposal in this invocation; only the host may request a bounded revision, '
                                 f'at most {MAX_CANDIDATES} candidates including the first. Do not retry autonomously.\n',encoding='utf-8')
    deadline=time.monotonic()+600
    attempt=run_agent(run,config=config,timeout=600)
    produced=set(attempt.produced)
    terminal=protocol_failure(attempt)
    missing=['decision.json'] if 'decision.json' not in produced else []
    if (attempt.ok and 'decision.json' in produced and read_json(work/'output/decision.json',{}).get('action')=='edit'
            and 'prompt.txt' not in produced):
        missing.append('prompt.txt')
    remaining=int(deadline-time.monotonic())
    if (terminal or (attempt.ok and missing)) and remaining>0:
        recovery={'first_attempt':attempt.directory.name,'reason':terminal or 'missing_current_outputs',
                  'missing_files':missing,'remaining_seconds':remaining}
        atomic_write_json(work/'protocol-recovery.json',recovery)
        continuation=work/'continue-protocol.txt'
        problem=('The previous provider claimed a tool call without valid tool contents.' if terminal else
                 'The host received current files '+str(sorted(produced))+' but is missing required files '+str(missing)+'.')
        continuation.write_text('Continue this same repair task from its saved conversation or saved workspace evidence. '+problem+
                                ' A completion message does not substitute for actual output files. '
                                'Read prompt.txt for the original task if this is a fresh native context. '
                                'Keep already read evidence and existing valid outputs; open any required '
                                'images still missing. Use one valid tool call at a time, finish the complete required output files '
                                'and stop. Do not render, change source files, or repeat unrelated reading.')
        attempt=run_agent(run,config={**config,'extra_args':[*config['extra_args'],'--continue']},
                          timeout=remaining,prompt=continuation)
        produced.update(attempt.produced)
        recovery.update(continued_attempt=attempt.directory.name,ok=attempt.ok,produced=sorted(produced))
        atomic_write_json(work/'protocol-recovery.json',recovery)
    if not attempt.ok or 'decision.json' not in produced:
        raise AgentUnavailable('repair agent did not produce a current decision')
    answer=read_json(work/'output/decision.json',{})
    if answer.get('action') not in {'edit','retake','keep','stop','input','asset','audio'} or not answer.get('reason') or not answer.get('evidence'):
        raise ValueError('repair agent decision requires action, reason and evidence')
    answer.update(run=run,attempt=attempt.attempt if hasattr(attempt,'attempt') else attempt.directory.name)
    if answer['action']=='edit':
        if 'prompt.txt' not in produced:raise AgentUnavailable('repair agent did not produce a current prompt')
        answer['prompt']=(work/'output/prompt.txt').read_text(encoding='utf-8').strip()
        try:
            validate_request(clip,answer['prompt'])
            if answer['prompt']==clip[field].strip():raise ValueError('agent edit made no effective change')
        except ValueError as error:
            raise CandidateRejected(str(error),answer,stage='validate_request') from error
    return answer


def final_proposal(directory, plan, clip, decision):
    """Retain source and unrelated requests; only the accepted English and seed revision change."""
    notes=read_json(directory/'review_feedback.json',{})
    candidate=copy.deepcopy(plan)
    target=next(c for c in candidate['clips'] if c['clip_id']==clip['clip_id'])
    validate_request(clip,decision['prompt'])
    lane = request_lane(clip)
    field = 'prompt_h3' if lane == 'h3' else 'prompt'
    target[field]=decision['prompt']
    if lane == 'h3':target['prompt_h3_of']=h3_stamp(target,notes.get(clip['clip_id'],''))
    target['repair_take']=int(clip.get('repair_take',0))+1
    target['prompt_h3_agent' if lane == 'h3' else 'prompt_sd25_agent']={k:decision[k] for k in ('run','attempt','reason','evidence') if k in decision}
    return RepairProposal(result={'changed':[clip['clip_id']],'why':decision['reason']},
                          script=read_json(directory/'chapter_script.json',{}),plan=candidate,notes=notes,
                          changes={clip['clip_id']:decision})
