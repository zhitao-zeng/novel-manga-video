"""Freeze source facts or an existing screenplay; never plan or generate assets."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import tempfile

from novel_manga.episodes import chapter_of
from novel_manga.util import atomic_write_json, read_json
from .context import EpisodeContext
from .session import SKILL_SOURCE


BOOK_FILES = ('novel.json', 'story_bible.json', 'bible_aliases.json', 'bible_growth.json', 'entity_index.json',
              'reading_cast.json', 'cast_index.json', 'style.json', 'profile.json',
              'visual_grammar.json', 'chat_screen.json', 'asr_aliases.json', 'entity/entities.json',
              'entity/claims.json', 'entity/types.json')
READING_FILES = ('identity_source_reading.json', 'identity_context.json', 'source_reading.json',
                 'source_speaker_contract.json')
SCRIPT_FILES = ('chapter_script.json', 'clip_plan.json', 'episode_plan.json', 'scene_outline.json',
                'scene_script.json', 'clip_overrides.json', 'visor_states.json', 'posture_states.json')
SKILL_REFERENCES = {
    'output_contract': 'references/output-contract.md',
    'authoring': 'references/authoring.md',
    'h3_format': 'references/h3-format.md',
    'official_h3_guide': 'references/official/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md',
    'review_and_repair': 'references/review-and-repair.md',
    'case_lessons': 'references/case-lessons.md',
}


def _install_skill(ctx):
    """Make this code revision's rules available through the episode's sole mount."""
    relative = Path('.claude/skills/episode-production')
    files = ('SKILL.md', *SKILL_REFERENCES.values(),
             'references/official/LICENSE', 'references/official/NOTICE')
    for name in files:
        source = SKILL_SOURCE / name
        if not source.is_file():
            raise ValueError(f'episode skill resource is missing: {name}')
        target = ctx.work_dir / relative / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return str(relative / 'SKILL.md'), {key: str(relative / name)
                                        for key, name in SKILL_REFERENCES.items()}


def _copy(source, target):
    source, target = Path(source), Path(target)
    if target.is_file():
        return True
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _local_directory(path):
    """Detach this run's directory, retaining links to unchanged shared files."""
    if path.is_symlink():
        shared = path.resolve()
        path.unlink()
        path.mkdir()
        for child in shared.iterdir():
            (path / child.name).symlink_to(child, target_is_directory=child.is_dir())
    else:
        path.mkdir(parents=True, exist_ok=True)


def _snapshot_file(ctx, source, relative):
    """Replace a local snapshot file without writing through a shared link."""
    source = Path(source)
    if not source.is_file():
        return False
    target = ctx.novel_dir / relative
    current = ctx.novel_dir
    for part in Path(relative).parent.parts:
        current /= part
        _local_directory(current)
    _replace_file(source, target)
    return True


def _replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        path = Path(temporary.name)
    try:
        shutil.copy2(source, path)
        os.replace(path, target)
    finally:
        path.unlink(missing_ok=True)


def _source(ctx, segments):
    metadata = read_json(ctx.source_novel_dir / 'novel.json', {})
    chapter = chapter_of(ctx.source_episode_dir.name)
    reason = None
    if metadata.get('source'):
        try:
            from novel_manga.ingest import read_novel
            path = Path(metadata['source']).expanduser()
            if not path.is_absolute():
                project = ctx.project_root / path
                path = project if project.is_file() else ctx.source_novel_dir / path
            entries = [r for r in metadata.get('chapters', []) if int(r['index']) == chapter]
            novel = read_novel(path, metadata.get('novel_id') or ctx.source_novel_dir.name,
                               chapters=entries or None)
            episode = next(ep for ep in novel.episodes if ep.index == chapter)
            return episode.source_text, episode.source_title, 'registered_source', None
        except (OSError, ValueError, KeyError, StopIteration) as error:
            reason = f'{type(error).__name__}: {error}'
    text = '\n'.join(str(r.get('text') or '') for r in segments if isinstance(r, dict)).strip()
    if not text:
        raise ValueError('episode has no readable original source or existing source segments')
    title = next((r.get('title') for r in metadata.get('chapters', [])
                  if int(r['index']) == chapter), ctx.source_episode_dir.name)
    return text, title, 'segments_fallback', reason


