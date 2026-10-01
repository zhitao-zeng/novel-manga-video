"""Freeze source facts or an existing screenplay; never plan or generate assets."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil

from novel_manga.episodes import chapter_of
from novel_manga.util import atomic_write_json, read_json
from .context import EpisodeContext
from .session import SKILL_SOURCE


BOOK_FILES = ('novel.json', 'story_bible.json', 'bible_aliases.json', 'entity_index.json',
              'reading_cast.json', 'cast_index.json', 'style.json', 'profile.json',
              'visual_grammar.json', 'chat_screen.json', 'entity/entities.json',
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


def _candidate_references(ctx, text, plan, script):
    references = [copy.deepcopy(r) for r in _references(plan)
                  if ctx.input_mode == 'script' or r.get('role') != 'scene_state']
    names = {r.get('name') for r in references}
    if ctx.input_mode == 'script':
        names.update(n for shot in script.get('shots', []) for n in shot.get('characters', []))
    aliases = read_json(ctx.novel_dir / 'bible_aliases.json', {})
    names.update(name for alias, name in aliases.items() if alias and alias in text)
    manifest = read_json(ctx.source_novel_dir / 'series_assets/manifest.json', {})
    for group, role in (('characters', 'character'), ('locations', 'location'), ('props', 'prop')):
        for row in manifest.get(group, []):
            name = str(row.get('name') or '')
            short = name.split('：', 1)[0]
            if not name or not (name in names or short in names or short in text):
                continue
            names.add(name)
            paths = [row[field] for field in ('primary_image', 'secondary_image') if row.get(field)]
            if paths:
                folder = ctx.source_novel_dir / Path(paths[0]).parent
                paths.extend(str(p.relative_to(ctx.source_novel_dir)) for p in sorted(folder.glob('*'))
                             if p.is_file() and p.suffix.lower() in {'.jpeg', '.jpg', '.png'})
            for path in dict.fromkeys(paths):
                references.append({'asset_id': row['asset_id'], 'role': role,
                    'name': short if role == 'location' else name, 'path': path})
    voices = read_json(ctx.source_novel_dir / 'series_assets/voices/voices.json', {})
    for name in voices:
        if name in names or name in text:
            references.append({'asset_id': f'voice_{name}', 'role': 'voice', 'name': name,
                               'path': f'series_assets/voices/{name}.wav'})
    return references


def _catalog(ctx, references):
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
        target = ctx.asset_snapshot_dir / relative
        if not _copy(source, target):
            missing.append({'role': ref['role'], 'name': ref.get('name'), 'path': str(original)})
            continue
        ident = str(ref.get('id') or ref.get('asset_id') or source.stem)
        if ident in ids:
            ident = f'{ident}_{source.stem}'
        if ident in ids:
            ident = f'{ident}_{len(assets)+1}'
        ids.add(ident)
        picture = ctx.work_dir / 'input/refs' / f'{ident}{source.suffix}'
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


def prepare_inputs(ctx: EpisodeContext):
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
    for name in READING_FILES:
        _copy(ctx.source_episode_dir / name, ctx.episode_dir / name)
        if ctx.input_mode == 'script' or name != 'identity_context.json':
            target = ctx.work_dir / 'input/reading' / name
            if _copy(ctx.episode_dir / name, target):
                reading.append(str(target.relative_to(ctx.work_dir)))
    script, plan = {}, {}
    if ctx.input_mode == 'script':
        for name in SCRIPT_FILES:
            _copy(ctx.source_episode_dir / name, ctx.episode_dir / name)
        script = read_json(ctx.episode_dir / 'chapter_script.json', {})
        plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    # Identity metadata points to the copied files, with the original reading
    # facts retained; this is path rebinding, not a new identity decision.
    identity = read_json(ctx.episode_dir / 'identity_context.json', {})
    if identity:
        from novel_manga.application.identity.store import chapter_inputs
        identity['inputs'] = chapter_inputs(ctx.episode_dir)
        atomic_write_json(ctx.episode_dir / 'identity_context.json', identity)
    if ctx.input_mode == 'script' and not plan:
        _catalog(ctx, _candidate_references(ctx, text, {}, script))
        plan = _pack_existing(ctx, script)
    too_long = [c.get('clip_id') for c in plan.get('clips', []) if c.get('kind', 'video') == 'video'
                and float(c.get('request_seconds') or 0) > ctx.clip_seconds_max]
    if too_long:
        raise ValueError(f'H3 requires repacking this existing clip plan to {ctx.clip_seconds_max:g}s clips: '
                         + ', '.join(map(str, too_long)) + '; the Agent input step does not change saved timing')
    selected = plan if ctx.input_mode == 'script' else read_json(ctx.source_episode_dir / 'clip_plan.json', {})
    refs = _candidate_references(ctx, text, selected, script)
    assets, missing, mapping = _catalog(ctx, refs)
    atomic_write_json(ctx.run_dir / 'catalog.json', assets)
    atomic_write_json(ctx.work_dir / 'input/catalog.json', {'assets': assets})
    for name in ('story_bible.json', 'bible_aliases.json', 'reading_cast.json', 'profile.json'):
        _copy(ctx.novel_dir / name, ctx.work_dir / 'input' / name)
    authoritative = []
    if ctx.input_mode == 'script':
        if not (ctx.work_dir / 'input/clip_plan.json').is_file():
            plan = _rebase_references(ctx, plan, mapping)
            atomic_write_json(ctx.episode_dir / 'clip_plan.json', plan)
            atomic_write_json(ctx.work_dir / 'input/clip_plan.json', plan)
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
        'authority': ('Original source; Agent owns screenplay, cutting and final requests.'
                      if ctx.input_mode == 'source' else
                      'Existing screenplay and clip plan are authoritative: preserve shots, dialogue, grouping, seconds and ordered references; write final requests only.'),
        'output': 'output/episode.json' if ctx.input_mode == 'source' else 'output/requests.json',
        'missing_assets': missing,
        'missing_inputs': [] if (ctx.novel_dir / 'story_bible.json').is_file() else ['story_bible.json'],
        'ready': not missing and (ctx.novel_dir / 'story_bible.json').is_file()}
    atomic_write_json(ctx.work_dir / 'input/manifest.json', manifest)
    atomic_write_json(ctx.run_dir / 'inputs.json', manifest)
    return manifest
