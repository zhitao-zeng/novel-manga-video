"""Evidence export and final-request proposals through the existing sandbox executor."""
from __future__ import annotations
import copy
import json
import re
import shutil
import time
from pathlib import Path

from novel_manga.application.agents.sandbox import load_config, run_agent
from novel_manga.application.configuration import project_root
from novel_manga.application.profiles import h3_stamp
from novel_manga.repair.proposal import RepairProposal
from novel_manga.story.h3 import request_issues
from novel_manga.story.dialogue import dialogue_punctuation
from novel_manga.application.review.evidence import clip_frames
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json, atomic_write_json

SECTIONS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
            'overall_soundscape', 'non_diegetic_music')


def enabled(directory: Path) -> bool:
    return read_json(directory.parent/'profile.json', {}).get('repair_backend') == 'sandbox_agent'


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
    return clip_frames(video, target, 4), 'host_uniform_10_to_90_percent'


def propose(directory: Path, clip: dict, verdict: dict, *, config=None):
    """No production writes. Container sees evidence copies, never the production directory."""
    config = config or load_config()
    sections(clip.get('prompt_h3', ''))
    run = f'repair-{directory.name}-{clip["clip_id"]}-{time.time_ns()}'
    work = Path(config['runs_root'])/run
    inputs = work/'input'; inputs.mkdir(parents=True)
    skill = work/'.claude/skills/video-repair'; skill.mkdir(parents=True)
    shutil.copy2(project_root()/'skills/video-repair/SKILL.md', skill/'SKILL.md')
    (inputs/'prompt.txt').write_text(clip['prompt_h3'], encoding='utf-8')
    script = read_json(directory/'chapter_script.json', {})
    indexes = clip.get('shot_indexes') or []
    near = {i+d for i in indexes for d in (-1,0,1)}
    shots = [s for i,s in enumerate(script.get('shots', []),1) if s.get('index',i) in near]
    media = read_json(directory/'thin_media_report.json', {})
    row = next((r for r in media.get('clips',[]) if r['clip_id']==clip['clip_id']), {})
    history = read_json(directory/'repair_history/history.json', {})
    from novel_manga.story.h3 import REQUEST_CONTENT_RULES
    evidence = {'request_rules': REQUEST_CONTENT_RULES, 'clip': clip, 'shots_and_neighbours': shots, 'segments':read_json(directory/'segments.json', []),
                'verdict':verdict, 'media':row, 'feedback':read_json(directory/'review_feedback.json', {}).get(clip['clip_id'],''),
                'observations':history.get('observations',{}).get(clip['clip_id'],[]),
                'trials':[{k:t.get(k) for k in ('id','method','changes','expected_notes')}
                          for t in history.get('trials',[]) if clip['clip_id'] in t.get('clips',[])],
                'references':[], 'takes':[]}
    for i,ref in enumerate(clip.get('references',[]),1):
        source = directory.parent/ref['path']; target = inputs/f'reference-{i}{source.suffix}'
        shutil.copy2(source,target)
        evidence['references'].append({**ref,'path':str(target.relative_to(work))})
    video = (row.get('selected') or {}).get('video')
    videos = ([Path(video)] if video else []) + sorted((directory/'repair_history/takes'/clip['clip_id']).glob('*/clip.mp4'))[-3:]
    for i,source in enumerate(dict.fromkeys(videos),1):
        if not source.is_file(): continue
        target=inputs/f'take-{i}'; target.mkdir()
        for name in ('clip.mp4','request.json','asr.json'):
            path=source if name=='clip.mp4' else source.parent/name
            if path.is_file():shutil.copy2(path,target/name)
        evidence['takes'].append({'path':str(target.relative_to(work)), 'current':str(source)==video})
    atomic_write_json(inputs/'evidence.json',evidence)
    (work/'prompt.txt').write_text('Read .claude/skills/video-repair/SKILL.md. Diagnose this failed clip from input/. '
                                 'Write output/decision.json and, only for edit, output/prompt.txt. '
                                 'Do not generate video. One proposal, no retries.\n',encoding='utf-8')
    attempt=run_agent(run,config=config,timeout=600)
    if not attempt.ok or 'decision.json' not in attempt.produced:
        raise ValueError('repair agent did not produce a current decision')
    answer=read_json(work/'output/decision.json',{})
    if answer.get('action') not in {'edit','retake','keep','stop'} or not answer.get('reason') or not answer.get('evidence'):
        raise ValueError('repair agent decision requires action, reason and evidence')
    answer.update(run=run,attempt=attempt.attempt if hasattr(attempt,'attempt') else attempt.directory.name)
    if answer['action']=='edit':
        if 'prompt.txt' not in attempt.produced:raise ValueError('repair agent did not produce a current prompt')
        answer['prompt']=(work/'output/prompt.txt').read_text(encoding='utf-8').strip()
        validate_prompt(clip,answer['prompt'])
        if answer['prompt']==clip['prompt_h3'].strip():raise ValueError('agent edit made no effective change')
    return answer


def final_proposal(directory, plan, clip, decision):
    """Retain source and unrelated requests; only the accepted English and seed revision change."""
    notes=read_json(directory/'review_feedback.json',{})
    candidate=copy.deepcopy(plan)
    target=next(c for c in candidate['clips'] if c['clip_id']==clip['clip_id'])
    validate_prompt(clip,decision['prompt'])
    target['prompt_h3']=decision['prompt']
    target['prompt_h3_of']=h3_stamp(target,notes.get(clip['clip_id'],''))
    target['repair_take']=int(clip.get('repair_take',0))+1
    target['prompt_h3_agent']={k:decision[k] for k in ('run','attempt','reason','evidence') if k in decision}
    return RepairProposal(result={'changed':[clip['clip_id']],'why':decision['reason']},
                          script=read_json(directory/'chapter_script.json',{}),plan=candidate,notes=notes,
                          changes={clip['clip_id']:decision})