def _references(plan):
    for clip in plan.get('clips', []):
        yield from clip.get('references') or []
        for key in ('character_references', 'voice_references'):
            yield from (clip.get('inner_voice') or {}).get(key) or []


def _candidate_references(ctx, text, plan, script, *, bound_plan=False, fresh=False):
    from novel_manga.application.identity.phases import load_phases, phase_for
    existing=ctx.input_mode=='script' or bound_plan
    phases = load_phases(ctx.novel_dir) if not existing else {}
    chapter = chapter_of(ctx.source_episode_dir.name)

    def phase_references(name):
        phase = phase_for(phases, name, chapter)
        if not phase or not phase.get('asset_id'):
            return None
        folder = Path('series_assets/characters')/str(phase['asset_id'])
        paths = [folder/'turnaround.jpeg']
        expressions = folder/'expressions.jpeg'
        roots = (ctx.source_novel_dir,) if fresh else (ctx.novel_dir, ctx.asset_snapshot_dir)
        if any((root/expressions).is_file() for root in roots):
            paths.append(expressions)
        # A missing required phase must be reported by the normal input check;
        # silently supplying the base face would change the current character.
        return [{'asset_id': phase['asset_id'], 'role': 'character', 'name': name,
                 'path': str(path), 'phase': phase.get('label') or phase['asset_id']} for path in paths]

    references = [copy.deepcopy(r) for r in _references(plan)
                  if existing or r.get('role') != 'scene_state']
    references = [candidate for ref in references for candidate in
                  ((phase_references(ref.get('name')) if ref.get('role') == 'character' else None) or [ref])]
    names = {r.get('name') for r in references}
    if existing:
        names.update(n for shot in script.get('shots', []) for n in shot.get('characters', []))
    aliases = read_json(ctx.novel_dir / 'bible_aliases.json', {})
    names.update(name for alias, name in aliases.items() if alias and alias in text)
    metadata_root = ctx.source_novel_dir if fresh else ctx.novel_dir
    manifest = read_json(metadata_root / 'series_assets/manifest.json', {})
    for group, role in (('characters', 'character'), ('locations', 'location'), ('props', 'prop')):
        for row in manifest.get(group, []):
            name = str(row.get('name') or '')
            short = name.split('：', 1)[0]
            if not name or not (name in names or short in names or short in text):
                continue
            names.add(name)
            if role == 'character' and (current := phase_references(name)):
                references.extend(current)
                continue
            paths = [row[field] for field in ('primary_image', 'secondary_image') if row.get(field)]
            if paths:
                folder = metadata_root / Path(paths[0]).parent
                # The folder also holds rejected generations and backups. Only
                # the published views are candidates; manifest paths already
                # cover explicitly registered nonstandard filenames.
                paths.extend(str(p.relative_to(metadata_root)) for p in
                             (folder/name for name in ('turnaround.jpeg', 'expressions.jpeg',
                                                        'establishing.jpeg', 'detail.jpeg')) if p.is_file())
            for path in dict.fromkeys(paths):
                references.append({'asset_id': row['asset_id'], 'role': role,
                    'name': short if role == 'location' else name, 'path': path})
    voices = read_json(metadata_root / 'series_assets/voices/voices.json', {})
    for name in voices:
        if name in names or name in text:
            references.append({'asset_id': f'voice_{name}', 'role': 'voice', 'name': name,
                               'path': f'series_assets/voices/{name}.wav'})
    return references


