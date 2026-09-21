"""Scene writing must survive director choices, packing and the English H3 compiler."""
import copy
import json
from pathlib import Path

import pytest

from novel_manga.planning.methods import METHODS
from novel_manga.planning.methods.scenes import normalize_screenplay, screenplay_schema, screenplay_prompt, apply_scene_revision
from novel_manga.planning.methods.direction import project_direction, direction_schema, validate_handoff, grounded_review, direction_budget
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.budget import configure_budget
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.application.planning import requests
from novel_manga.application.packing.context import load_context
from novel_manga.application.packing.service import compile_plan
from novel_manga.models.bible import StoryBible
from novel_manga.story.h3 import request_issues
from novel_manga.application.rendering import h3


def payload():
    return {'chapter_title': '溪岸', 'chapter_chars': 100,
            'available_characters': ['梁舟'], 'available_locations': ['溪岸'],
            'anonymous_offscreen_speakers': ['无名老妇人'],
            'production_profile': {'style': '3d', 'frame': '16:9'},
            'planning_budget': {'clip_seconds': [4, 15]},
            'segments': [{'segment_id': 'seg_1', 'text': '梁舟把背包拖上溪岸，低头检查湿透的背带。'},
                         {'segment_id': 'seg_2', 'text': '第二天灰色山羊冲向梁舟，他举起木棍横挡，山羊撞上木棍。'}]}


def bible():
    return {'novel_title': '测试', 'genre': 'generic', 'visual_style': '统一3D动画',
            'palette': '自然色', 'style_fingerprint': 'fixture',
            'characters': [{'name': '梁舟', 'appearance': '黑发青年', 'wardrobe': '青衣'}],
            'locations': ['溪岸：溪流与岩石']}


def written():
    data = payload()
    return {'video_title': '溪岸', 'hook': '背包还在水中', 'summary': '取包，次日挡住山羊',
            'episode_contract': {'goal': '取回装备继续前进', 'obstacle': '溪水与山羊',
                                 'outcome': '取包后挡住撞击', 'exit_state': '梁舟持棍站稳'},
            'omissions': [], 'scenes': [
                {'location': '溪岸', 'time': '第一天白天', 'transition': 'opening', 'purpose': '装备回到手里',
                 'entry_state': '背包仍在溪水中', 'exit_state': '梁舟坐在岸边，背包放在膝前',
                 'units': [{'source_segment_id': 'seg_1', 'source_quote': data['segments'][0]['text'], 'action': '梁舟把背包拖上岸，坐下查看背带。',
                            'sfx': '水声，背包落地后水声变轻', 'turns': [
                                {'speaker_name': '梁舟', 'text': '终于……拿回来了。', 'emotion': '松气',
                                 'delivery_mode': 'visible_dialogue', 'chat_target': ''}]}]},
                {'location': '溪岸', 'time': '第二天白天', 'transition': 'time_jump', 'purpose': '挡住冲撞',
                 'entry_state': '梁舟持木棍，山羊从草丛接近', 'exit_state': '木棍横在胸前，山羊停止向前',
                 'units': [{'source_segment_id': 'seg_2', 'source_quote': data['segments'][1]['text'], 'action': '灰色山羊撞上梁舟横举的木棍。',
                            'sfx': '碰撞声，蹄声停止', 'turns': []}]}]}


