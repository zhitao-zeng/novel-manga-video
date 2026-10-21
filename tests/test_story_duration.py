from copy import deepcopy

import pytest

from novel_manga.planning import budget, contracts, prompts, validation, authored_brief
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode
from tests.test_packer_cuts_long_lines import compiler, long_stage


def flex(chars=2500, **kwargs):
    ctx = PlannerContext(clip_seconds_max=15, short_clips=True)
    settings = budget.configure_budget(chars, shape='flex', ctx=ctx, **kwargs)
    return ctx, settings


@pytest.mark.parametrize('chars', [100, 2500, 9000])
def test_flex_has_no_automatic_episode_floor_or_length_quota(chars):
    ctx, settings = flex(chars)
    assert ctx.episode_shape == 'flex'
    assert ctx.episode_seconds_min == ctx.episode_seconds_target == ctx.episode_seconds_max == 0
    assert not {'episode_target_seconds', 'episode_max_seconds', 'spoken_chars', 'clip_count'} & settings.keys()
    requirements = budget.budget_requirements(ctx=ctx)
    assert 'spoken_chars_total' not in requirements and 'clip_count' not in requirements
    brief = prompts.render_brief(ctx.system_prompt, ctx=ctx)
    assert '按剧情' in brief and '15' in brief
    assert '规划上限' not in brief and '发声字数控制' not in brief
    assert '每个阶段3到7秒' not in brief and '每个区段用1到3个阶段' not in brief
    assert not any(t in brief for t in ('{budget_brief}', '{episode_target}', '{episode_max}', '{clip_lo}', '{spoken_lo}'))
    schema = contracts.build_schema(['甲'], ['庭院'], ['seg_1'], ctx=ctx)
    assert schema['properties']['clips']['minItems'] == 1
    assert 'maxItems' not in schema['properties']['clips']


def test_short_flex_scene_is_not_padded_and_long_scene_keeps_dialogue():
    ctx, _ = flex()
    short = {**long_stage(), 'duration_seconds': 4, 'turns': []}
    errors = []
    budget.validate_duration([short], ctx, errors, [])
    assert not errors
    shots = [deepcopy(long_stage()) for _ in range(20)]
    errors = []
    budget.validate_duration(shots, ctx, errors, [])
    assert not errors
    clips = compiler().pack(shots)
    assert sum(c['seconds'] for c in clips) > 120
    assert all(c['seconds'] <= 15 for c in clips)
    assert [t['text'] for c in clips for s in c['shots'] for t in s['turns']] == [t['text'] for s in shots for t in s['turns']]


def test_explicit_episode_bounds_and_uncuttable_single_shot_still_apply():
    ctx, _ = flex(min_seconds=160)
    ctx.episode_seconds_max = 180
    errors = []
    budget.validate_duration([{**long_stage(), 'duration_seconds': 4, 'turns': []}], ctx, errors, [])
    assert any(i.code == PlanningCode.DURATION_BELOW_MINIMUM for i in errors)
    ctx.episode_seconds_min = 0
    errors = []
    budget.validate_duration([deepcopy(long_stage()) for _ in range(20)], ctx, errors, [])
    assert any(i.code == PlanningCode.DURATION_ABOVE_MAXIMUM for i in errors)
    ctx.episode_seconds_max = 0
    errors = []
    budget.validate_duration([{**long_stage(), 'duration_seconds': 20}], ctx, errors, [])
    assert any(i.code == PlanningCode.STAGE_ABOVE_MAXIMUM for i in errors)


def test_strict_speech_setting_cannot_reintroduce_a_flex_word_quota():
    ctx, _ = flex()
    ctx.strict_plan = True
    assert validation.strict_plan_issues([long_stage() for _ in range(20)], '原文', [], {}, ctx=ctx) == []


def test_agent_authoring_brief_also_uses_story_length(monkeypatch):
    monkeypatch.setenv('NOVEL_CLIP_SECONDS_MAX', '15')
    b = authored_brief.episode_budget('甲开门。', {'episode_shape': 'flex'})
    text = authored_brief.task_note({'title': '测试'}, 1, [], {'frame': '16:9', 'style': '3d'}, b)
    assert '按剧情' in text and '15' in text
    assert '超了会被退回' not in text and '全集说出口的字数控制' not in text
    assert '0–0' not in text and '0 秒' not in text


def test_explicit_flex_ceiling_supplies_compression_target_without_word_quota(tmp_path, monkeypatch):
    import json
    import sys
    from novel_manga.application.planning import cli
    source = tmp_path/'source.txt'
    source.write_text('第一章 庭院\n主角推开院门，走进庭院。')
    bible = tmp_path/'bible.json'
    bible.write_text(json.dumps({'novel_title':'测试','genre':'通用','visual_style':'国漫',
        'palette':'青色','style_fingerprint':'test',
        'characters':[{'name':'主角','role':'主角','appearance':'黑发','wardrobe':'青衣'}],
        'locations':['庭院：院门内有石桌']}))
    monkeypatch.setattr(sys,'argv',['plan_chapter_thin.py',str(source),'--novel-id','demo',
        '--bible',str(bible),'--output-root',str(tmp_path/'out'),'--tier','fast',
        '--max-seconds','60','--dry-run'])
    ctx = PlannerContext(clip_seconds_max=15, short_clips=True)
    assert cli.main(context=ctx) == 0
    assert ctx.episode_seconds_max == ctx.episode_seconds_target == 60
    payload = json.loads((tmp_path/'out/demo/demo_1/request_dry_run.json').read_text())
    assert payload['requirements']['episode_max_seconds'] == 60
    assert 'spoken_chars_total' not in payload['requirements']