def _catalog(ctx, references, *, fresh=False):
    assets, missing, by_reference, ids = [], [], {}, set()
    for ref in references:
        if ref.get('role') not in {'character', 'location', 'prop', 'voice', 'scene_state'} or not ref.get('path'):
            continue
        original = Path(ref['path'])
        source = original if original.is_absolute() else ctx.source_novel_dir / original
        key = (str(source.resolve()), ref['role'], ref.get('name', ''))
        if key in by_reference:
            continue
        logical = source.relative_to(ctx.source_novel_dir) if source.is_relative_to(ctx.source_novel_dir) else original
        if not logical.is_absolute() and logical.parts and logical.parts[0] == 'series_assets' and '..' not in logical.parts:
            relative = logical
        else:
            relative = Path('series_assets/episode_refs') / f'{ctx.source_episode_dir.name}-{ctx.input_mode}' / f'asset_{len(assets)+1:03d}{source.suffix}'
        local_target = ctx.novel_dir / relative
        target = local_target if local_target.is_file() else ctx.asset_snapshot_dir / relative
        if not source.is_file() and not original.is_absolute():
            local = ctx.novel_dir/original
            if local.is_file():
                source = local
        copied = _snapshot_file(ctx, source, relative) if fresh else _copy(source, target)
        if not copied:
            missing.append({'role': ref['role'], 'name': ref.get('name'), 'path': str(original),
                            'asset_id': ref.get('asset_id')})
            continue
        if fresh:
            target = local_target
            for suffix in ('.request.json', '.task.json'):
                sidecar = source.with_name(source.name + suffix)
                _snapshot_file(ctx, sidecar, relative.with_name(relative.name + suffix))
            if ref['role'] == 'character':
                _snapshot_file(ctx, source.parent / 'spec.json', relative.parent / 'spec.json')
        ident = str(ref.get('id') or ref.get('asset_id') or source.stem)
        if ident in ids:
            ident = f'{ident}_{source.stem}'
        if ident in ids:
            ident = f'{ident}_{len(assets)+1}'
        ids.add(ident)
        picture = ctx.work_dir / 'input/refs' / f'{ident}{source.suffix}'
        if fresh:
            _replace_file(target, picture)
        else:
            _copy(target, picture)
        entry = {**copy.deepcopy(ref), 'id': ident, 'asset_id': ref.get('asset_id') or ident,
                 'path': str(relative), 'image': str(picture.relative_to(ctx.work_dir))}
        assets.append(entry)
        by_reference[key] = entry
    return assets, missing, by_reference


def _rebase_references(ctx, plan, catalog):
    copied = copy.deepcopy(plan)
    for ref in _references(copied):
        original = Path(ref['path'])
        source = original if original.is_absolute() else ctx.source_novel_dir / original
        row = catalog.get((str(source.resolve()), ref['role'], ref.get('name', '')))
        if row:
            ref['path'] = row['path']
    return copied


def _pack_existing(ctx, script):
    if not script.get('shots') or not (ctx.novel_dir / 'story_bible.json').is_file():
        raise ValueError('missing plan: deterministic packing requires bound shots and story_bible.json')
    from novel_manga.application.packing.context import load_context
    from novel_manga.application.packing.service import compile_plan
    try:
        context = load_context(ctx.episode_dir, ctx.novel_dir / 'story_bible.json',
                               limits={'max_clip_seconds': ctx.clip_seconds_max,
                                       'soft_cut_seconds': ctx.clip_seconds_max * .6, 'max_stages': 3})
        plan, decisions = compile_plan(script, context)
    except (ValueError, KeyError, OSError) as error:
        raise ValueError(f'missing plan: deterministic packing could not use the existing script: {error}') from error
    atomic_write_json(ctx.episode_dir / 'clip_plan.json', plan)
    atomic_write_json(ctx.episode_dir / 'pack_decisions.json', decisions)
    return plan


def _request_draft(ctx, plan):
    """Give the author an editable copy of existing requests and exact missing work."""
    from .contracts import validate_existing_request
    from .requests import compose_request
    from novel_manga.story.h3 import positive_direction_issues
    info_path = ctx.work_dir / 'input/request-draft.json'
    if info_path.is_file():
        return read_json(info_path)
    output = ctx.work_dir / 'output/requests.json'
    if not output.exists():
        atomic_write_json(output, {'prompts': {c['clip_id']: c.get('prompt_h3') or ''
            for c in plan.get('clips', []) if c.get('kind') == 'video'}})
    prompts = read_json(output, {}).get('prompts', {})
    reusable, errors, warnings = [], {}, {}
    for clip in plan.get('clips', []):
        if clip.get('kind') != 'video':
            continue
        cid = clip['clip_id']
        prompt = prompts.get(cid)
        try:
            prompt = compose_request(clip, prompt)
            validate_existing_request(clip, prompt)
        except (ValueError, KeyError, TypeError) as error:
            errors[cid] = str(error)
        else:
            reusable.append(cid)
        if isinstance(prompt, str) and (advice := positive_direction_issues(prompt)):
            warnings[cid] = advice
    info = {'file': 'output/requests.json', 'reusable': reusable, 'needs_work': errors,
            'style_warnings': warnings,
            'scope': 'Initial structural check only; inspect source and image conflicts. '
                     'Reuse unchanged requests. Style advice alone does not require rewriting.'}
    atomic_write_json(info_path, info)
    return info