def directed(script, key='leos'):
    method = METHODS[key]
    return {'method_plan': {k: '以实际动作和反应完成本集' for k, _ in method.episode_fields},
            'directions': {s['scene_id']: {
                'method_details': {k: '当前刺激与动作相接' for k, _ in method.beat_fields},
                'shots': [{'unit_ids': [u['unit_id'] for u in s['units']],
                           'turn_ids': [t['turn_id'] for u in s['units'] for t in u['turns']],
                           'purpose': s['purpose'], 'start_state': s['entry_state'],
                           'event': s['units'][0]['action'], 'end_state': s['exit_state'],
                           'camera': '溪岸侧面平视，固定', 'light': '左侧日光',
                           'sfx': s['units'][0]['sfx'], 'cut': '动作完成时切',
                           'duration_seconds': 6 if s['units'][0]['turns'] else 3,
                           'shot_scale': '中景', 'in_frame': ['梁舟'],
                           'extras': ['灰色山羊'] if i else [],
                           'actions': ([{'actor': '灰色山羊', 'action': '撞上', 'target': '木棍'}]
                                       if i else [{'actor': '梁舟', 'action': '拖上岸', 'target': '背包'}])}]
            } for i, s in enumerate(script['scenes'])}}


def fixtures(key='leos'):
    script = normalize_screenplay(written(), payload(), METHODS[key])
    direction = directed(script, key)
    return script, direction


@pytest.mark.parametrize('key', METHODS)
def test_each_route_writes_scenes_before_direction_and_reviews_actual_shots(monkeypatch, key):
    script, direction = fixtures(key)
    replies = [written(), {'issues': []}, direction, {'issues': []}]
    calls = []
    def post(client, endpoints, headers, body):
        calls.append(copy.deepcopy(body))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies[len(calls)-1])}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method=key)
    original = payload()
    content, meta = requests.call_model(base_url='http://invalid/v1', model='fake', payload=original,
        schema={}, max_tokens=16000, analysis_tokens=9000, timeout=5, seed=73, ctx=ctx)
    assert original == payload()
    assert [c['response_format']['json_schema']['name'] for c in calls] == ['scene_screenplay', 'screenplay_review', 'scene_direction', 'scene_review']
    assert 'scenes' in calls[0]['response_format']['json_schema']['schema']['properties']
    assert 'beats_by_segment' not in json.dumps(calls[0])
    assert 'screenplay' in json.loads(calls[2]['messages'][1]['content'])
    creative_budget = json.loads(calls[2]['messages'][1]['content'])['planning_budget']
    assert 'clip_seconds' not in creative_budget and 'stages_per_clip' not in creative_budget
    assert creative_budget['single_shot_max_seconds'] == 15
    director_input = json.loads(calls[2]['messages'][1]['content'])
    assert 'segments' not in director_input and 'source_segments' not in director_input['screenplay']
    assert all('source_quote' not in u and 'source_refs' not in u
               for s in director_input['screenplay']['scenes'] for u in s['units'])
    assert 'segments' in json.loads(calls[1]['messages'][1]['content'])
    assert all(s['scene_id'] in calls[2]['response_format']['json_schema']['schema']['properties']['directions']['required'] for s in script['scenes'])
    assert meta['semantic_quality_review'] == 'passed'
    assert not validate_handoff(json.loads(content), ctx.story_blueprint)
    assert len(ctx.method_artifacts) == 9


def test_visual_methods_use_equal_scene_writer_inputs_and_no_source_bucket_quota():
    assert len({screenplay_prompt(METHODS[k]) for k in ('community', 'dream', 'leos', 'visual')}) == 1
    schema = screenplay_schema(payload())['properties']['scenes']['items']['properties']['units']
    assert schema['maxItems'] > 2  # Capacity bound, not a per-source creative quota.
    data = written()
    # Presentation order may intentionally revisit earlier source evidence.
    data['scenes'].reverse()
    data['scenes'][0]['transition'] = 'opening'
    data['scenes'][1]['transition'] = 'flashback'
    before = copy.deepcopy(data)
    result = normalize_screenplay(data, payload(), METHODS['visual'])
    assert data == before
    assert result['scenes'][0]['units'][0]['source_refs'][0]['segment_id'] == 'seg_2'
    assert result['scenes'][1]['transition'] == 'flashback'


