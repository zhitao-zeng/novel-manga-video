import fcntl
from contextlib import contextmanager
from novel_manga.application.identity import ledger_store as store, ledger_flow as flow, ledger_extraction as extraction, ledger_resolution as resolve, ledger_judges as judges


def assert_unlocked(book):
    with (book / 'entity/ledger.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)


def test_extraction_model_is_outside_book_lock_and_preserves_other_writer(tmp_path, monkeypatch):
    def model(*args):
        assert_unlocked(tmp_path)
        with store.ledger_lock(tmp_path):
            current = store.Ledger(tmp_path)
            current._add_entity('另一会话角色', 'person', True, '', 2)
            current.save()
        return {'mentions': [], 'new_entities': [], 'claims': [], 'relations': []}
    monkeypatch.setattr(judges, 'extract_chapter', model)
    flow.run_extract(tmp_path, [1], workers=1, texts={1: '原文'})
    assert store.read_json(tmp_path/'entity/raw/ch_0001.json', {})['known_through'] == 0
    assert store.Ledger(tmp_path).entities[0]['canonical'] == '另一会话角色'
    assert (tmp_path/'entity/raw/ch_0001.json').is_file()


def test_healthy_raw_cache_is_reused_without_model_call(tmp_path, monkeypatch):
    ledger = store.Ledger(tmp_path)
    raw = {'mentions': [], 'new_entities': [], 'claims': [], 'relations': [], 'known_through': 0}
    store.write_json(ledger.raw_path(1), raw)
    monkeypatch.setattr(judges, 'extract_chapter', lambda *a: (_ for _ in ()).throw(AssertionError('unexpected model')))
    assert extraction.extract(ledger, 1, '原文') == raw


def test_ordered_resolution_reloads_ledger_after_each_release(tmp_path, monkeypatch):
    seen = []
    def chapter(ledger, ch, text, **kwargs):
        seen.append([e['canonical'] for e in ledger.entities])
        ledger._add_entity(f'本章{ch}', 'person', True, '', ch)
        ledger.save()
        return {'mentions': 0, 'new': [], 'claims': {}, 'dropped': 0}
    def between(*args, **kwargs):
        if len(seen) == 1:
            assert_unlocked(tmp_path)
            with store.ledger_lock(tmp_path):
                ledger = store.Ledger(tmp_path)
                ledger._add_entity('章间另一写者', 'person', True, '', 1)
                ledger.save()
    monkeypatch.setattr(resolve, 'resolve_chapter', chapter)
    monkeypatch.setattr(flow, 'print', between, raising=False)
    flow.run_resolve(tmp_path, [1, 2], workers=1, texts={1: '原文一', 2: '原文二'})
    assert seen == [[], ['本章1', '章间另一写者']]
    assert [e['canonical'] for e in store.Ledger(tmp_path).entities] == ['本章1', '章间另一写者', '本章2']


def test_late_rereading_does_not_reacquire_owned_book_lock(tmp_path, monkeypatch):
    ledger = store.Ledger(tmp_path)
    ledger._add_entity('本章角色', 'person', True, '', 1)
    raw = {'mentions': [], 'new_entities': [], 'claims': [], 'relations': [], 'known_through': 0}
    monkeypatch.setattr(extraction, 'stale', lambda *a: [ledger.entities[0]])
    monkeypatch.setattr(judges, 'extract_chapter', lambda *a: {'mentions': [], 'new_entities': [], 'claims': [], 'relations': []})
    original = store.ledger_lock
    owned = False
    @contextmanager
    def lock(book):
        nonlocal owned
        assert not owned, 'nested book flock would deadlock'
        with original(book):
            owned = True
            try: yield
            finally: owned = False
    monkeypatch.setattr(store, 'ledger_lock', lock)
    with store.ledger_lock(tmp_path):
        assert resolve.resolve_chapter(ledger, 1, '本章角色在场。', raw, workers=1) is not None
    assert store.read_json(ledger.raw_path(1), {})['fresh'] is True