def _preparation_exception(ctx, chapter):
    existing = 'input/preparation/exception.json'
    if ctx.input_mode == 'script' and (ctx.work_dir/existing).is_file():
        return existing
    if (ctx.run_dir/'prepared.json').is_file():
        return None
    saved = read_json(ctx.source_novel_dir/f'h3_preparation/episodes/{chapter}.json', {})
    failed_plan = ctx.source_episode_dir/'planning_failed.json'
    if saved.get('status') not in {'needs_source', 'needs_replan', 'needs_repair', 'error'} and not failed_plan.is_file():
        return None
    files = []
    names = ['planning_failed.json', 'chapter_script.json', 'clip_plan.json', 'episode_plan.json',
             'pre_render_story_audit.json']
    for pattern in ('response_attempt_*.raw.json', 'compression_attempt_*.json'):
        names.extend(path.name for path in sorted(ctx.source_episode_dir.glob(pattern)))
    for name in names:
        target = ctx.work_dir/'input/preparation'/name
        if _copy(ctx.source_episode_dir/name, target):
            files.append(str(target.relative_to(ctx.work_dir)))
    path = 'input/preparation/exception.json'
    if not (ctx.work_dir/path).exists():
        atomic_write_json(ctx.work_dir/path, {
            'status': saved.get('status', 'planning_failed'),
            'failure': {k: v for k, v in saved.items() if k in
                        {'reason', 'audit', 'blocks', 'request_conflicts', 'missing_assets'}},
            'files': files,
            'authority': 'Provisional failed production inputs. Reuse valid choices; repair against original source.'})
    return path


