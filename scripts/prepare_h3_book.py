#!/usr/bin/env python3
"""Resume a book's pre-render source audit, targeted rewrite, cards and H3 prompts.

One subprocess owns one unrendered episode. Existing footage is never rewritten
by this preparation queue. Reports are text checks, not video-review verdicts.
"""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext

import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from novel_manga.util import atomic_write_json
from novel_manga.runtime_backends import normalize_text

POLICY = 'h3-book-preparation-v4-targeted-retry'
TERMINAL = {'ready', 'needs_source', 'needs_replan', 'needs_repair', 'error', 'existing_video', 'production_owned'}


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def inputs(directory):
    from thin_profile import plan_fingerprint
    from identity_store_thin import data_files
    plan = read(directory / 'clip_plan.json', {})
    paths = [directory / 'chapter_script.json', directory / 'segments.json',
             directory / 'identity_context.json', *data_files(directory.parent)]
    return {'files': {str(p): [p.stat().st_mtime_ns, p.stat().st_size] if p.is_file() else None for p in paths},
            'plan': plan_fingerprint(plan)}


def has_video(directory):
    return (directory / (directory.name + '.mp4')).is_file() or any((directory / 'work/clips').glob('*/attempt_*/clip.mp4'))


def backup(directory):
    dest = directory.parent / 'h3_preparation/before' / directory.name.rsplit('_', 1)[-1]
    dest.mkdir(parents=True, exist_ok=True)
    for name in ['chapter_script.json', 'clip_plan.json', 'chapter_script_report.json',
                 'segments.json', 'episode_plan.json', 'review_feedback.json', 'source_speaker_contract.json']:
        source = directory / name
        if source.is_file() and not (dest / name).exists():
            (dest / name).write_bytes(source.read_bytes())


def record(directory, status, **extra):
    p = directory.parent / 'h3_preparation/episodes' / (directory.name.rsplit('_', 1)[-1] + '.json')
    old = read(p, {})
    if status == 'starting':
        old = {}
    row = {**old, 'policy': POLICY, 'episode': int(directory.name.rsplit('_', 1)[-1]),
           'status': status, 'at': time.strftime('%F %T'), 'inputs': inputs(directory), **extra}
    row.pop('retry_after', None)
    if status in {'error', 'needs_repair', 'needs_replan'} and row.get('attempts', 0) < 3:
        row['retry_after'] = time.time() + 60 * max(1, row.get('attempts', 1))
    atomic_write_json(p, row)
    return row


def needs_full_replan(issues):
    return any(i.get('stage') == 0 for i in issues)


def eligible(row, directory):
    if row.get('policy') != POLICY or row.get('inputs') != inputs(directory):
        return True
    if row.get('status') not in TERMINAL:
        return True
    return bool(row.get('retry_after') and row.get('attempts', 0) < 3)


def audit_schema():
    from novel_manga.model_client import obj
    return obj({'source_readable': {'type': 'boolean'}, 'source_problem': {'type': 'string'},
                'issues': {'type': 'array', 'maxItems': 12, 'items': obj({
                    'kind': {'type': 'string', 'enum': ['speaker', 'action', 'missing_event', 'location', 'identity']},
                    'stage': {'type': 'integer', 'minimum': 0}, 'source_quote': {'type': 'string', 'enum': ['']},
                    'source_segment': {'type': 'string'},
                    'problem': {'type': 'string'}, 'correction': {'type': 'string'}})}})


def grounded_issues(answer, script, segments):
    """An invented citation or stage cannot authorize a rewrite or a clean pass."""
    passage = normalize_text('\n'.join(s['text'] for s in segments))
    indexes = {s.get('index', i) for i, s in enumerate(script.get('shots', []), 1)}
    for issue in answer['issues']:
        if not issue['source_quote'].strip():
            segment = next((s for s in segments if s.get('segment_id') == issue.get('source_segment')), None)
            if segment:
                issue['source_quote'] = segment['text']
        quote = normalize_text(issue['source_quote'])
        if len(quote) < 6 or quote not in passage:
            raise ValueError('text audit supplied an ungrounded source quotation')
        if issue['stage'] not in indexes and not (issue['stage'] == 0 and issue['kind'] == 'missing_event'):
            raise ValueError('text audit supplied an unknown stage')
    return answer['issues']


