"""Regression tests must supply a simulated HTTP transport, never a production model."""
import httpx
import pytest


@pytest.fixture(autouse=True)
def forbid_unmocked_http(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('Unmocked HTTP connection in test: supply MockTransport or mock the model call', pytrace=False)
    async def blocked_async(*args, **kwargs):
        blocked()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', blocked_async)


@pytest.fixture(autouse=True)
def isolated_identity_semantic_service(monkeypatch, request):
    """Other unit tests isolate the model boundary; integration cases exercise the actual closure."""
    if request.path.name == 'test_identity_auto_closure.py':
        return
    from novel_manga.application.identity import semantic_review
    def book(ledger, chapter, text, raw, **kwargs):
        return raw, {'approved': True}
    def bindings(directory, answer, catalog, segments, mapped, **kwargs):
        current={m['source_actor']:m['entity_id'] for m in mapped['mentions']}
        rows=[{'source_id':a['source_id'],'entity_id':current.get(a['source_id'],'UNKNOWN'),
               'decision':'catalogue' if current.get(a['source_id'],'UNKNOWN')!='UNKNOWN' else 'unknown',
               'paragraphs':a['paragraphs'],'reason':''} for a in answer['actors']]
        return {'actors':answer['actors'],'bindings':rows}, {'approved': True}
    monkeypatch.setattr(semantic_review, 'book', book)
    monkeypatch.setattr(semantic_review, 'bindings', bindings)