def prepare_inputs(ctx: EpisodeContext):
    fresh = not (ctx.run_dir / 'inputs.json').is_file()
    for directory in (ctx.run_dir, ctx.novel_dir, ctx.episode_dir, ctx.work_dir / 'input/refs',
                      ctx.work_dir / 'output', ctx.asset_snapshot_dir / 'series_assets'):
        directory.mkdir(parents=True, exist_ok=True)
    skill, skill_references = _install_skill(ctx)
    asset_link = ctx.novel_dir / 'series_assets'
    if not asset_link.exists():
        asset_link.symlink_to(ctx.asset_snapshot_dir / 'series_assets', target_is_directory=True)
    for name in BOOK_FILES:
        _copy(ctx.source_novel_dir / name, ctx.novel_dir / name)
    if not (ctx.novel_dir / 'profile.json').is_file():
        atomic_write_json(ctx.novel_dir / 'profile.json', read_json(ctx.novel_dir / 'novel.json', {}).get('profile', {}))
    for name in ('manifest.json', 'phases.json', 'voices/voices.json'):
        _copy(ctx.source_novel_dir / 'series_assets' / name, ctx.asset_snapshot_dir / 'series_assets' / name)
        if fresh:
            _snapshot_file(ctx, ctx.source_novel_dir / 'series_assets' / name,
                           Path('series_assets') / name)
    _copy(ctx.source_episode_dir / 'segments.json', ctx.episode_dir / 'segments.json')
    segments = read_json(ctx.episode_dir / 'segments.json', [])
    source_path = ctx.run_dir / 'source.txt'
    if source_path.is_file():
        text = source_path.read_text(encoding='utf-8')
        old = read_json(ctx.work_dir / 'input/manifest.json', {})
        title, source_kind, source_error = old.get('title', ctx.source_episode_dir.name), old.get('source_kind', 'snapshot'), old.get('source_error')
    else:
        text, title, source_kind, source_error = _source(ctx, segments)
        source_path.write_text(text, encoding='utf-8')
    _copy(source_path, ctx.work_dir / 'input/source.txt')
    if not segments:
        segments = [{'segment_id': f'{ctx.source_episode_dir.name}_s001', 'text': text}]
        atomic_write_json(ctx.episode_dir / 'segments.json', segments)
    atomic_write_json(ctx.work_dir / 'input/segments.json', segments)
    reading = []
    chapter = chapter_of(ctx.source_episode_dir.name)
    exception = _preparation_exception(ctx, chapter)
    from novel_manga.application.identity.reading_locations import for_chapter
    locations = for_chapter(ctx.source_novel_dir, chapter)
    if locations:
        path = 'input/reading/locations.json'
        atomic_write_json(ctx.work_dir/path, locations)
        reading.append(path)
    extracted = read_json(ctx.source_novel_dir/f'lean/extract/ch-{chapter:03d}.json', {})
    if extracted.get('chapter') == chapter and isinstance(extracted.get('answer'), dict):
        path = 'input/reading/chapter-extract.json'
        if not (ctx.work_dir/path).is_file():
            atomic_write_json(ctx.work_dir/path, {'chapter': chapter, 'answer': extracted['answer']})
        reading.append(path)
    if ctx.input_mode == 'source':
        from novel_manga.application.identity.phases import load_phases, phase_for
        phases = load_phases(ctx.novel_dir)
        current = {name: phase for name in phases if (phase := phase_for(phases, name, chapter))}
        path = 'input/reading/character-phases.json'
        if current or (ctx.work_dir/path).is_file():
            atomic_write_json(ctx.work_dir/path, {'chapter': chapter, 'characters': current})
            reading.append(path)
    batch_adopted=((ctx.episode_dir/'batch-production.json').exists() and (ctx.run_dir/'prepared.json').exists()
                   and (ctx.episode_dir/'clip_plan.json').exists())
    bound=ctx.input_mode=='script' or batch_adopted
    for name in READING_FILES:
        _copy(ctx.source_episode_dir / name, ctx.episode_dir / name)
        if bound or name != 'identity_context.json':
            target = ctx.work_dir / 'input/reading' / name
            if _copy(ctx.episode_dir / name, target):
                reading.append(str(target.relative_to(ctx.work_dir)))
    script, plan = {}, {}
    if ctx.input_mode == 'script':
        for name in SCRIPT_FILES:
            _copy(ctx.source_episode_dir / name, ctx.episode_dir / name)
    if bound:
        script = read_json(ctx.episode_dir / 'chapter_script.json', {})
        plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    # Identity metadata points to the copied files, with the original reading
    # facts retained; this is path rebinding, not a new identity decision.
    identity = read_json(ctx.episode_dir / 'identity_context.json', {})
    if identity:
        from novel_manga.application.identity.store import chapter_inputs
        current_inputs=chapter_inputs(ctx.episode_dir)
        if identity.get('inputs') != current_inputs:
            identity['inputs'] = current_inputs
            atomic_write_json(ctx.episode_dir / 'identity_context.json', identity)
    if ctx.input_mode == 'script' and not plan:
        _catalog(ctx, _candidate_references(ctx, text, {}, script, fresh=fresh), fresh=fresh)
        plan = _pack_existing(ctx, script)
    too_long = [c.get('clip_id') for c in plan.get('clips', []) if c.get('kind', 'video') == 'video'
                and float(c.get('request_seconds') or 0) > ctx.clip_seconds_max]
    if too_long:
        raise ValueError(f'H3 requires repacking this existing clip plan to {ctx.clip_seconds_max:g}s clips: '
                         + ', '.join(map(str, too_long)) + '; the Agent input step does not change saved timing')
    selected = plan if bound else read_json(ctx.source_episode_dir / 'clip_plan.json', {})
    refs = _candidate_references(ctx, text, selected, script, bound_plan=batch_adopted, fresh=fresh)
    extras = {r['id']: r for r in read_json(ctx.run_dir/'draft-assets.json', [])}
    library = ctx.asset_snapshot_dir/'series_assets/draft-assets'
    library_rows = read_json(library/'index.json', [])
    if library_rows:
        from .input_assets import style_key
        current_style = style_key(ctx)
    for row in library_rows:
        if row['name'] in text and row.get('style_key') == current_style:
            accepted = read_json(library/row['id']/'accepted.json', {})
            if accepted:
                extras.setdefault(row['id'], accepted)
    if extras and not (ctx.novel_dir/'series_assets/draft-assets').exists():
        (ctx.novel_dir/'series_assets/draft-assets').symlink_to(library, target_is_directory=True)
    if extras:
        from .input_assets import register_metadata
        register_metadata(ctx, list(extras.values()))
    assets, missing, mapping = _catalog(ctx, [*refs, *extras.values()], fresh=fresh)
    atomic_write_json(ctx.run_dir / 'catalog.json', assets)
    atomic_write_json(ctx.work_dir / 'input/catalog.json', {'assets': assets})
    for name in ('story_bible.json', 'bible_aliases.json', 'reading_cast.json', 'profile.json'):
        _copy(ctx.novel_dir / name, ctx.work_dir / 'input' / name)
    authoritative = []
    if bound:
        if not (ctx.work_dir / 'input/clip_plan.json').is_file():
            plan = _rebase_references(ctx, plan, mapping)
            atomic_write_json(ctx.episode_dir / 'clip_plan.json', plan)
            atomic_write_json(ctx.work_dir / 'input/clip_plan.json', plan)
        if batch_adopted:
            atomic_write_json(ctx.work_dir/'input/clip_plan.json',plan)
            atomic_write_json(ctx.work_dir/'input/chapter_script.json',script)
        else:
            _copy(ctx.episode_dir / 'chapter_script.json', ctx.work_dir / 'input/chapter_script.json')
        authoritative = ['input/chapter_script.json', 'input/clip_plan.json']
    target = ctx.target_seconds
    if ctx.input_mode == 'script' and target is None:
        total = sum(float(c.get('request_seconds') or 0) for c in plan.get('clips', []))
        target = (total, total) if total else None
    manifest = {'input_mode': ctx.input_mode, 'source': 'input/source.txt', 'source_kind': source_kind,
        'source_complete': source_kind == 'registered_source', 'source_error': source_error,
        'segments': 'input/segments.json', 'catalog': 'input/catalog.json', 'reading': reading,
        'chapter': chapter_of(ctx.source_episode_dir.name), 'title': title,
        'profile': 'input/profile.json', 'target_seconds': list(target) if target else None,
        'skill': skill, 'skill_references': skill_references,
        'format_reference': skill_references['h3_format'],
        'runtime_rules': skill_references['review_and_repair'],
        'clip_seconds_max': ctx.clip_seconds_max, 'authoritative_inputs': authoritative,
        'authority': ('Existing native screenplay and clip plan are adopted; preserve current media and use scoped repair tools.' if batch_adopted else 'Original source; Agent owns screenplay, cutting and final requests.'
                      if ctx.input_mode == 'source' else
                      'Existing screenplay and clip plan are authoritative: preserve shots, dialogue, grouping, seconds and ordered references; write final requests only.'),
        'output': 'output/episode.json' if ctx.input_mode == 'source' else 'output/requests.json',
        'missing_assets': missing,
        'missing_inputs': [] if (ctx.novel_dir / 'story_bible.json').is_file() else ['story_bible.json'],
        'ready': not missing and (ctx.novel_dir / 'story_bible.json').is_file()}
    if exception:
        manifest['preparation_exception'] = exception
    if ctx.input_mode == 'script':
        manifest['request_draft'] = _request_draft(ctx, plan)
    atomic_write_json(ctx.work_dir / 'input/manifest.json', manifest)
    atomic_write_json(ctx.run_dir / 'inputs.json', manifest)
    return manifest