def test_handoff_keeps_exact_dialogue_animal_and_prop_targets_without_mutation():
    script, direction = fixtures()
    original = copy.deepcopy((script, direction))
    raw = project_direction(script, direction)
    assert (script, direction) == original
    assert raw['clips'][0]['stages'][0]['turns'][0]['text'] == '终于……拿回来了。'
    assert raw['clips'][1]['stages'][0]['actions'][0] == {'actor': '灰色山羊', 'action': '撞上', 'target': '木棍'}
    damaged = copy.deepcopy(direction)
    damaged['directions']['SC001']['shots'][0]['turn_ids'] *= 2
    with pytest.raises(ValueError, match='台词'):
        project_direction(script, damaged)


def test_scoped_review_repair_does_not_rewrite_other_scenes(monkeypatch):
    script, direction = fixtures()
    revised = copy.deepcopy(direction)
    revised['directions'] = {'SC002': revised['directions']['SC002']}
    revised['directions']['SC002']['shots'][0]['camera'] = '溪岸低机位，先看清木棍与山羊接触'
    finding = {'item_id': 'SC002-SH01', 'upstream_id': 'SC002-U01',
               'issue': '接触位置未给出可见范围', 'repair': '机位明确接触点'}
    replies = [written(), {'issues': []}, direction, {'issues': [finding]}, revised, {'issues': []}]
    def post(client, endpoints, headers, body):
        value = replies.pop(0)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method='leos')
    requests.call_model(base_url='http://invalid/v1', model='fake', payload=payload(), schema={},
        max_tokens=5000, analysis_tokens=5000, timeout=5, ctx=ctx)
    assert ctx.story_blueprint['direction']['directions']['SC001'] == direction['directions']['SC001']
    assert '低机位' in ctx.story_blueprint['direction']['directions']['SC002']['shots'][0]['camera']
    assert ctx.story_blueprint['initial_review']['issues'][0]['item_id'] == finding['item_id']
    assert not ctx.story_blueprint['review']['issues']


def test_scene_boundary_duration_and_sound_survive_native_packing_and_h3(tmp_path, monkeypatch):
    script, direction = fixtures()
    bp = {**script, 'direction': direction, 'script_review': {'completed': True, 'issues': []}, 'review': {'completed': True, 'issues': []}}
    raw = project_direction(script, direction)
    ctx = PlannerContext(story_method='leos', story_blueprint=bp)
    configure_budget(100, fast=False, ctx=ctx); ctx.spoken_range = (0, 300)
    b = StoryBible.model_validate(bible())
    valid = validate_and_normalize(raw, payload()['segments'], b, {'溪岸': b.locations[0]},
                                   '\n'.join(s['text'] for s in payload()['segments']), ctx=ctx)
    assert not valid.errors
    assert valid.shots[0]['motion_prompt'] == direction['directions']['SC001']['shots'][0]['event']
    book = tmp_path / 'trial'; episode = book / 'trial_1'; episode.mkdir(parents=True)
    (book / 'story_bible.json').write_text(json.dumps(bible(), ensure_ascii=False))
    (episode / 'segments.json').write_text(json.dumps(payload()['segments'], ensure_ascii=False))
    output = {'story_method': METHODS['leos'].describe(), 'profile': payload()['production_profile'], 'shots': valid.shots}
    (episode / 'chapter_script.json').write_text(json.dumps(output, ensure_ascii=False))
    context = load_context(episode, book / 'story_bible.json')
    plan, decisions = compile_plan(output, context)
    assert len(plan['clips']) == 2  # Both <8s in one location; absorb must respect the day change.
    assert [c['seconds_estimate'] for c in plan['clips']] == [6, 3]
    assert [c['scene_ids'] for c in plan['clips']] == [['SC001'], ['SC002']]
    assert '故事时间：第二天白天' in plan['clips'][1]['prompt']
    seen = []
    def translate(parts, schema, **kwargs):
        seen.append(parts[0]['text'])
        return {'shots': ['A grey goat strikes the held stick; its hoofbeats stop on contact.']}
    monkeypatch.setattr(h3, 'ask_json', translate)
    clip = plan['clips'][1]
    assert h3.convert(clip, tries=1)
    assert '蹄声停止' in seen[0]
    assert 'hoofbeats stop' in clip['prompt_h3']
    assert 'consistent stylized 3D animation' in clip['prompt_h3']
    assert 'which garments are currently worn, removed or wet' in clip['prompt_h3']
    assert not request_issues(clip)
    assert 'non_diegetic_music:\nNone.' in clip['prompt_h3']
    spoken = plan['clips'][0]
    assert h3.convert(spoken, tries=1)
    assert '<d>[Chinese] 终于……拿回来了。</d>' in spoken['prompt_h3']
    assert not request_issues(spoken)


