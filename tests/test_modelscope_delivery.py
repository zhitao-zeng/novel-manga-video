"""Accepted delivery includes the matching cover, without repeating production."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.production import modelscope_delivery as delivery
from novel_manga.util import atomic_write_json, read_json


class Api:
    def __init__(self, index=None):
        self.files = {delivery.INDEX: json.dumps(index or {'episodes': []}).encode()}
        self.uploads = []
        self.drop = None
        self.stale_index = None

    def list_repo_files(self, *args):
        return [SimpleNamespace(path=p, size=len(body)) for p, body in self.files.items()]

    def download_file(self, repo, kind, path, *, local_dir, force):
        target = local_dir/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.stale_index or self.files[path])
        return target

    def upload_folder(self, repo, kind, stage, **options):
        assert options['sync_remote_repo'] is False
        self.uploads.append(options['allow_patterns'])
        for path in options['allow_patterns']:
            if path != self.drop:
                self.files[path] = (stage/path).read_bytes()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'retry', lambda operation: operation())
    novel = tmp_path/'book'; novel.mkdir()
    run = tmp_path/'run'
    movie = run/'final-candidate/movie.mp4'; movie.parent.mkdir(parents=True)
    movie.write_bytes(b'accepted current movie')
    cover = movie.with_name('cover.jpeg'); cover.write_bytes(b'corresponding cover')
    manifest = {'passed': True, 'quality_review': {'passed': True},
                'episode_status': 'done',
                'movie': str(movie), 'cover': str(cover)}
    atomic_write_json(movie.parent/'manifest.json', manifest)
    return novel, run, movie, cover


def upload(bundle, api, number=7):
    return delivery.upload_episode(bundle[0], bundle[1], number, 'owner/book', api=api)


def test_bundle_upload_preserves_urls_other_rows_and_excludes_workfiles(bundle):
    api = Api({'episodes': [{'episode': 7, 'path': 'drafts/original.mp4', 'title': '七'},
                            {'episode': 8, 'path': 'episodes/eight.mp4', 'bytes': 44}]})
    receipt = upload(bundle, api)
    assert api.uploads == [['drafts/original.mp4', 'covers/book-007.jpeg', delivery.INDEX]]
    index = json.loads(api.files[delivery.INDEX])
    assert index['episodes'][0]['title'] == '七'
    assert index['episodes'][1] == {'episode': 8, 'path': 'episodes/eight.mp4', 'bytes': 44}
    assert receipt['source'] == delivery.source(bundle[2])
    assert receipt['cover']['source'] == delivery.source(bundle[3])
    assert receipt['verified_remote'] and receipt['cover']['verified_remote']
    assert read_json(bundle[1]/'delivery/modelscope.json') == receipt


def test_repeat_delivery_skips_media_and_index_upload(bundle):
    api = Api()
    first = upload(bundle, api)
    second = upload(bundle, api)
    assert first == second and len(api.uploads) == 1


def test_cover_only_change_does_not_resend_video_or_replace_movie_timestamp(bundle):
    api = Api(); first = upload(bundle, api)
    bundle[3].write_bytes(b'new corresponding cover')
    second = upload(bundle, api)
    assert api.uploads[-1] == ['covers/book-007.jpeg', delivery.INDEX]
    assert second['source'] == first['source']
    assert second['uploaded_at'] == first['uploaded_at']


@pytest.mark.parametrize('missing', ['movie', 'cover', 'index'])
def test_incomplete_remote_bundle_cannot_be_certified(bundle, missing):
    api = Api()
    api.drop = {'movie': 'episodes/book-007.mp4', 'cover': 'covers/book-007.jpeg',
                'index': delivery.INDEX}[missing]
    with pytest.raises(RuntimeError):
        upload(bundle, api)
    assert not (bundle[1]/'delivery/modelscope.json').exists()


@pytest.mark.parametrize('bad', ['missing_cover', 'failed', 'quality_failed', 'not_done'])
def test_unaccepted_or_incomplete_local_bundle_cannot_upload(bundle, bad):
    manifest = bundle[2].parent/'manifest.json'; data = read_json(manifest)
    if bad == 'missing_cover': data['cover'] = None
    elif bad == 'failed': data['passed'] = False
    elif bad=='quality_failed': data['quality_review']['passed'] = False
    else: data['episode_status']='done_with_warnings'
    atomic_write_json(manifest, data)
    api = Api()
    with pytest.raises(ValueError): upload(bundle, api)
    assert api.uploads == []


def test_stale_download_cannot_erase_another_completed_delivery(bundle, tmp_path):
    api = Api()
    old_index = api.files[delivery.INDEX]
    upload(bundle, api)
    other = tmp_path/'other-run'; other.mkdir()
    import shutil
    shutil.copytree(bundle[1]/'final-candidate', other/'final-candidate')
    second = (bundle[0], other, bundle[2], bundle[3])
    # The next read lags the verified upload; only the initial index read is stale.
    original = api.download_file
    def download(repo, kind, path, **kwargs):
        api.stale_index = old_index if kwargs['local_dir'].name == 'remote-index' else None
        return original(repo, kind, path, **kwargs)
    api.download_file = download
    upload(second, api, number=8)
    assert [row['episode'] for row in json.loads(api.files[delivery.INDEX])['episodes']] == [7, 8]


def test_upload_failure_retains_formal_pass_and_retries_delivery_only(bundle, monkeypatch):
    novel, run, movie, cover = bundle
    atomic_write_json(novel/'modelscope_delivery.json', {'repository': 'owner/book', 'python': '/delivery/python'})
    ctx = SimpleNamespace(source_novel_dir=novel, source_episode_dir=novel/'book_7', run_dir=run)
    calls = []
    def submit(command, **kwargs):
        calls.append((command, kwargs['env']))
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(delivery.subprocess, 'run', submit)
    monkeypatch.setenv('HTTPS_PROXY', 'http://test-proxy:9000')
    result = delivery.deliver_if_configured(ctx, {'status': 'finished', 'passed': True, 'attempts': [1], 'exit_code': 0})
    assert result['passed'] and result['status'] == 'finished' and result['attempts'] == [1]
    assert result['delivery']['status'] == 'pending' and result['exit_code'] == 2
    assert calls[0][0][0] == '/delivery/python'
    assert 'HTTPS_PROXY' not in calls[0][1]
    assert read_json(run/'agent-run.json')['passed'] is True


def test_no_upload_without_destination_or_before_acceptance(bundle, monkeypatch):
    ctx = SimpleNamespace(source_novel_dir=bundle[0])
    monkeypatch.setattr(delivery.subprocess, 'run', lambda *a, **kw: pytest.fail('unauthorized upload'))
    assert delivery.deliver_if_configured(ctx, {'passed': True}) == {'passed': True}
    atomic_write_json(bundle[0]/'modelscope_delivery.json', {'repository': 'owner/book'})
    assert delivery.deliver_if_configured(ctx, {'passed': False}) == {'passed': False}


@pytest.mark.parametrize('failure', ['missing_python', 'missing_receipt'])
def test_delivery_start_failure_cannot_discard_media_acceptance(bundle, monkeypatch, failure):
    novel,run,_,_ = bundle
    atomic_write_json(novel/'modelscope_delivery.json', {'repository': 'owner/book'})
    ctx = SimpleNamespace(source_novel_dir=novel, source_episode_dir=novel/'book_7', run_dir=run)
    def submit(*args, **kwargs):
        if failure == 'missing_python': raise FileNotFoundError('upload interpreter unavailable')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(delivery.subprocess, 'run', submit)
    result = delivery.deliver_if_configured(ctx, {'status': 'finished', 'passed': True, 'exit_code': 0})
    assert result['passed'] and result['status'] == 'finished'
    assert result['delivery']['status'] == 'pending' and result['exit_code'] == 2


def test_conductor_keeps_failed_delivery_pending_without_reopening_accepted_media(bundle, monkeypatch):
    from novel_manga.application.production import episode_agents
    novel,run,_,_ = bundle
    saved = {'status': 'finished', 'passed': True, 'attempts': [1, 2], 'deadline': 123,
             'delivery': {'status': 'pending', 'reason': 'retry delivery only'}}
    atomic_write_json(run/'agent-run.json', saved)
    conductor = SimpleNamespace(agent_pause=novel/'pause')
    row = {'run_dir': str(run)}
    monkeypatch.setattr(episode_agents, '_worker', lambda *a: None)
    monkeypatch.setattr(episode_agents.agent_models, 'observe', lambda *a: None)
    episode_agents._observe(conductor, row, [])
    assert row['status'] == 'deferred' and row['phase'] == 'delivery' and row['passed']
    assert read_json(run/'agent-run.json') == saved
    saved['delivery'] = {'verified_remote': True}
    atomic_write_json(run/'agent-run.json', saved)
    episode_agents._observe(conductor, row, [])
    assert row['status'] == 'finished' and row['passed'] and row['waiting_for'] is None