def refresh_character_inputs(ctx: EpisodeContext, asset_ids):
    """Explicitly refresh selected actors in one existing run; never replan it."""
    selected = list(dict.fromkeys(asset_ids))
    if not selected:
        raise ValueError('choose at least one character asset_id to refresh')
    if not (ctx.run_dir / 'inputs.json').is_file():
        raise ValueError('prepare the episode inputs before refreshing selected characters')
    source_manifest = read_json(ctx.source_novel_dir / 'series_assets/manifest.json', {})
    source_rows = {row['asset_id']: row for row in source_manifest.get('characters', [])}
    unknown = [ident for ident in selected if ident not in source_rows]
    if unknown:
        raise ValueError('unknown source character asset_id: ' + ', '.join(unknown))
    source_bible = read_json(ctx.source_novel_dir / 'story_bible.json')
    source_characters = {row['name']: row for row in source_bible.get('characters', [])}
    catalog = read_json(ctx.run_dir / 'catalog.json', [])
    references = []
    for ident in selected:
        row = source_rows[ident]
        name = row['name']
        if name not in source_characters:
            raise ValueError(f'{ident}: source story_bible.json has no character {name}')
        paths = [row[key] for key in ('primary_image', 'secondary_image') if row.get(key)]
        if not paths:
            raise ValueError(f'{ident}: source manifest has no published character image')
        folder = Path(paths[0]).parent
        paths.extend(str(folder / filename) for filename in ('turnaround.jpeg', 'expressions.jpeg')
                     if (ctx.source_novel_dir / folder / filename).is_file())
        prior = {asset['path']: asset for asset in catalog
                 if asset.get('role') == 'character' and asset.get('asset_id') == ident}
        for path in dict.fromkeys(paths):
            if not (ctx.source_novel_dir / path).is_file():
                raise ValueError(f'{ident}: source character image is missing: {path}')
            references.append({**prior.get(path, {}), 'asset_id': ident,
                               'role': 'character', 'name': name, 'path': path})
        if any(path not in paths for path in prior):
            raise ValueError(f'{ident}: published image paths changed; preserve or repair the saved references first')

    # The source selection is valid before any copy. Detach only this run's
    # character directories; linked files belonging to other actors stay frozen.
    refreshed, missing, _ = _catalog(ctx, references, fresh=True)
    if missing:
        raise ValueError('selected character images could not be copied')
    local_manifest = read_json(ctx.novel_dir / 'series_assets/manifest.json', {})
    old_names = {row['asset_id']: row.get('name')
                 for row in local_manifest.get('characters', [])}
    remaining = set(selected)
    rows = []
    for row in local_manifest.get('characters', []):
        ident = row.get('asset_id')
        rows.append(copy.deepcopy(source_rows[ident]) if ident in remaining else row)
        remaining.discard(ident)
    rows.extend(copy.deepcopy(source_rows[ident]) for ident in selected if ident in remaining)
    local_manifest['characters'] = rows
    atomic_write_json(ctx.novel_dir / 'series_assets/manifest.json', local_manifest)

    replacements = {old_names.get(ident) or source_rows[ident]['name']:
                    copy.deepcopy(source_characters[source_rows[ident]['name']]) for ident in selected}
    bible = read_json(ctx.novel_dir / 'story_bible.json')
    characters = []
    for row in bible.get('characters', []):
        characters.append(replacements.pop(row['name'], row))
    characters.extend(replacements.values())
    bible['characters'] = characters
    atomic_write_json(ctx.novel_dir / 'story_bible.json', bible)
    atomic_write_json(ctx.work_dir / 'input/story_bible.json', bible)

    replacements = {row['path']: row for row in refreshed}
    merged, used = [], set()
    for row in catalog:
        if row.get('role') == 'character' and row.get('asset_id') in selected:
            current = {**row, **replacements[row['path']]}
            # Separate catalog entries can share a picture (for example an
            # authored alias). Retain their ids and sandbox paths individually.
            for key in ('id', 'image', 'tag', 'phase'):
                if key in row:
                    current[key] = row[key]
            _replace_file(ctx.novel_dir / current['path'], ctx.work_dir / current['image'])
            merged.append(current)
            used.add(row['path'])
        else:
            merged.append(row)
    merged.extend(row for path, row in replacements.items() if path not in used)
    atomic_write_json(ctx.run_dir / 'catalog.json', merged)
    offered = read_json(ctx.work_dir / 'input/catalog.json', {})
    atomic_write_json(ctx.work_dir / 'input/catalog.json', {**offered, 'assets': merged})
    for path in (ctx.run_dir / 'inputs.json', ctx.work_dir / 'input/manifest.json'):
        manifest = read_json(path)
        manifest['missing_assets'] = [row for row in manifest.get('missing_assets', [])
                                      if row.get('asset_id') not in selected]
        manifest['ready'] = not manifest['missing_assets'] and not manifest.get('missing_inputs')
        atomic_write_json(path, manifest)
    return {'refreshed_characters': selected, 'names': [source_rows[ident]['name'] for ident in selected]}