def test_review_or_downstream_changes_cannot_claim_an_accepted_handoff():
    script, direction = fixtures()
    bp = {**script, 'direction': direction, 'script_review': {'completed': True, 'issues': []}, 'review': {'completed': True, 'issues': [{'issue': '仍有跨夜合镜'}]}}
    raw = project_direction(script, direction)
    assert validate_handoff(raw, bp) == ['仍有跨夜合镜']
    raw['clips'][0]['stages'][0]['turns'][0]['speaker_name'] = '山羊'
    assert any('交接后被改写' in s for s in validate_handoff(raw, bp))


def test_long_dialogue_is_split_before_directing_without_changing_its_owner_or_words():
    data = written()
    line = '我先把背包从水里拖出来，检查里面的工具，再带上它沿着溪流走。到了山口以后，还要看看地图上的地方在哪里。'
    line *= 2
    data['scenes'][0]['units'][0]['turns'][0]['text'] = line
    script = normalize_screenplay(data, payload(), METHODS['drama'])
    turns = script['scenes'][0]['units'][0]['turns']
    assert len(turns) > 1 and ''.join(t['text'] for t in turns) == line
    assert {t['speaker_name'] for t in turns} == {'梁舟'}
    assert len({t['turn_id'] for t in turns}) == len(turns)
    with pytest.raises(ValueError, match='台词至少估算'):
        project_direction(script, directed(script, 'drama'))


def test_citation_must_belong_to_its_declared_source_segment():
    data = written()
    data['scenes'][0]['units'][0]['source_segment_id'] = 'seg_2'
    with pytest.raises(ValueError, match='引文存在.*定位不符'):
        normalize_screenplay(data, payload(), METHODS['drama'])


def test_contiguous_quote_across_program_segments_keeps_both_source_addresses():
    data = written()
    data['scenes'][0]['units'][0]['source_quote'] = ''.join(s['text'] for s in payload()['segments'])
    data['scenes'][0]['units'][0]['source_segment_id'] = 'seg_2'  # A covered segment, not necessarily the first.
    original = copy.deepcopy(data)
    script = normalize_screenplay(data, payload(), METHODS['drama'])
    refs = script['scenes'][0]['units'][0]['source_refs']
    assert [r['segment_id'] for r in refs] == ['seg_1', 'seg_2']
    raw = project_direction(script, directed(script, 'drama'))
    assert raw['clips'][0]['stages'][0]['source_quote'] == data['scenes'][0]['units'][0]['source_quote']
    assert raw['clips'][0]['stages'][0]['source_refs'] == refs
    assert data == original
    data['scenes'][0]['units'][0]['source_quote'] = '梁舟把背包拖上溪岸，第二天灰色山羊冲向梁舟'
    with pytest.raises(ValueError, match='引文不在完整原文中'):
        normalize_screenplay(data, payload(), METHODS['drama'])  # Omitting intervening words is not a continuous quote.


