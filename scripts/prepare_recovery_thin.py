"""Prepare one owned episode for structural, technical or residual recovery."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
from novel_manga.util import atomic_write_json
from clip_readiness import read


def black_ranges(video: Path, minimum: float = 1.0) -> list[tuple[float, float]]:
    run = subprocess.run(['ffmpeg', '-nostdin', '-threads', '1', '-v', 'info', '-i', str(video),
                          '-vf', f'blackdetect=d={minimum}:pix_th=0.10', '-an', '-f', 'null', '-'],
                         capture_output=True, text=True, timeout=120, check=True)
    return [(float(a), float(b)) for a, b in re.findall(r'black_start:([0-9.]+) black_end:([0-9.]+)', run.stderr)]


def brighten_dark_scene(video: Path, ranges: list[tuple[float, float]], directory: Path, cid: str) -> bool:
    """Recover visible detail in underexposed footage; a blank frame stays rejected."""
    from PIL import Image, ImageStat
    from repair_history import archived_take
    with tempfile.TemporaryDirectory(prefix='exposure-', dir=video.parent) as folder:
        frame = Path(folder) / 'frame.png'
        for start, end in ranges:
            for fraction in (.25, .5, .75):
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start + (end - start) * fraction),
                                '-i', str(video), '-frames:v', '1', '-y', str(frame)], capture_output=True, check=True, timeout=30)
                with Image.open(frame) as image:
                    gray = image.convert('L')
                    histogram = gray.histogram()
                    total, cumulative, percentile99 = sum(histogram), 0, 0
                    for value, count in enumerate(histogram):
                        cumulative += count
                        if cumulative >= total * .99:
                            percentile99 = value
                            break
                    if ImageStat.Stat(gray).stddev[0] < 4 or percentile99 < 12:
                        return False  # no recoverable character/scene detail
        corrected = Path(folder) / 'corrected.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(video), '-map', '0:v:0', '-map', '0:a?',
                        '-vf', 'eq=gamma=1.6', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-threads', '2',
                        '-c:a', 'copy', '-movflags', '+faststart', str(corrected)], capture_output=True, check=True, timeout=120)
        if black_ranges(corrected, 1.0):
            return False
        archived_take(directory, cid, video)
        corrected.replace(video)
        return True


def technical_targets(directory: Path) -> dict[str, str]:
    media = read(directory / 'thin_media_report.json', {})
    plan = read(directory / 'clip_plan.json', {})
    by_id = {c['clip_id']: c for c in plan.get('clips', [])}
    notes = {}
    for cid in media.get('gate_failed_clips', []):
        lines = by_id.get(cid, {}).get('lines', [])
        notes[cid] = ('逐句完整读出给定台词；每句由指定说话者独立说完，交替说话，不抢话、不叠音、不吞句。'
                      '开头立即进入对白，结尾留给最后一句说完。保持每句原定的画内或画外方式，不增添额外台词。'
                      + '说话者顺序：' + '、'.join(t.get('speaker_name', '') or '原定画外音' for t in lines))
    checks = (media.get('assembly') or {}).get('media_qc', {}).get('checks', {})
    if checks.get('black_frames', {}).get('passed') is False:
        for row in media.get('clips', []):
            selected = row.get('selected') or {}
            video = Path(selected.get('video') or '')
            if video.is_file() and black_ranges(video, 0.2):
                notes[row['clip_id']] = notes.get(row['clip_id'], '') + (
                    '全段从首帧到尾帧持续呈现可辨认的角色和场景，保持场景照明；'
                    '直接切入动作并以场景画面结束，不淡入黑场、不淡出黑场、不插入黑色过渡。')
        if not any('不插入黑色过渡' in note for note in notes.values()):
            raise ValueError('black frames are in assembly/cards, not located in generated takes; needs assembly repair')
    # A black interval also registers as frozen. Fixing that same interval
    # addresses both checks; it is not a separate unsupported assembly fault.
    handled = {'black_frames', 'long_freeze'} if checks.get('black_frames', {}).get('passed') is False else set()
    from thin_profile import media_qc_ignores
    ignored=set(media_qc_ignores(directory.parent,directory))
    unsupported = [k for k, v in checks.items() if v.get('passed') is False and k not in handled|ignored]
    if unsupported:
        raise ValueError(f'unsupported media checks need assembly repair: {unsupported}')
    return notes


def prepare_technical(directory: Path) -> dict:
    from repair_history import begin_trial
    import build_clip_plan_thin as packer
    notes = technical_targets(directory)
    existing = read(directory / 'review_feedback.json', {})
    plan = read(directory / 'clip_plan.json', {})
    nonverbal = {c['clip_id']: [packer.nonverbal_sound(t) for t in c.get('lines', []) if packer.nonverbal_sound(t)]
                 for c in plan.get('clips', []) if c['clip_id'] in notes}
    nonverbal = {cid: sounds for cid, sounds in nonverbal.items() if sounds}
    updated_plan = plan
    if nonverbal:
        from repair_clips_thin import rebuild_clips
        updated_plan, changed = rebuild_clips(directory, directory.parent / 'story_bible.json',
                                             read(directory / 'chapter_script.json', {}), plan, set(nonverbal))
        if not set(nonverbal).issubset(changed):
            raise ValueError('could not rebuild nonverbal sound events from source stages')
        feedback = read(directory / 'episode_review.json', {}).get('feedback', {})
        for cid, sounds in nonverbal.items():
            notes[cid] = '\n'.join(x for x in (feedback.get(cid, ''), '保留声音事件：' + '、'.join(sounds)
                                   + '。直接发出自然声音，不把音效名称或导演说明读成对白；其他原定台词保持不变。') if x)
    fresh = {cid: '\n'.join(x for x in (existing.get(cid, ''), note) if x)
             for cid, note in notes.items() if note not in existing.get(cid, '')}
    fresh.update({cid: notes[cid] for cid in nonverbal})
    if not fresh:
        raise ValueError('no new technical correction; previous strategy has already been tried')
    updated = {**existing, **fresh}
    begin_trial(directory, set(fresh), 'technical_recovery', after_plan=updated_plan, after_notes=updated, changes=fresh)
    atomic_write_json(directory / 'review_feedback.json', updated)
    if nonverbal:
        atomic_write_json(directory / 'clip_plan.json', updated_plan)
    technical = read(directory / 'technical_repair.json', {})
    black = set(technical.get('black_clips', [])) | {cid for cid, note in notes.items() if '不插入黑色过渡' in note}
    speech = set(technical.get('speech_clips', [])) | {cid for cid in notes if cid not in black}
    atomic_write_json(directory / 'technical_repair.json', {'black_clips': sorted(black), 'speech_clips': sorted(speech)})
    return {'changed': list(fresh), 'instructions': fresh, 'nonverbal': nonverbal}


def grant_changed_source_retry(directory: Path, before: dict, before_notes: dict, result: dict, extra_takes: int) -> list[str]:
    """An explicit extra take requires a changed request, not merely a new seed."""
    from managed_repair_thin import generated_counts, generation_limit
    from repair_history import load, accepted_clip_material
    counts = generated_counts(load(directory))
    after = read(directory / 'clip_plan.json', {})
    notes = read(directory / 'review_feedback.json', {})
    old = {c['clip_id']:c for c in before.get('clips', [])}
    grants = read(directory / 'repair_budget_grants.json', {})
    granted = []
    for clip in after.get('clips', []):
        cid = clip['clip_id']
        if cid not in result.get('changed', []) or cid in result.get('accepted', []) or counts.get(cid, 0) < generation_limit(directory, cid):
            continue
        a = accepted_clip_material(old.get(cid, {})); b = accepted_clip_material(clip)
        a.pop('repair_take', None); b.pop('repair_take', None)
        if a == b and before_notes.get(cid, '') == notes.get(cid, ''):
            continue
        grants[cid] = {'limit': counts[cid] + extra_takes,
                       'reason': '用户授权的残留段处理：原文核验后请求已有实质修改，增加一次实际生成，历史次数保留'}
        granted.append(cid)
    if granted:
        atomic_write_json(directory / 'repair_budget_grants.json', grants)
    return granted


def prepare(directory: Path, kind: str, *, extra_takes: int = 0) -> dict:
    if kind == 'entities':
        from story_identity import resolve_chapter, typed_entities
        from repair_clips_thin import repair_episode
        import repair_history as history
        context = resolve_chapter(directory)
        types = typed_entities(directory.parent, context)
        before = read(directory / 'clip_plan.json', {})
        notes = read(directory / 'review_feedback.json', {})
        issues = {}
        for clip in before.get('clips', []):
            objects = {name: types[name] for name in clip.get('cast', []) if types.get(name, {}).get('kind') == 'object'}
            if objects:
                issues[clip['clip_id']] = ('原文确认以下是器物，不是人物：' + json.dumps(objects, ensure_ascii=False)
                    + '。只修本段：保留器物及它参与的原文事件，移除错误人形外貌与人物卡绑定，不让器物扮演人。'
                    '核对台词归属，保留真实人物的原有对白和事件；场景、服装等正确部分保持。')
        if not issues:
            return {'changed': [], 'skip_render': True}
        result = repair_episode(directory.parent, int(directory.name.rsplit('_',1)[1]), False,
                                use_history=False, reframe=True, source_issues=issues, return_proposal=True)
        proposal = result.get('proposal')
        if not proposal or set(issues) - set(result.get('changed', [])):
            raise ValueError('entity repair incomplete: ' + result.get('why', 'no complete proposal'))
        from clip_readiness import reference_issues
        for c in proposal['plan']['clips']:
            if c['clip_id'] in issues and any(r.startswith('entity:') for r in reference_issues(c, directory.parent)):
                raise ValueError('object remains bound as a character')
        trial = history.begin_trial(directory, set(result['changed']), 'entity_type_recovery',
            after_plan=proposal['plan'], after_notes=proposal['notes'], changes=proposal['changes'])
        rec = history.load(directory)
        rec['trials'][-1]['managed'] = True
        history.save(directory, rec)
        atomic_write_json(directory / 'chapter_script.json', proposal['script'])
        atomic_write_json(directory / 'clip_plan.json', proposal['plan'])
        atomic_write_json(directory / 'review_feedback.json', proposal['notes'])
        if extra_takes:
            result['extra_take_grants'] = grant_changed_source_retry(directory, before, notes, result, extra_takes)
        result.pop('proposal', None)
        return {**result, 'skip_render': False}
    if kind == 'references':
        from single_card_plan import repair_missing_expressions
        result=repair_missing_expressions(directory)
        return {**result,'skip_render':not result['changed']}
    if kind in {'managed','residual'}:
        from managed_repair_thin import prepare as prepare_managed
        return prepare_managed(directory)
    # Resume the interrupted source/visual repair only after restoring existing
    # script parts. Both preparations happen before any new video is requested.
    structural = None
    if kind in {'identity', 'residual'}:
        from clip_readiness import collapsed_source_addresses
        if collapsed_source_addresses(read(directory/'clip_plan.json',{}),read(directory/'chapter_script.json',{})):
            from repair_blocked_plan import repair_episode as repack_episode
            structural = repack_episode(directory,apply=True)
    if kind == 'source':
        from source_recheck_thin import prepare_source_recheck
        import repair_history as history
        initial_count = len(history.load(directory)['trials'])
        before = read(directory / 'clip_plan.json', {})
        before_notes = read(directory / 'review_feedback.json', {})
        result = prepare_source_recheck(directory)
        record = history.load(directory)
        for trial in record['trials'][initial_count:]:
            trial['managed'] = True
        if len(record['trials']) > initial_count:
            history.save(directory, record)
        if extra_takes:
            result['extra_take_grants'] = grant_changed_source_retry(directory, before, before_notes, result, extra_takes)
        result['skip_render'] = not result.get('needs_render', result.get('changed'))
        return result
    if kind == 'plan':
        from repair_blocked_plan import repair_episode
        return repair_episode(directory, apply=True)
    if kind == 'technical':
        return prepare_technical(directory)
    if kind == 'speech':
        from repair_history import begin_trial
        media = read(directory / 'thin_media_report.json', {})
        ids = set(media.get('gate_failed_clips') or [])
        old = read(directory / 'review_feedback.json', {})
        instruction = '本段音轨仅包含指定角色的原定中文台词和现场环境声。每句台词完整自然地说完，句间依次衔接，不添加解说、额外旁白、英文说明或其他对白。'
        updated = {**old, **{cid: instruction for cid in ids}}
        if not ids or updated == old:
            raise ValueError('no new speech strategy to apply')
        begin_trial(directory, ids, 'speech_recovery', after_notes=updated, changes={cid: instruction for cid in ids})
        atomic_write_json(directory / 'review_feedback.json', updated)
        technical = read(directory / 'technical_repair.json', {})
        technical['speech_clips'] = sorted(set(technical.get('speech_clips', [])) | ids)
        atomic_write_json(directory / 'technical_repair.json', technical)
        return {'changed': sorted(ids)}
    from repair_clips_thin import repair_episode
    source_issues = read(directory / 'source_binding_issues.json') if kind == 'binding' else None
    result = repair_episode(directory.parent, int(directory.name.rsplit('_', 1)[1]), True, reframe=True, identity=kind == 'identity', source_issues=source_issues)
    if kind in {'identity','residual'} and result.get('why') == 'nothing to repair':
        return {**result, 'already_correct': True, **({'structural_repair':structural} if structural else {})}
    if not result.get('changed'):
        raise ValueError(f'no effective residual change: {result}')
    return {**result, **({'structural_repair':structural} if structural else {})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode-dir', type=Path, required=True)
    parser.add_argument('--kind', choices=['plan', 'references', 'technical', 'residual', 'identity', 'binding', 'speech', 'source','managed','entities'], required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--extra-takes', type=int, choices=[0, 1], default=0,
                        help='source recovery: one explicitly authorized extra take only after a substantive request change')
    args = parser.parse_args()
    directory = args.episode_dir.resolve()
    path = directory / 'repair_history' / f'preparation-{args.job_id}.json'
    old = read(path, {})
    if old.get('success'):
        print(json.dumps(old, ensure_ascii=False), flush=True)
        return 0
    try:
        result = {'success': True, 'kind': args.kind, **prepare(directory, args.kind, extra_takes=args.extra_takes)}
    except ValueError as error:
        result = {'success': False, 'kind': args.kind, 'error': str(error)}
    atomic_write_json(path, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['success'] else 4


if __name__ == '__main__':
    raise SystemExit(main())
