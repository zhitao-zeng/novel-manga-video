"""Whether an endpoint is up is a fact about right now, not a line in a file.

QWEN38_LOCAL_BASE_URL lists four local Qwen instances and one of them has been stopped for days.  The
list does not know that, and requests are spread over it by a hash of the chapter - so a quarter of
them opened with a connect timeout, fell through to the next instance, and did it again on the next
request.  3.1 seconds each, all day, for something the first failure already established.

The H3 pool has had a cooldown for months (configs/h3_pool.json).  The model client had none.
"""
from __future__ import annotations

import httpx
import pytest

from novel_manga.llm import transport


@pytest.fixture(autouse=True)
def clean():
    transport._UNREACHABLE.clear()
    yield
    transport._UNREACHABLE.clear()


class FakeClient:
    """Answers from `up`, refuses from anywhere else, and remembers who was asked."""

    def __init__(self, up):
        self.up, self.asked = up, []

    def post(self, url, headers=None, json=None, **kwargs):
        self.asked.append(url)
        if not any(url.startswith(base) for base in self.up):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"ok": url}, request=httpx.Request("POST", url))


DEAD, LIVE, OTHER = "http://dead/v1", "http://live/v1", "http://other/v1"


def test_a_refusal_is_remembered_so_the_next_request_does_not_repeat_it():
    client = FakeClient(up=[LIVE])
    assert transport.post_any(client, [DEAD, LIVE], {}, {})["ok"].startswith(LIVE)
    assert [u.split("/chat")[0] for u in client.asked] == [DEAD, LIVE]

    client.asked.clear()
    transport.post_any(client, [DEAD, LIVE], {}, {})
    assert [u.split("/chat")[0] for u in client.asked] == [LIVE]      # the dead one is not tried again


def test_an_endpoint_that_answers_clears_what_we_believed_about_it():
    client = FakeClient(up=[LIVE])
    transport.post_any(client, [DEAD, LIVE], {}, {})
    assert DEAD in transport._UNREACHABLE
    client.up.append(DEAD)
    transport._UNREACHABLE[DEAD] = 0                                  # cooldown expired
    transport.post_any(client, [DEAD, LIVE], {}, {})
    assert DEAD not in transport._UNREACHABLE                         # it came back; forget the note


def test_a_cooldown_moves_an_endpoint_to_the_back_and_never_out_of_the_list():
    """A cooldown is a guess.  A guess must not be able to leave a request with nowhere to go."""
    transport._UNREACHABLE[DEAD] = float("inf")
    assert transport.reachable_first([DEAD, LIVE, OTHER]) == [LIVE, OTHER, DEAD]
    for url in (LIVE, OTHER):
        transport._UNREACHABLE[url] = float("inf")
    assert transport.reachable_first([DEAD, LIVE, OTHER]) == [DEAD, LIVE, OTHER]


def test_everything_in_cooldown_still_gets_tried():
    client = FakeClient(up=[OTHER])
    for url in (DEAD, LIVE, OTHER):
        transport._UNREACHABLE[url] = float("inf")
    assert transport.post_any(client, [DEAD, LIVE, OTHER], {}, {})["ok"].startswith(OTHER)


def test_a_gateway_that_cannot_reach_its_backend_counts_as_unreachable():
    class Gateway(FakeClient):
        def post(self, url, headers=None, json=None, **kwargs):
            self.asked.append(url)
            if url.startswith(DEAD):
                return httpx.Response(503, json={}, request=httpx.Request("POST", url))
            return httpx.Response(200, json={"ok": url}, request=httpx.Request("POST", url))

    client = Gateway(up=[LIVE])
    transport.post_any(client, [DEAD, LIVE], {}, {})
    assert DEAD in transport._UNREACHABLE


def test_a_real_error_is_not_turned_into_a_cooldown():
    """A 400 is this request's problem, not the endpoint's; hiding it would move the fault."""
    class Refuses(FakeClient):
        def post(self, url, headers=None, json=None, **kwargs):
            self.asked.append(url)
            return httpx.Response(400, json={}, request=httpx.Request("POST", url))

    with pytest.raises(httpx.HTTPStatusError):
        transport.post_any(Refuses(up=[]), [LIVE], {}, {})
    assert LIVE not in transport._UNREACHABLE