def test_animal_or_landscape_shot_does_not_gain_a_named_actor_from_context():
    script, direction = fixtures()
    direction['directions']['SC002']['shots'][0]['in_frame'] = []
    bp = {**script, 'direction': direction, 'script_review': {'completed': True, 'issues': []}, 'review': {'completed': True, 'issues': []}}
    ctx = PlannerContext(story_method='leos', story_blueprint=bp)
    configure_budget(100, fast=False, ctx=ctx); ctx.spoken_range = (0, 300)
    b = StoryBible.model_validate(bible())
    result = validate_and_normalize(project_direction(script, direction), payload()['segments'], b,
                                   {'溪岸': b.locations[0]}, '\n'.join(s['text'] for s in payload()['segments']), ctx=ctx)
    assert not result.errors
    assert result.shots[1]['in_frame'] == result.shots[1]['characters'] == []
    assert result.shots[1]['extras'] == ['灰色山羊']


def test_v2_cli_replay_writes_both_scene_screenplay_and_director_artifacts(tmp_path, monkeypatch):
    import sys
    from novel_manga.application.planning import cli
    source = tmp_path / 'source.txt'
    source.write_text('第一章 溪岸\n' + '\n'.join(s['text'] for s in payload()['segments']))
    bible_path = tmp_path / 'bible.json'; bible_path.write_text(json.dumps(bible(), ensure_ascii=False))
    args = ['plan_chapter_thin.py', str(source), '--bible', str(bible_path), '--novel-id', 'sample',
            '--output-root', str(tmp_path / 'out'), '--story-method', 'leos', '--max-redo', '0']
    monkeypatch.setattr(sys, 'argv', [*args, '--dry-run']); assert cli.main() == 0
    directory = tmp_path / 'out/sample/sample_1'
    actual = json.loads((directory / 'request_dry_run.json').read_text())
    script = normalize_screenplay(written(), actual, METHODS['leos'])
    direction = directed(script)
    bp = {**script, 'direction': direction, 'script_review': {'completed': True, 'issues': []}, 'review': {'completed': True, 'issues': []}}
    model = tmp_path / 'model'; model.mkdir()
    raw = model / 'response_attempt_01.raw.json'
    raw.write_text(json.dumps(project_direction(script, direction), ensure_ascii=False))
    (model / 'analysis_attempt_01.txt').write_text(json.dumps(bp, ensure_ascii=False))
    monkeypatch.setattr(requests, 'post_any', lambda *a, **k: pytest.fail('replay must not call a model'))
    monkeypatch.setattr(sys, 'argv', [*args, '--replay', str(raw)])
    assert cli.main() == 0
    assert '分场剧本' in (directory / 'scene_script.md').read_text()
    assert '导演镜号' in (directory / 'chapter_script.md').read_text()
    report = json.loads((directory / 'chapter_script_report.json').read_text())
    assert report['story_method_checks']['semantic_quality_review'] == 'passed'


@pytest.mark.parametrize('method,maximum,expected', [
    ('leos', None, None), ('leos', 140, 140), ('leos', 60, 60), ('default', None, 120),
])
def test_cli_preview_uses_the_actual_creative_budget_without_render_quotas(tmp_path, monkeypatch, method, maximum, expected):
    import sys
    from novel_manga.application.planning import cli
    from novel_manga.planning.methods.scenes import screenplay_input
    source = tmp_path / 'source.txt'
    source.write_text('第一章 溪岸\n' + '\n'.join(s['text'] for s in payload()['segments']))
    bible_path = tmp_path / 'bible.json'
    bible_path.write_text(json.dumps(bible(), ensure_ascii=False))
    args = ['plan_chapter_thin.py', str(source), '--bible', str(bible_path), '--novel-id', 'sample',
            '--output-root', str(tmp_path / 'out'), '--story-method', method, '--dry-run']
    if maximum is not None:
        args += ['--max-seconds', str(maximum)]
    monkeypatch.setattr(sys, 'argv', args)
    monkeypatch.setattr(requests, 'post_any', lambda *a, **k: pytest.fail('preview must not call a model'))
    assert cli.main() == 0
    directory = tmp_path / 'out/sample/sample_1'
    raw = json.loads((directory / 'request_dry_run.json').read_text())
    assert raw['planning_budget']['episode_max_seconds'] == expected
    if method != 'default':
        preview = json.loads((directory / 'method_request_dry_run.json').read_text())['payload']
        assert preview == screenplay_input(raw)
        assert 'requirements' not in preview and 'policy' not in preview
        assert 'clip_seconds' not in preview['planning_budget']
        assert preview['planning_budget']['episode_target_seconds'] == min(90, maximum or 90)


