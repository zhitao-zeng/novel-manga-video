"""A sheet somebody already cut is not a chapter to write.

The sandbox backend reaches the binding path by setting --bind-storyboard, and everything below it
was already correct.  What it did not look at was a branch further down: call_model asks for a story
method first and returns whatever that method generates, so a book naming a local method took it even
for a chapter holding an accepted sandbox storyboard - the method wrote its own scenes, and merge()
then failed on an authored shot it had never produced.

It has never fired, because no book sets story_method today.  That is luck, not design.
"""
from __future__ import annotations

import pytest

from novel_manga.application.planning import requests
from novel_manga.planning.context import PlannerContext


@pytest.fixture
def watched(monkeypatch):
    """call_model with both routes replaced, so the answer is which one it took."""
    taken = []
    monkeypatch.setattr("novel_manga.application.planning.method_pipeline.generate",
                        lambda **kwargs: taken.append("method") or ("{}", {}))
    monkeypatch.setattr(requests, "post_any", lambda *a, **k: taken.append("plain") or ("{}", {}))
    return taken


def call(ctx, watched, **extra):
    try:
        requests.call_model(base_url="http://endpoint", model="m", payload={"chapter_title": "第1章"},
                            schema={}, max_tokens=100, timeout=10, ctx=ctx, **extra)
    except Exception:  # the plain route needs more of the world than this test builds
        pass
    return watched


@pytest.mark.parametrize("story_method", ["", "shanyin", "leos"])
def test_an_authored_sheet_is_bound_whatever_method_the_book_names(story_method, watched):
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard = True
    ctx.story_method = story_method
    assert "method" not in call(ctx, watched, profile={"story_method": story_method})


@pytest.mark.parametrize("source", ["ctx", "profile"])
def test_without_an_authored_sheet_the_named_method_still_runs(source, watched):
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard = False
    ctx.story_method = "shanyin" if source == "ctx" else ""
    call(ctx, watched, profile={"story_method": "shanyin"} if source == "profile" else {})
    assert watched == ["method"]


def test_a_chapter_with_neither_goes_the_plain_route(watched):
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard = False
    ctx.story_method = ""
    assert "method" not in call(ctx, watched, profile={})


def test_authored_binding_is_one_call_without_replanning_or_budget_instructions(monkeypatch):
    import copy
    import json
    from novel_manga.planning.binding import merge
    from novel_manga.planning.constants import AUTHORED_STORYBOARD_USAGE

    shots = [
        {'镜号': '5', '场景': '诊室', '画面内容 / 动作': '托尼飞出窗户，席勒靠回椅背。',
         '景别': '中景', '机位 / 运镜 / 连续性': '固定', '预算秒': 7, '摄影角度': '平视'},
        {'镜号': '6', '场景': '诊室', '画面内容 / 动作': '托尼从窗外飞入，落地站在桌前。',
         '景别': '全景', '机位 / 运镜 / 连续性': '固定', '预算秒': 7, '摄影角度': '平视'},
    ]
    answer = {'bindings': [
        {'镜号': '5', 'segment_id': 'seg_1', 'source_quote': '托尼飞出窗户',
         'start_state': '托尼在诊室内', 'end_state': '托尼在室外，席勒独坐',
         'light': '窗外日光', 'in_frame': ['托尼', '席勒'], 'extras': [], 'actions': []},
        {'镜号': '6', 'segment_id': 'seg_2', 'source_quote': '托尼再次飞入',
         'start_state': '托尼在窗外', 'end_state': '托尼站在桌前',
         'light': '窗外日光', 'in_frame': ['托尼', '席勒'], 'extras': [], 'actions': []},
    ]}
    payload = {'chapter_title': '第12章', 'authored_storyboard': {'shots': shots},
               'segments': [{'segment_id': 'seg_1', 'text': '托尼飞出窗户'},
                            {'segment_id': 'seg_2', 'text': '托尼再次飞入'}],
               'authored_storyboard_usage': '旧说明：重新编写对白',
               'quoted_lines_that_must_be_kept': ['已被改编删除的对白'],
               'planning_budget': {'spoken_min': 2000},
               'requirements': {'source_quote_chars': '8-200', 'one_visible_speaker_per_shot': True}}
    original = copy.deepcopy(payload)
    calls = []
    def post(client, endpoints, headers, request):
        calls.append(request)
        return {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}],
                'usage': {'total_tokens': 123}}
    monkeypatch.setattr(requests, 'post_any', post)
    def unexpected_outline(*args, **kwargs):
        pytest.fail('an authored storyboard must not be adapted again')
    monkeypatch.setattr(requests, 'generate_outline', unexpected_outline)
    ctx = PlannerContext(authored_storyboard=True)
    ctx.outline = {'stale': 'another chapter'}
    content, meta = requests.call_model(base_url='http://endpoint', model='m', payload=payload,
                                       schema={'type': 'object'}, max_tokens=1000, timeout=10, ctx=ctx)
    assert len(calls) == 1
    assert calls[0]['messages'][0]['content'] == AUTHORED_STORYBOARD_USAGE
    sent = json.loads(calls[0]['messages'][1]['content'])
    assert sent['authored_storyboard'] == payload['authored_storyboard']
    assert sent['segments'] == payload['segments']
    assert sent['requirements'] == {'source_quote_chars': '8-200'}
    assert 'planning_budget' not in sent and 'quoted_lines_that_must_be_kept' not in sent
    assert payload == original and ctx.outline == {}
    assert meta['analysis'] == '' and meta['outline_attempts'] == []
    assert meta['usage']['total_tokens'] == 123
    merged = merge(payload['authored_storyboard'], json.loads(content))
    stages = merged['clips'][0]['stages']
    assert stages[0]['end_state'] == answer['bindings'][0]['end_state']
    assert stages[1]['start_state'] == '托尼在窗外'
    assert [s['event'] for s in stages] == [s['画面内容 / 动作'] for s in shots]
