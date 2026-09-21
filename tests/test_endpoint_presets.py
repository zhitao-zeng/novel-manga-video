"""Whatever endpoint a preset names, the request that reaches it still says "do not think".

Every request this project builds carries chat_template_kwargs = {"enable_thinking": False}.  The
streaming transport drops that field - it was written for a platform model behind a proxy, where the
field is an error - and the flashnext preset asked for streaming, so the night planning moved to
Flash-Next every call went out without it.  The model reasoned until the budget ran out: 38,094 tokens
on one chapter's first attempt, and a fill-in-the-blanks binding that ran seventeen minutes and came
back finish_reason=length with its JSON cut in half.  It looked like a slow endpoint.  The endpoint
answers the same question in 0.4 s when the switch is there.
"""
from __future__ import annotations

import os

import httpx
import pytest

from novel_manga.llm import transport
from novel_manga.llm.config import ENDPOINTS, using_endpoint

REQUEST = {"model": "m", "messages": [{"role": "user", "content": "x"}], "max_tokens": 700,
           "chat_template_kwargs": {"enable_thinking": False}}


class Recorder:
    """Stands where httpx.Client does and keeps the body it was handed, by either door."""

    def __init__(self):
        self.sent, self.streamed = None, False

    def post(self, url, headers=None, json=None, **kwargs):
        self.sent = json
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]},
                              request=httpx.Request("POST", url))

    def stream(self, method, url, headers=None, json=None, **kwargs):
        self.sent, self.streamed = json, True
        raise httpx.ConnectError("the recorder does not stream")


@pytest.fixture(autouse=True)
def clean():
    transport._UNREACHABLE.clear()
    yield
    transport._UNREACHABLE.clear()


@pytest.mark.parametrize("name", sorted(ENDPOINTS))
def test_the_thinking_switch_reaches_the_endpoint(name):
    client = Recorder()
    with using_endpoint(name):
        base = os.environ["QWEN38_LOCAL_BASE_URL"].split(",")[0]
        try:
            transport.post_any(client, [base], {}, dict(REQUEST))
        except Exception:                                  # noqa: BLE001 - what was sent is the point
            pass
    assert client.sent is not None
    assert client.sent.get("chat_template_kwargs") == {"enable_thinking": False}


@pytest.mark.parametrize("name", sorted(ENDPOINTS))
def test_no_preset_lets_the_transport_raise_the_token_budget(name):
    """The streaming path floors max_tokens at 50,000; a 700-token question must stay one."""
    client = Recorder()
    with using_endpoint(name):
        base = os.environ["QWEN38_LOCAL_BASE_URL"].split(",")[0]
        try:
            transport.post_any(client, [base], {}, dict(REQUEST))
        except Exception:                                  # noqa: BLE001
            pass
    assert client.sent["max_tokens"] == 700
    assert not client.streamed