def test_plain_dialogue_mislabeled_as_singing_is_rejected_before_direction():
    data = written()
    t = data['scenes'][0]['units'][0]['turns'][0]
    t.update(delivery_mode='singing', text='这个背包总算是从水里完整地拿回来了。')
    with pytest.raises(ValueError, match='singing'):
        normalize_screenplay(data, payload(), METHODS['shanyin'])


def test_review_cannot_use_unknown_ids_or_another_scenes_upstream_unit():
    script, direction = fixtures()
    issue = {'item_id': 'SC002-SH01', 'upstream_id': 'SC001-U01', 'issue': '问题', 'repair': '修正'}
    with pytest.raises(ValueError, match='跨场'):
        grounded_review({'issues': [issue]}, script, direction, payload(), 'shots')
    issue.update(item_id='episode', upstream_id='not-a-source-id')
    with pytest.raises(ValueError, match='不存在'):
        grounded_review({'issues': [issue]}, script, direction, payload(), 'script')
    issue.update(item_id='SC001', upstream_id='seg_1')
    result = grounded_review({'issues': [issue]}, script, None, payload(), 'script')['issues'][0]
    assert result['source_quote'] == payload()['segments'][0]['text']
    assert '梁舟把背包拖上岸' in result['draft_quote']


def test_citation_retry_cannot_discard_other_grounded_findings(monkeypatch):
    script, direction = fixtures()
    good = {'item_id': 'SC002-SH01', 'upstream_id': 'SC002-U01',
            'issue': '接触点需给出位置', 'repair': '明确位置'}
    bad = {**good, 'item_id': 'not-a-shot'}
    corrected = copy.deepcopy(direction)
    corrected['directions'] = {'SC002': corrected['directions']['SC002']}
    corrected['directions']['SC002']['shots'][0]['camera'] = '溪岸低机位，看清接触点'
    replies = [written(), {'issues': []}, direction, {'issues': [good, bad]}, {'issues': []}, corrected, {'issues': []}]
    def post(client, endpoints, headers, body):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies.pop(0))}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method='leos')
    requests.call_model(base_url='http://invalid/v1', model='fake', payload=payload(), schema={},
                        max_tokens=5000, analysis_tokens=5000, timeout=5, ctx=ctx)
    assert [i['item_id'] for i in ctx.story_blueprint['initial_review']['issues']] == [good['item_id']]
    assert not ctx.story_blueprint['review']['issues']


def test_h3_does_not_accept_chinese_sound_instructions_outside_dialogue(monkeypatch):
    clip = {'clip_id': 'x', 'scene_ids': ['SC001'], 'request_seconds': 6, 'references': [],
            'prompt': '【阶段一·中景】开始时：空草地。声音：风声。结束时：草被风压倒。',
            'shot_sound': ['风声停止'], 'dialogue_bindings': []}
    monkeypatch.setattr(h3, 'ask_json', lambda *a, **k: {'shots': ['Wind stops. 风声停止。']})
    assert not h3.convert(clip, tries=1)
    assert 'prompt_h3' not in clip


