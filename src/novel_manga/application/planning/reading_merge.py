"""Grouped whole-book roster merge, using lean contracts and resumable job outputs."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import json
from pathlib import Path
import time

from novel_manga.llm.client import ask_json
from novel_manga.llm import usage
from novel_manga.story import lean_reading as lean
from novel_manga.util import atomic_write_json, read_json

PEOPLE_PER_JOB = 20
PLACES_PER_JOB = 40


def _validate(value, schema, field='answer'):
    """Enforce the existing lean field types before caching a completed result."""
    expected = schema.get('type')
    kinds = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool}
    if expected in kinds and type(value) is not kinds[expected]:
        raise ValueError(field+' must be '+expected)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(field+' has an invalid value')
    if expected == 'object':
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError(field+' lacks a required field')
        for key, rule in schema.get('properties', {}).items():
            if key in value:
                _validate(value[key], rule, field+'.'+key)
    elif expected == 'array':
        for item in value:
            _validate(item, schema['items'], field+'[]')


def _context(text, quote):
    position = text.find(quote) if quote else -1
    return text[max(0, position-100):position+400] if position >= 0 else ''


def _groups(rows):
    """Find proposals that overlap; the model, not string equality, decides identity."""
    groups = []
    for row in rows:
        names = {row['name'], *(row.get('aliases') or [])} - {''}
        touching = [g for g in groups if names & g[0]]
        if not touching:
            groups.append((names, [row]))
            continue
        members = [row]
        for group in touching:
            names |= group[0]; members.extend(group[1]); groups.remove(group)
        groups.append((names, members))
    return [members for names, members in groups]


def _related_names(names, material):
    """A name index supplies hints, not evidence for identities absent from this job."""
    text = json.dumps(material, ensure_ascii=False)
    return [name for name in names if name and name in text]


def _last_chapter(rows):
    return max((chapter for row in rows
                for field in ('listed', 'in_text', 'on_stage', 'speaks', 'chapters')
                for chapter in row.get(field, [])), default=1)


def _cache_inputs(declared):
    """Ignore unrelated index hints only when reusing a completed judgment.

    Fresh calls still carry the full index. Source rows, looks, model, prompt
    and schema remain exact dependencies; newly relevant names invalidate it.
    """
    result = deepcopy(declared)
    inputs = result.get('input', {})
    for field in ('global_name_index', 'global_location_names'):
        names = inputs.pop(field, None)
        if names is not None:
            inputs[field] = sorted(_related_names(names, inputs))
    return result


def merge_reading(novel_dir, candidates, texts, *, settings, workers=8):
    novel_dir = Path(novel_dir)
    folder = novel_dir/'lean/merge'
    metadata = read_json(novel_dir/'novel.json', {})
    title = metadata.get('title', novel_dir.name)
    scope = {'model': getattr(settings, 'model', ''),
             'candidates': json.loads(json.dumps(candidates)),
             'texts': {str(ch): text for ch, text in texts.items()}}
    state_path = folder/'run.json'
    state = read_json(state_path, {})
    if not state:
        state = {'inputs': scope, 'deadline': time.time()+7200}
        atomic_write_json(state_path, state)
    elif state.get('inputs') != scope:
        with state_path.with_suffix('.history.jsonl').open('a') as history:
            history.write(json.dumps(state, ensure_ascii=False)+'\n')
        state['inputs'] = scope
        atomic_write_json(state_path, state)
    deadline = state['deadline']

    def partitions(names, field, size):
        groups = state.setdefault(field, [])
        known = {name for group in groups for name in group}
        added = [name for name in names if name not in known]
        groups.extend(added[start:start+size] for start in range(0, len(added), size))
        atomic_write_json(state_path, state)
        return groups

    def call(job):
        job_id, prompt, inputs, schema = job
        path = folder/(job_id+'.json')
        declared = {'model': getattr(settings, 'model', ''), 'prompt': prompt,
                    'input': inputs, 'schema': schema}
        saved = read_json(path, {})
        if (saved.get('answer') is not None
                and _cache_inputs(saved.get('inputs', {})) == _cache_inputs(declared)):
            try:
                _validate(saved['answer'], schema)
            except ValueError:
                pass
            else:
                return saved['answer']
        remaining = deadline-time.time()
        if remaining <= 0:
            raise TimeoutError('Book merge window exhausted; finished groups retained')
        with usage.scope(novel=novel_dir.name, episode=job_id, stage='reading_merge'):
            answer = ask_json([{'type': 'text', 'text': prompt+'\n'+json.dumps(inputs, ensure_ascii=False, separators=(',', ':'))}],
                schema, name=job_id, settings=settings, max_tokens=16000,
                timeout=min(900, remaining), json_object=True)
        try:
            _validate(answer, schema)
        except ValueError:
            with (folder/(job_id+'.failed.jsonl')).open('a') as stream:
                stream.write(json.dumps({'inputs': declared, 'answer': answer}, ensure_ascii=False)+'\n')
            raise
        for key in schema['required']:
            if key in {'characters', 'locations'} and any(not r['name'].strip() for r in answer[key]):
                raise ValueError(job_id+': unnamed '+key+' row')
        if saved:
            with path.with_suffix('.history.jsonl').open('a') as history:
                history.write(json.dumps(saved, ensure_ascii=False)+'\n')
        atomic_write_json(path, {'inputs': declared, 'answer': answer})
        return answer

    def run(jobs):
        answers = {}
        iterator = iter(enumerate(jobs))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            active = {}
            def submit():
                item = next(iterator, None)
                if item is not None:
                    number, job = item
                    active[pool.submit(call, job)] = number
            for _ in range(max(1, workers)):
                submit()
            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    number = active.pop(future)
                    answers[number] = future.result()
                for _ in done:
                    submit()
        return [answers[n] for n in range(len(jobs))]

    forms = deepcopy(candidates.get('forms') or [])
    for row in forms:
        for quote in row.get('evidence', [])[:3]:
            text = texts.get(int(quote['chapter']), '')
            quote['original_context'] = _context(text, quote.get('quote', ''))
        row['evidence'] = row.get('evidence', [])[:3]
    names = [r['form'] for r in forms]
    by_name = {row['form']: row for row in forms}
    jobs = []
    for number, group in enumerate(partitions(names, 'people_groups', PEOPLE_PER_JOB)):
        subset = [by_name[name] for name in group if name in by_name]
        if not subset:
            continue
        assigned = {r['form'] for r in subset}
        material = {'assigned_forms': subset,
                    'looks': {n: rows for n, rows in candidates.get('looks', {}).items() if n in assigned}}
        rules = lean.MERGE_PROMPT.format(title=title, last=_last_chapter(subset), table='')
        jobs.append((f'merge_people_{number:04d}', rules+
            '\n只导出assigned_forms涉及且有原文依据的人物；global_name_index只供跨组查同指，'
            '不回抄索引。不同人物保留分开，未确定的外貌留待后续按章处理。',
            {'global_name_index': names, **material},
            lean.MERGE_SCHEMA))
    locations = list(candidates.get('locations', {}))
    for number, group in enumerate(partitions(locations, 'location_groups', PLACES_PER_JOB)):
        subset = [name for name in group if name in candidates['locations']]
        if not subset:
            continue
        material = {'assigned_locations': {n: {'chapters': candidates['locations'][n],
                    'sketches': candidates.get('sketches', {}).get(n, [])[:4]} for n in subset}}
        places_rule = lean.MERGE_LOCATIONS_PROMPT.format(title=title,
            last=_last_chapter(material['assigned_locations'].values()), table='')
        jobs.append((f'merge_locations_{number:04d}', places_rule+
            '\n只导出assigned_locations，保留不同房间和空间。',
            {'global_location_names': locations, **material},
            lean.MERGE_LOCATIONS_SCHEMA))
    answers = run(jobs)
    characters = [row for answer in answers for row in answer.get('characters', [])]
    groups = _groups(characters)
    reconciliations = []
    for number, members in enumerate(groups):
        if len(members) > 1:
            rules = lean.MERGE_PROMPT.format(title=title, last=_last_chapter(members), table='')
            reconciliations.append((f'merge_overlap_{number:04d}', rules+
                '\n核对跨组重名/别名提议。按原文证据决定同指或保留不同主体，不以名字重叠直接合并。'
                '只输出这些proposals涉及的人物，保留已有的有据外貌片段和未决项。',
                {'proposals': members, 'source_evidence': [
                    {'chapter': int(e['chapter']), 'quote': e['quote'],
                     'context': _context(texts.get(int(e['chapter']), ''), e['quote'])}
                    for row in members for e in row.get('evidence', [])]},
                lean.MERGE_SCHEMA))
    resolved = iter(run(reconciliations))
    characters = [row for members in groups for row in
                  (next(resolved)['characters'] if len(members) > 1 else members)]
    export = {'characters': characters,
              'locations': [row for answer in answers for row in answer.get('locations', [])],
              'look_bindings': [row for answer in answers for row in answer.get('look_bindings', [])]}
    export = lean.bind_export_looks(export, candidates)
    export['pending_changes'] = candidates.get('changes', [])
    atomic_write_json(novel_dir/'lean/export.json', export)
    return export