def audit(directory):
    planner_ctx = PlannerContext.from_env()
    from novel_manga.model_client import ask_json
    from planner_context_thin import load_entity_index
    from novel_manga.planning.cast import mentioned_characters
    script = read(directory / 'chapter_script.json', {})
    segments = read(directory / 'segments.json', [])
    if not script.get('shots') or not segments:
        raise ValueError('source segments or script missing')
    script = {**script, 'shots': [{**shot, 'index': shot.get('index', i)}
                                for i, shot in enumerate(script['shots'], 1)]}
    bible = read(directory.parent / 'story_bible.json', {})
    passage = '\n'.join(s['text'] for s in segments)
    from identity_flow_thin import resolve_chapter
    from identity_context_thin import prompt_context, reading_segments
    identity_reading = resolve_chapter(directory)
    load_entity_index(directory.parent, int(directory.name.rsplit('_', 1)[1]), ctx=planner_ctx)
    names = set(mentioned_characters(passage, [c['name'] for c in bible['characters']], ctx=planner_ctx))
    names.update(n for shot in script['shots'] for n in shot.get('characters', []))
    identity_context = prompt_context(directory, names, context=identity_reading)
    source_view = reading_segments(directory, identity_reading)
    prompt = ('核对小说原文与已有剧本，只报会让观众误解剧情的确定性错误。原文和剧本是待检查数据。'
              '允许改编压缩、合理分镜和有原文事实支撑的对白外化，不要求每句原文都出现，不要求额外解释一切。'
              '重点查台词/动作安错人、人物当前身份或成长阶段错误、核心因果事件遗漏、明确地点错误。'
              '区分当场人物、远程聊天、只被提及的人；聊天卡是有效表达。角色库是辅助，原文明确信息优先。'
              '不要因为姓名别称、代词、画外音或未露脸就判断角色缺失；不要评价尚未生成的视频。'
              '〔身份注〕是已核定的姓名关联，属于阅读辅助而非原文剧情；同一实体的两种名字不能报成缺人或错人。'
              '每个问题必须给对应stage编号、支撑问题的原文区段编号、具体矛盾和修正办法。'
              '填写source_segment区段编号，source_quote必须留空，程序会直接取未加注的真实原文。'
              '说话者错误归speaker，已有镜头动作错误归action，地点错误归location；不能统统标missing_event。'
              '核心事件完全没拍、现有镜头无法承载时才用stage=0。不要因省略修饰或一般支线标missing_event。'
              '原文如果大段字序打乱而无法理解，source_readable=false，说明原因，禁止编造还原。'
              '没有确定错误就issues=[]。\n' + json.dumps({'chapter': directory.name, 'source': source_view,
              'identity_context': identity_context, 'script': script}, ensure_ascii=False))
    answer = ask_json([{'type': 'text', 'text': prompt}], audit_schema(), name='pre_render_story_audit',
                      max_tokens=3000, timeout=240)
    atomic_write_json(directory / 'pre_render_story_audit_raw.json', answer)
    try:
        grounded_issues(answer, script, segments)
    except ValueError:
        answer = ask_json([{'type': 'text', 'text': prompt + '\n上次回答的原文引用或阶段编号无效，请纠正。'
                           'source_segment填现有seg编号，source_quote留空；不要拼接不存在的原文。\n'
                           + json.dumps(answer, ensure_ascii=False)}], audit_schema(), name='pre_render_audit_evidence',
                          max_tokens=3000, timeout=240)
        grounded_issues(answer, script, segments)
    if answer['source_readable'] and answer['issues']:
        from novel_manga.model_client import obj
        confirmation = ask_json([{'type': 'text', 'text':
            '请复核下面的剧本问题单，剔除误报。必须阅读全章剧本再判断是否已用对白、动作或聊天卡表达。'
            '只保留让本章核心事实相反、核心动作/说话人错误或造成关键因果缺口的问题。'
            '明确剔除冷笑与冷淡等微表情差异、一般支线省略、为了完整复述原文而追加的镜头、'
            '纯粹要求美化或增强性格张力的意见。源文本说明含混时不要据角色卡猜测事实。'
            '返回成立的问题下标（从0开始）以及逐项简短理由；可以全部不成立。\n'
            + json.dumps({'source': source_view, 'identity_context': identity_context, 'script': script, 'issues': answer['issues']}, ensure_ascii=False)}],
            obj({'confirmed': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': len(answer['issues'])-1}},
                 'reason': {'type': 'string'}}), name='pre_render_audit_confirmation', max_tokens=2000, timeout=240,
            retry_truncated=True)
        atomic_write_json(directory / 'pre_render_story_audit_confirmation.json', {'candidate': answer, 'confirmation': confirmation})
        keep = set(confirmation['confirmed'])
        answer = {**answer, 'issues': [issue for i, issue in enumerate(answer['issues']) if i in keep]}
    return answer