def test_reuse_scene_script_skips_writing_and_cannot_carry_an_old_review(monkeypatch):
    script, direction = fixtures()
    script['direction'] = {'old': 'must not be reused'}
    script['review'] = {'completed': True, 'issues': [{'issue': 'old finding'}]}
    script['omissions'] = [{'source_segment_id': 'seg_1', 'reason': '省略不影响动作的议论',
                            'source_quote': '模型曾经错误重打的省略引文'}]
    replies = [{'issues': []}, direction, {'issues': []}]
    calls = []
    def post(client, endpoints, headers, body):
        calls.append(body)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies.pop(0))}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method='leos')
    _, meta = requests.call_model(base_url='http://invalid/v1', model='fake', payload=payload(), schema={},
        max_tokens=5000, timeout=5, ctx=ctx, scene_script=script)
    assert [c['response_format']['json_schema']['name'] for c in calls] == ['screenplay_review', 'scene_direction', 'scene_review']
    upstream = json.loads(calls[1]['messages'][1]['content'])['screenplay']
    assert 'direction' not in upstream and 'review' not in upstream
    assert meta['scene_script_reused']
    assert ctx.story_blueprint['review']['issues'] == []
    assert ctx.story_blueprint['omissions'][0]['source_refs'][0]['source_quote'] == payload()['segments'][0]['text']
    other = payload(); other['segments'][0]['text'] += '原文已变。'
    with pytest.raises(ValueError, match='不属于当前完整原文'):
        normalize_screenplay(script, other, METHODS['leos'])


def test_total_director_duration_is_corrected_before_review_not_silently_scaled(monkeypatch):
    script, good = fixtures()
    long = copy.deepcopy(good)
    for d in long['directions'].values():
        d['shots'][0]['duration_seconds'] = 9
    replies = [{'issues': []}, long, good, {'issues': []}]
    calls = []
    def post(client, endpoints, headers, body):
        calls.append(body)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies.pop(0))}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    data = payload(); data['planning_budget']['episode_max_seconds'] = 9
    ctx = PlannerContext(story_method='leos')
    raw, _ = requests.call_model(base_url='http://invalid/v1', model='fake', payload=data, schema={},
        max_tokens=5000, timeout=5, ctx=ctx, scene_script=script)
    assert '全片计划18秒' in calls[2]['messages'][-1]['content']
    assert sum(s['duration_seconds'] for c in json.loads(raw)['clips'] for s in c['stages']) == 9


def test_unresolved_source_problem_stops_before_directing(monkeypatch):
    finding = {'item_id': 'SC001', 'upstream_id': 'seg_1',
               'issue': '退出状态待核对', 'repair': '核对退出状态'}
    replies = [written(), {'issues': [finding]}, {'replacements': {'SC001': [written()['scenes'][0]]}}, {'issues': [finding]}]
    calls = []
    def post(client, endpoints, headers, body):
        calls.append(body['response_format']['json_schema']['name'])
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies.pop(0))}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method='leos')
    with pytest.raises(requests.IncompleteOutlineError, match='screenplay_review'):
        requests.call_model(base_url='http://invalid/v1', model='fake', payload=payload(), schema={},
                            max_tokens=5000, analysis_tokens=5000, timeout=5, ctx=ctx)
    assert calls == ['scene_screenplay', 'screenplay_review', 'scene_screenplay_revision', 'screenplay_review']
    assert 'scene_screenplay_candidate' in ctx.method_artifacts


def test_short_voice_budget_is_allocated_in_code_without_rewriting_direction():
    script, direction = fixtures()
    direction['directions']['SC001']['shots'][0]['duration_seconds'] = 1
    original = copy.deepcopy(direction)
    shot = project_direction(script, direction)['clips'][0]['stages'][0]
    assert shot['duration_seconds'] > 1
    assert shot['timing_adjustment']['director_seconds'] == 1
    assert direction == original
    assert shot['turns'][0]['text'] == '终于……拿回来了。'


