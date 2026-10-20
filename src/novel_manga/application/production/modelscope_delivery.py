"""Deliver an accepted episode's movie, cover and index through the existing SDK."""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from novel_manga.util import atomic_write_json, read_json, retry
from novel_manga.episodes import chapter_of

INDEX = 'all-episodes.json'


def source(path):
    path = Path(path)
    stat = path.stat()
    if not path.is_file() or not stat.st_size:
        raise ValueError(f'missing delivery media: {path}')
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def accepted_bundle(run_dir):
    manifest = read_json(Path(run_dir)/'final-candidate/manifest.json', {})
    if manifest.get('passed') is not True or (manifest.get('quality_review') or {}).get('passed') is not True:
        raise ValueError('finish must accept the current movie before delivery')
    if not manifest.get('movie') or not manifest.get('cover'):
        raise ValueError('finish bundle must contain both movie and corresponding cover')
    return source(manifest['movie']), source(manifest['cover'])


def upload_episode(novel_dir, run_dir, episode, repository, *, api=None):
    """Keep old URLs; resume delivery independently of Agent and generation budgets."""
    novel_dir, run_dir = Path(novel_dir), Path(run_dir)
    root = novel_dir/'delivery/modelscope'
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = run_dir/'delivery/modelscope.json'
    with (root/'upload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        movie, cover = accepted_bundle(run_dir)
        if api is None:
            from modelscope_hub import HubApi
            api = HubApi()
        files = {row.path: int(row.size) for row in api.list_repo_files(repository, 'dataset')}
        index = (read_json(Path(api.download_file(repository, 'dataset', INDEX,
                    local_dir=root/'remote-index', force=True)))
                 if INDEX in files else {'episodes': []})
        downloaded_index = json.loads(json.dumps(index))
        entries = {row['episode']: row for row in index['episodes']}
        # A downloaded index can lag a completed upload. Preserve this book's
        # verified current deliveries, rather than reverting their links/bytes.
        for path in root.glob('*-uploaded.json'):
            saved = read_json(path, {})
            if saved.get('repository') != repository or not saved.get('verified_remote'):
                continue
            prior = saved.get('source') or {}
            prior_cover = (saved.get('cover') or {}).get('source') or {}
            if (not Path(prior.get('path', '')).is_file()
                    or not Path(prior_cover.get('path', '')).is_file()
                    or source(prior['path']) != prior or source(prior_cover['path']) != prior_cover):
                continue
            entries.setdefault(saved['episode'], {'episode': saved['episode']}).update(
                path=saved['path'], bytes=saved['bytes'], cover=saved['cover']['path'])
        entry = entries.setdefault(episode, {'episode': episode})
        entry.setdefault('path', f'episodes/{novel_dir.name}-{episode:03d}.mp4')
        entry.setdefault('cover', f'covers/{novel_dir.name}-{episode:03d}.jpeg')
        entry['bytes'] = movie['size']
        index['episodes'] = sorted(entries.values(), key=lambda row: row['episode'])
        saved = read_json(receipt_path, {})
        same_movie = (saved.get('repository') == repository and saved.get('verified_remote') is True
                      and saved.get('source') == movie and saved.get('path') == entry['path'])
        stage = root/'stage'
        patterns = []
        for relative, original, verified in (
            (entry['path'], movie, same_movie),
            (entry['cover'], cover, same_movie and saved.get('cover', {}).get('source') == cover),
        ):
            if verified and files.get(relative) == original['size']:
                continue
            target = stage/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            try:
                target.hardlink_to(original['path'])
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                shutil.copy2(original['path'], target)
            patterns.append(relative)
        if not patterns and downloaded_index == index:
            return saved
        atomic_write_json(stage/INDEX, index)
        # Upload only this bundle and the index, never manuscript or work files.
        api.upload_folder(repository, 'dataset', stage, allow_patterns=[*patterns, INDEX],
                          commit_message=f'Update episode {episode} and cover', max_workers=2,
                          disable_tqdm=True, sync_remote_repo=False)

        def verify():
            current = {row.path: int(row.size) for row in api.list_repo_files(repository, 'dataset')}
            if current.get(entry['path']) != movie['size'] or current.get(entry['cover']) != cover['size']:
                raise RuntimeError('remote movie or cover is missing or incomplete')
            remote = read_json(Path(api.download_file(repository, 'dataset', INDEX,
                                     local_dir=root/'verified-index', force=True)), {})
            remote_entries = {row['episode']: row for row in remote.get('episodes', [])}
            for number, expected in entries.items():
                if remote_entries.get(number) != expected:
                    raise RuntimeError(f'remote index has not yet recorded episode {number}')

        # Index propagation is read-only retry; it never resubmits media or an LLM.
        retry(verify)
        if accepted_bundle(run_dir) != (movie, cover):
            raise RuntimeError('finish bundle changed during delivery; do not certify the old version')
        receipt = {'repository': repository, 'episode': episode, 'path': entry['path'],
                   'bytes': movie['size'], 'source': movie, 'verified_remote': True,
                   'cover': {'path': entry['cover'], 'bytes': cover['size'], 'source': cover,
                             'verified_remote': True},
                   'uploaded_at': saved.get('uploaded_at') if same_movie else time.strftime('%F %T'),
                   'cover_uploaded_at': time.strftime('%F %T')}
        atomic_write_json(root/f'{episode}-uploaded.json', receipt)
        atomic_write_json(receipt_path, receipt)
        return receipt


def deliver_if_configured(ctx, result):
    """Called by the normal episode host; a missing destination means local delivery."""
    settings = read_json(ctx.source_novel_dir/'modelscope_delivery.json', {})
    if result.get('passed') is not True or not settings.get('repository'):
        return result
    command = [settings.get('python') or sys.executable, '-m', __name__,
               '--novel-dir', str(ctx.source_novel_dir), '--run-dir', str(ctx.run_dir),
               '--episode', str(chapter_of(ctx.source_episode_dir.name)),
               '--repository', settings['repository']]
    # Only this public bulk-upload child gets direct routing. Never change the
    # host's model/private-service network or copy credentials into configuration.
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in {'http_proxy', 'https_proxy', 'all_proxy'}}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3])
    try:
        code = subprocess.run(command, env=env, check=False).returncode
    except OSError:
        code = 2
    delivery = read_json(ctx.run_dir/'delivery/modelscope.json', {}) if code == 0 else {}
    if not delivery.get('verified_remote') or delivery.get('repository') != settings['repository']:
        code = 2
        delivery = {'status': 'pending', 'reason': 'ModelScope delivery failed; resume delivery without rerunning production'}
    result = {**result, 'delivery': delivery}
    result['exit_code'] = 2 if code else 0
    atomic_write_json(ctx.run_dir/'agent-run.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--novel-dir', required=True, type=Path)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--episode', required=True, type=int)
    parser.add_argument('--repository', required=True)
    args = parser.parse_args(argv)
    receipt = retry(lambda: upload_episode(args.novel_dir, args.run_dir, args.episode, args.repository))
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
