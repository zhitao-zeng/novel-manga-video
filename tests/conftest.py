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
