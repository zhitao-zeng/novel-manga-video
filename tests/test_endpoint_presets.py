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


# ---- what a review of c9718c4 found ------------------------------------------------------------------

from novel_manga.llm.config import ENDPOINTS, endpoint_settings, planner_endpoint_name


def test_a_preset_puts_back_exactly_what_it_found(monkeypatch):
    """The reason using_endpoint exists, and the one thing nothing tested: seven main() calls in one
    pytest process, each applying a preset, and every test after them reading whatever was left."""
    monkeypatch.setenv("QWEN38_LOCAL_BASE_URL", "http://was-here:1/v1")
    monkeypatch.delenv("QWEN38_LOCAL_MODEL", raising=False)
    monkeypatch.setenv("QWEN38_LOCAL_STREAM", "1")
    before = {key: os.environ.get(key) for key in ENDPOINTS["flashnext"]}
    with using_endpoint("flashnext"):
        assert os.environ["QWEN38_LOCAL_MODEL"] == "Qwen3.8-Flash-Next"
        assert os.environ["QWEN38_LOCAL_STREAM"] == "0"
    assert {key: os.environ.get(key) for key in ENDPOINTS["flashnext"]} == before
    assert "QWEN38_LOCAL_MODEL" not in os.environ          # unset before, unset after - not set to ""


def test_a_preset_is_put_back_even_when_the_run_raises(monkeypatch):
    monkeypatch.setenv("QWEN38_LOCAL_BASE_URL", "http://was-here:1/v1")
    with pytest.raises(RuntimeError):
        with using_endpoint("flashnext"):
            raise RuntimeError("the planner blew up")
    assert os.environ["QWEN38_LOCAL_BASE_URL"] == "http://was-here:1/v1"


@pytest.mark.parametrize("value, expected", [(None, "flashnext"), ("", "flashnext"), ("  ", "flashnext"), ("local", "local")])
def test_an_empty_planner_endpoint_is_the_default_not_an_endpoint_called_nothing(value, expected):
    environ = {} if value is None else {"NOVEL_PLANNER_ENDPOINT": value}
    assert planner_endpoint_name(environ) == expected


def test_a_planner_endpoint_that_names_nothing_is_refused_with_the_list():
    with pytest.raises(ValueError, match="flashnext.*local|local.*flashnext"):
        planner_endpoint_name({"NOVEL_PLANNER_ENDPOINT": "flashnxt"})


def test_named_settings_leave_the_process_environment_alone(monkeypatch):
    monkeypatch.setenv("QWEN38_LOCAL_BASE_URL", "http://was-here:1/v1")
    settings = endpoint_settings("flashnext")
    assert settings.model == "Qwen3.8-Flash-Next" and settings.endpoints == ("http://172.28.4.81:8038/v1",)
    assert os.environ["QWEN38_LOCAL_BASE_URL"] == "http://was-here:1/v1"


def test_the_bibles_questions_go_to_the_judge_whatever_endpoint_plans(monkeypatch):
    """Inside the planner the process is on Flash-Next; who a name is, is still the 27B's to say."""
    from novel_manga.application.review import bible as review_bible
    asked = {}
    def fake_ask(parts, schema, **kwargs):
        asked["settings"] = kwargs.get("settings")
        return {"characters": []}
    monkeypatch.setattr(review_bible.model_client, "ask_json", fake_ask)
    with using_endpoint("flashnext"):
        review_bible.extract_names("席勒站在讲台后。")
    assert asked["settings"] is not None
    assert asked["settings"].model == "Qwen3.8-27B-Project"


def test_the_preflight_probes_the_endpoint_planning_will_use_and_the_judges(monkeypatch):
    """3/4 alive, then two hundred chapters refused: the probe and the planner disagreed on where."""
    from novel_manga.application.production import flow as production_flow
    probed = []
    monkeypatch.setattr(production_flow.Batch, "endpoint_answers", staticmethod(lambda base, key="": probed.append(base) or True))
    monkeypatch.setattr(production_flow.production_common, "log", lambda *a, **k: None)
    batch = object.__new__(production_flow.Batch)
    batch.check_qwen()
    assert "http://172.28.4.81:8038/v1" in probed                      # the planner's
    assert any(":1812" in base for base in probed)                      # the judge's pool


def test_the_preflight_stops_when_the_planners_endpoint_is_the_dead_one(monkeypatch):
    from novel_manga.application.production import flow as production_flow
    monkeypatch.setattr(production_flow.Batch, "endpoint_answers", staticmethod(lambda base, key="": "8038" not in base))
    monkeypatch.setattr(production_flow.production_common, "log", lambda *a, **k: None)
    batch = object.__new__(production_flow.Batch)
    with pytest.raises(SystemExit, match="规划"):
        batch.check_qwen()