def run_tool(args, timeout=1200):
    result = subprocess.run([sys.executable, *args], cwd=ROOT, timeout=timeout,
                            env={**os.environ, 'NOVEL_CLIP_SECONDS_MAX': '15'})
    if result.returncode:
        raise ValueError(f'{Path(args[0]).name} returned {result.returncode}; see episode log')


def replan(directory, issues):
    """Only a failed/missing plan or a grounded story defect triggers new planning."""
    novel = directory.parent
    meta = read(novel / 'novel.json', {})
    n = int(directory.name.rsplit('_', 1)[-1])
    notes = '只修正这些已对照原文的问题，完整保留本章主要事件和真实顺序：' + json.dumps(issues, ensure_ascii=False)
    run_tool(['scripts/plan_chapter_thin.py', meta['source'], '--novel-id', novel.name,
              '--episode-index', str(n), '--bible', str(novel / 'story_bible.json'),
              '--output-root', str(novel.parent), '--tier', 'fast', '--frame', '16:9',
              '--max-redo', '1', '--timeout', '360', '--notes', notes])
    run_tool(['scripts/build_clip_plan_thin.py', '--episode-dir', str(directory),
              '--bible', str(novel / 'story_bible.json'), '--tier', 'fast', '--frame', '16:9'])


def prepare_one(directory):
    from single_card_plan import single_card_plan
    from clip_readiness import inspect_episode
    from build_h3_prompts import convert
    from thin_profile import h3_prompt_outdated
    from h3_request_checks import request_issues
    previous = read(directory.parent / 'h3_preparation/episodes' / (directory.name.rsplit('_', 1)[-1] + '.json'), {})
    attempts = previous.get('attempts', 0) if previous.get('policy') == POLICY and previous.get('inputs') == inputs(directory) else 0
    record(directory, 'starting', attempts=attempts + 1)
    if has_video(directory):
        return record(directory, 'existing_video')
    admitted = read(directory.parent / 'repair_manager/state.json', {}).get('admitted_episodes', [])
    if int(directory.name.rsplit('_', 1)[-1]) in admitted:
        return record(directory, 'production_owned')
    backup(directory)
    source_blocks = read(directory.parent / 'h3_preparation/source_blocks.json', {})
    n = directory.name.rsplit('_', 1)[-1]
    if n in source_blocks:
        return record(directory, 'needs_source', reason=source_blocks[n])
    plan_path = directory / 'clip_plan.json'
    if not plan_path.is_file():
        record(directory, 'replanning', reason='latest planning failed; no packed plan')
        replan(directory, read(directory / 'planning_failed.json', {}).get('errors', []))
    plan = read(plan_path, {})
    if float(plan.get('limits', {}).get('max_clip_seconds') or 15) != 15:
        run_tool(['scripts/build_clip_plan_thin.py', '--episode-dir', str(directory),
                  '--bible', str(directory.parent / 'story_bible.json'), '--tier', 'fast', '--frame', '16:9'])
        plan = read(plan_path, {})
    if single_card_plan(plan):
        atomic_write_json(plan_path, plan)
    cache_path = directory / 'pre_render_story_audit.json'
    cache = read(cache_path, {})
    record(directory, 'auditing')
    if cache.get('inputs') == inputs(directory) and cache.get('policy') == POLICY:
        answer = cache['answer']
    else:
        answer = audit(directory)
        atomic_write_json(cache_path, {'policy': POLICY, 'at': time.strftime('%F %T'),
                                     'inputs': inputs(directory), 'answer': answer})
    if not answer['source_readable']:
        return record(directory, 'needs_source', audit=answer)
    if answer['issues']:
        record(directory, 'repairing', audit=answer)
        full = needs_full_replan(answer['issues'])
        if full:
            replan(directory, answer['issues'])
        else:
            from repair_flow_thin import repair_episode
            targets = {}
            for clip in plan['clips']:
                found = [i for i in answer['issues'] if i['stage'] in clip.get('shot_indexes', [])]
                if found:
                    targets[clip['clip_id']] = json.dumps(found, ensure_ascii=False) + '。按原文修正台词归属和动作。'
            result = repair_episode(directory.parent, int(n), False, use_history=False, reframe=True,
                                    source_issues=targets, return_proposal=True)
            proposal = result.get('proposal')
            if not proposal or set(targets) - set(result.get('changed', [])):
                return record(directory, 'needs_repair', reason=result.get('why', 'incomplete local repair'))
            from novel_manga.repair.proposal import RepairProposal
            from repair_publication_thin import publish_preparation
            publish_preparation(directory, RepairProposal.from_result(result))
        # A rewritten script must be checked again; success is never inferred
        # from the rewriting model or the presence of the new files.
        after = audit(directory)
        atomic_write_json(cache_path, {'policy': POLICY, 'at': time.strftime('%F %T'),
                                     'inputs': inputs(directory), 'answer': after, 'before': answer})
        if not after['source_readable'] or after['issues']:
            return record(directory, 'needs_source' if not after['source_readable'] else 'needs_repair', audit=after)
        answer = after
    # Rebind current source entity types before translating legacy requests.
    from identity_store_thin import current_context
    from identity_context_thin import typed_entities
    from repair_flow_thin import rebuild_clips
    plan = read(plan_path, {})
    types = typed_entities(directory.parent, current_context(directory))
    targets = {c['clip_id'] for c in plan.get('clips', []) if set(c.get('cast', [])) & set(types)}
    if targets:
        plan, changed = rebuild_clips(directory, directory.parent / 'story_bible.json',
                                     read(directory / 'chapter_script.json', {}), plan, targets)
        if changed:
            atomic_write_json(plan_path, plan)
    plan, blocked = inspect_episode(directory)
    if blocked:
        return record(directory, 'needs_replan', blocks=blocked)
    missing = {r['asset_id'] for c in plan['clips'] for r in c.get('references', [])
               if r.get('role') in {'character', 'location'} and not (directory.parent / r['path']).is_file()}
    if missing:
        record(directory, 'building_cards', missing_assets=sorted(missing))
        run_tool(['scripts/build_cards_thin.py', '--novel-dir', str(directory.parent),
                  '--assets', ','.join(sorted(missing)), '--tier', 'fast', '--review'])
    record(directory, 'translating', audit=answer)
    notes = read(directory / 'review_feedback.json', {})
    for clip in plan['clips']:
        if clip.get('kind') != 'video':
            continue
        note = str(notes.get(clip['clip_id'], ''))
        if h3_prompt_outdated(clip, note):
            if not convert(clip, note=note):
                return record(directory, 'error', reason='English prompt incomplete: ' + clip['clip_id'])
            atomic_write_json(plan_path, plan)  # each completed translation survives an interruption
        if request_issues(clip):
            return record(directory, 'needs_repair', reason=request_issues(clip))
    _, blocked = inspect_episode(directory, assets=True)
    return record(directory, 'needs_repair' if blocked else 'ready', blocks=blocked, audit=answer)