def test_director_gets_the_acceptance_budget_and_only_rewrites_overlong_scene(monkeypatch):
    script, direction = fixtures()
    unit = script['scenes'][1]['units'][0]
    # The real failure grouped 22 + 20 + 22 characters into a supposed 12s shot.
    unit['turns'] = [{**script['scenes'][0]['units'][0]['turns'][0],
                      'turn_id': f'SC002-U01-T{i:02d}', 'text': '问' * count}
                     for i, count in enumerate([22, 20, 22], 1)]
    direction = directed(script)
    budget = direction_budget(script)
    groups = budget['scenes']['SC002']['suggested_groups']
    assert [g['minimum_seconds'] for g in groups] == [14, 8]
    corrected = {'method_plan': direction['method_plan'], 'directions': {'SC002': copy.deepcopy(direction['directions']['SC002'])}}
    shot = corrected['directions']['SC002']['shots'][0]
    corrected['directions']['SC002']['shots'] = [{**shot, 'turn_ids': g['turn_ids'], 'duration_seconds': g['minimum_seconds']} for g in groups]
    replies = [{'issues': []}, direction, corrected, {'issues': []}]
    calls = []
    def post(client, endpoints, headers, body):
        calls.append(copy.deepcopy(body))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies.pop(0))}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method='leos')
    requests.call_model(base_url='http://invalid/v1', model='fake', payload=payload(), schema={},
                        max_tokens=5000, timeout=5, ctx=ctx, scene_script=script)
    first_input = json.loads(calls[1]['messages'][1]['content'])
    assert first_input['dialogue_budget'] == budget
    patch_schema = calls[2]['response_format']['json_schema']['schema']['properties']['directions']
    assert patch_schema['required'] == ['SC002']
    assert ctx.story_blueprint['direction']['directions']['SC001'] == direction['directions']['SC001']
    accepted = project_direction(ctx.story_blueprint, ctx.story_blueprint['direction'])
    assert [s['duration_seconds'] for s in accepted['clips'][1]['stages']] == [14, 8]


def test_screenplay_revision_splits_affected_scene_without_rewriting_neighbors(monkeypatch):
    data = written()
    data['scenes'][1]['time'] = '夜晚至次晨'
    later = data['scenes'][1]['units'][0]
    data['scenes'][1]['units'].insert(0, {**copy.deepcopy(later), 'action': '梁舟在溪边放下木棍，入睡。'})
    script = normalize_screenplay(data, payload(), METHODS['leos'])
    night, morning = copy.deepcopy(data['scenes'][1]), copy.deepcopy(data['scenes'][1])
    night.update(time='夜晚', units=[night['units'][0]], exit_state='梁舟入睡')
    morning.update(time='次晨', transition='time_jump', units=[morning['units'][1]])
    revision = {'replacements': {'SC002': [night, morning]}}
    expected = apply_scene_revision(script, revision, {'SC002'}, payload(), METHODS['leos'])
    finding = {'item_id': 'SC002', 'upstream_id': 'seg_2', 'issue': '跨夜需要拆场', 'repair': '拆场'}
    replies = [data, {'issues': [finding]}, revision, {'issues': []}, directed(expected), {'issues': []}]
    calls = []
    def post(client, endpoints, headers, body):
        calls.append(copy.deepcopy(body))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(replies.pop(0))}}]}
    monkeypatch.setattr(requests, 'post_any', post)
    ctx = PlannerContext(story_method='leos')
    requests.call_model(base_url='http://invalid/v1', model='fake', payload=payload(), schema={},
                        max_tokens=5000, timeout=5, ctx=ctx)
    patch_schema = calls[2]['response_format']['json_schema']['schema']['properties']['replacements']
    assert patch_schema['required'] == ['SC002']
    assert ctx.story_blueprint['scenes'][0] == script['scenes'][0]
    assert [s['time'] for s in ctx.story_blueprint['scenes'][1:]] == ['夜晚', '次晨']
    with pytest.raises(ValueError, match='已点名'):
        apply_scene_revision(script, {'replacements': {'SC001': [night]}}, {'SC002'}, payload(), METHODS['leos'])