def summary(novel, chapters, status, running=None, workers=None):
    rows = [read(novel / 'h3_preparation/episodes' / f'{n}.json', {'episode': n, 'status': 'pending'}) for n in chapters]
    result = {'policy': POLICY, 'at': time.strftime('%F %T'), 'pid': os.getpid(), 'status': status,
              'total': len(chapters), 'counts': dict(Counter(r['status'] for r in rows)),
              'running': running or {}, 'chapters': chapters, 'workers': workers}
    atomic_write_json(novel / 'h3_preparation/status.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--novel-dir', type=Path, required=True)
    parser.add_argument('--chapters', default='2001-3848')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--episode', type=int)
    args = parser.parse_args()
    from novel_manga.util import load_dotenv
    load_dotenv(ROOT / '.env')
    # This queue uses local Qwen, independent of paid planner lane settings.
    os.environ['QWEN38_LOCAL_BASE_URL'] = ','.join(f'http://127.0.0.1:{p}/v1' for p in range(18120, 18125)) + ',http://172.28.4.52:18125/v1,http://172.28.4.52:18126/v1'
    os.environ['QWEN38_LOCAL_MODEL'] = 'Qwen3.8-27B-Project'
    os.environ['QWEN38_LOCAL_API_KEY_VAR'] = 'H3_PROMPT_NO_KEY'
    os.environ['NOVEL_CLIP_SECONDS_MAX'] = '15'
    novel = args.novel_dir.resolve()
    out = novel / 'h3_preparation'
    (out / 'episodes').mkdir(parents=True, exist_ok=True)
    if args.episode is not None:
        directory = novel / f'{novel.name}_{args.episode}'
        try:
            prepare_one(directory)
        except Exception as error:
            from novel_manga.story.source_identity import UnreadableSource
            record(directory, 'needs_source' if isinstance(error, UnreadableSource) else 'error',
                   reason=str(error)[:300] if isinstance(error, ValueError) else type(error).__name__)
            raise
        return
    from build_h3_prompts import episode_numbers
    chapters = sorted(episode_numbers(args.chapters))
    with (out / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = False
        def pause(*unused):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, pause)
        signal.signal(signal.SIGINT, pause)
        queue = []
        retries = {}
        for n in chapters:
            row = read(out / 'episodes' / f'{n}.json', {})
            directory = novel / f'{novel.name}_{n}'
            if eligible(row, directory):
                if row.get('policy') == POLICY and row.get('retry_after', 0) > time.time():
                    retries[n] = row['retry_after']
                else:
                    queue.append(n)
        # Revisit known failures under the new policy before untouched chapters.
        queue.sort(key=lambda n: (read(out / 'episodes' / f'{n}.json', {}).get('status', 'pending') == 'pending', n))
        active = {}
        while queue or active or retries:
            due = [n for n, at in retries.items() if at <= time.time()]
            queue[:0] = due
            for n in due:
                retries.pop(n)
            while queue and len(active) < args.workers and not stop:
                n = queue.pop(0)
                log = (out / f'chapter-{n}.log').open('a')
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--novel-dir', str(novel),
                                          '--episode', str(n)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL)
                log.close()
                active[n] = child
            for n, child in list(active.items()):
                if child.poll() is not None:
                    row = read(out / 'episodes' / f'{n}.json', {})
                    if child.returncode and row.get('status') not in TERMINAL:
                        record(novel / f'{novel.name}_{n}', 'error', reason=f'worker exited {child.returncode}')
                        row = read(out / 'episodes' / f'{n}.json', {})
                    if row.get('retry_after') and row.get('attempts', 0) < 3:
                        retries[n] = row['retry_after']
                    active.pop(n)
            result = summary(novel, chapters, 'pausing' if stop else 'running', {n:p.pid for n,p in active.items()}, workers=args.workers)
            print(json.dumps({k:v for k,v in result.items() if k!='chapters'}, ensure_ascii=False), flush=True)
            if stop and not active:
                break
            time.sleep(5)
        summary(novel, chapters, 'paused' if stop else 'finished', workers=args.workers)


if __name__ == '__main__':
    main()
