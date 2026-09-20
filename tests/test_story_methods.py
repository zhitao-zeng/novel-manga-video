"""Creative methods must reach the screenplay and its compiled request, not just a label."""
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from novel_manga.planning.context import PlannerContext
from novel_manga.planning.methods import METHODS, get_method
from novel_manga.planning.methods.blueprint import blueprint_schema, validate_blueprint, validate_application, normalize_blueprint, quote_candidates
from novel_manga.planning.methods.prompts import screenplay_prompt
from novel_manga.planning.contracts import build_schema
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.planning.issues import PlanningCode
from novel_manga.models.bible import StoryBible
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options, load_context, context_for_plan
from novel_manga.application.planning import requests, cli

SEGMENTS = [
    {'segment_id': 'seg_1', 'text': '梁舟踩稳岸边石头，双手握住背带，把浸水的背包拖上岸。'},
    {'segment_id': 'seg_2', 'text': '灰色山羊从草丛冲向梁舟，他横举木棍，受撞后向后退了一步。'},
]


def blueprint(method, segments=SEGMENTS):
    return {'method_id': method.key,
            'episode_contract': {k: v for k, v in zip(('goal', 'obstacle', 'outcome', 'exit_state'),
                ('取回物品继续前进', '水和突然冲来的动物', '背包上岸，人挡住冲撞', '背包在岸上，人物退后站稳'))},
            'method_plan': {k: '以原文行为及结果为依据进行具体设计' for k, _ in method.episode_fields},
            'beats': [{'beat_id': f'beat_{i}', 'segment_id': s['segment_id'], 'source_quote': s['text'],
                       'scene': '溪岸白天', 'purpose': '完成当前动作并交代结果', 'start_state': '人物立在溪岸',
                       'action': s['text'], 'end_state': '人物完成这一动作',
                       'continuity_from': f'beat_{i-1}' if i > 1 else '',
                       'transition': 'continuous' if i > 1 else 'opening',
                       'method_details': {k: '基于这一拍的具体身体和物件变化' for k, _ in method.beat_fields}}
                      for i, s in enumerate(segments, 1)]}


def bible_data():
    return {'novel_title': '回归', 'genre': 'generic', 'visual_style': '3D动画', 'palette': '自然色',
            'style_fingerprint': 'methods-fixture', 'characters': [
                {'name': '梁舟', 'role': '主角', 'appearance': '黑发青年', 'wardrobe': '深色外衣'}],
            'locations': ['溪岸：白天的石质河岸']}


def model_blueprint(flat):
    data=copy.deepcopy(flat);beats=data.pop('beats');groups={}
    for beat in beats:
        sid=beat.pop('segment_id');beat.pop('beat_id');beat.pop('continuity_from')
        groups.setdefault(sid,[]).append(beat)
    data['beats_by_segment']=groups
    return data


def raw_script(segments=SEGMENTS):
    stages = []
    for i, s in enumerate(segments, 1):
        stages.append({'beat_id': f'beat_{i}', 'segment_id': s['segment_id'], 'source_quote': s['text'],
                       'start_state': '梁舟站在溪岸', 'event': s['text'], 'end_state': '梁舟完成动作站稳',
                       'camera': '溪岸侧面平视，随梁舟后退缓慢后移', 'light': '日光从右上方照入',
                       'sfx': '水声和脚步', 'shot_scale': '中景', 'in_frame': ['梁舟'],
                       'actions': [{'actor': '灰色山羊', 'action': '冲撞', 'target': '梁舟'}] if i == 2 else [
                           {'actor': '梁舟', 'action': '拖上岸', 'target': '背包'}],
                       'extras': ['灰色山羊'] if i == 2 else [],
                       'turns': [{'speaker_name': '', 'delivery_mode': 'silent_action', 'text': '完成动作', 'emotion': '平静', 'chat_target': ''}]})
    return {'video_title': '溪岸', 'hook': '物品还在水中', 'summary': '取回背包并挡住山羊',
            'clips': [{'clip_id': 'clip_1', 'location': '溪岸', 'characters': ['梁舟'], 'avoid': '', 'stages': stages}],
            'skipped_segments': []}


def test_source_buckets_reserve_the_second_half_and_assign_ids_without_mutation():
    method=METHODS['leos'];flat=blueprint(method);wire=model_blueprint(flat);before=copy.deepcopy(wire)
    assert normalize_blueprint(wire,SEGMENTS)==flat and wire==before
    schema=blueprint_schema(method,SEGMENTS)['properties']['beats_by_segment']
    assert schema['required']==['seg_1','seg_2']
    assert schema['properties']['seg_1']['maxItems']==2
    for segment in SEGMENTS:
        candidates=schema['properties'][segment['segment_id']]['items']['properties']['source_quote']['enum']
        assert all(q in segment['text'] for q in candidates)
    assert 'enum' not in schema['properties']['seg_1']['items']['properties']['action']
    del wire['beats_by_segment']['seg_2']
    with pytest.raises(ValueError,match='every source segment'):
        normalize_blueprint(wire,SEGMENTS)


def test_quote_candidates_are_literal_bounded_and_keep_short_original_dialogue():
    text='梁舟说：“到了。”\n'+('山羊踩过浅水，梁舟后退站稳。'*30)
    candidates=quote_candidates(text)
    assert all(q in text and len(q)<=120 for q in candidates)
    assert '梁舟说：“到了。' in candidates or '梁舟说：“到了。”' in candidates
    assert quote_candidates('到了。')==['到了。']


def test_retry_repairs_complete_method_draft_without_forwarding_it_as_accepted(monkeypatch):
    method=METHODS['community'];good=blueprint(method);bad=copy.deepcopy(good)
    bad['beats'][1]['source_quote']='原文没有的营救事件'
    calls=[]
    def answer(client,urls,headers,body):
        calls.append(body)
        content=model_blueprint(bad if len(calls)==1 else good) if len(calls)<3 else raw_script()
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content,ensure_ascii=False)}}]}
    monkeypatch.setattr(requests,'post_any',answer)
    ctx=PlannerContext(story_method=method.key)
    import httpx
    with httpx.Client(trust_env=False) as client:
        content, _ = requests.generate_outline(client, ['http://model.invalid/v1'], {},
            model='test', payload={'segments':SEGMENTS}, mode='coverage', max_tokens=4000, timeout=5, method=method)
    assert len(calls)==2 and '待修稿' in calls[1]['messages'][-1]['content']
    assert json.loads(content)==good


def test_missing_or_forged_source_and_broken_continuity_never_reach_second_pass(monkeypatch):
    method = METHODS['leos'];bad = blueprint(method)
    bad['beats'][1]['source_quote'] = '原文里不存在的突然营救'
    bad['beats'][1]['continuity_from'] = ''
    errors = validate_blueprint(json.dumps(bad), method, SEGMENTS)
    assert any('source_quote' in e for e in errors) and any('preceding' in e for e in errors)
    bad['beats'].pop()
    assert any('missing source segments' in e for e in validate_blueprint(json.dumps(bad), method, SEGMENTS))
    calls = []
    def incomplete(client, urls, headers, body):
        calls.append(body)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(model_blueprint(bad))}}]}
    monkeypatch.setattr(requests, 'post_any', incomplete)
    ctx = PlannerContext(story_method='leos')
    with pytest.raises(requests.IncompleteOutlineError):
        import httpx
        with httpx.Client(trust_env=False) as client:
            requests.generate_outline(client, ['http://model.invalid/v1'], {}, model='test',
                payload={'segments': SEGMENTS}, mode='coverage', max_tokens=4000, timeout=5, method=method)
    assert len(calls) == 2
    assert all(c['response_format']['json_schema']['name'] == 'chapter_outline_leos' for c in calls)


def test_final_script_cannot_drop_a_beat_even_when_segment_coverage_passes():
    method = METHODS['drama'];bp = blueprint(method)
    extra = copy.deepcopy(bp['beats'][-1]);extra.update(beat_id='beat_3', continuity_from='beat_2')
    bp['beats'].append(extra)
    ctx = PlannerContext(story_method='drama', story_blueprint=bp)
    b = StoryBible.model_validate(bible_data())
    result = validate_and_normalize(raw_script(), SEGMENTS, b, {'溪岸': b.locations[0]},
                                   ''.join(s['text'] for s in SEGMENTS), ctx=ctx)
    assert any(i.code == PlanningCode.METHOD_CONTRACT and 'beat_3' in i.detail for i in result.issues)
    assert [s['beat_id'] for s in result.shots] == ['beat_1', 'beat_2']
    assert result.shots[1]['actions'][0]['actor'] == '灰色山羊'
    assert result.shots[0]['actions'][0]['target'] == '背包'


def test_short_quoted_lines_and_named_speech_are_not_rejected_as_chat():
    method=METHODS['shanyin']
    segments=[{'segment_id':'seg_1','text':'梁舟说：“到了。”\n梁舟把背包放到岸边。'}]
    bp=blueprint(method,segments);bp['beats'][0]['source_quote']='梁舟说：“到了。”'
    assert not validate_blueprint(json.dumps(bp),method,segments)
    bp['beats'][0]['source_quote']='到了。'
    assert not validate_blueprint(json.dumps(bp),method,segments)
    from novel_manga.planning.source_checks import chapter_coverage
    ctx=PlannerContext();issues=[]
    prose='\n'.join(['梁舟说：“到了。”']*6)
    chapter_coverage({},[],[{'segment_id':'seg_1','text':prose}],{'seg_1':[1]},prose,ctx,issues,[],known_speakers=['梁舟'])
    assert not any(i.code==PlanningCode.MISSING_CHAT for i in issues)
    chat='\n'.join(['网友甲：到了','网友乙：收到']*3)
    chapter_coverage({},[],[{'segment_id':'seg_1','text':chat}],{'seg_1':[1]},chat,ctx,issues,[],known_speakers=['网友甲','网友乙'])
    assert any(i.code==PlanningCode.MISSING_CHAT for i in issues)


def test_quote_reassignment_cannot_silently_change_a_methods_source_owner():
    method=METHODS['drama'];ctx=PlannerContext(story_method='drama',story_blueprint=blueprint(method))
    raw=raw_script();raw['clips'][0]['stages'][1]['source_quote']=SEGMENTS[0]['text']
    b=StoryBible.model_validate(bible_data())
    result=validate_and_normalize(raw,SEGMENTS,b,{'溪岸':b.locations[0]},''.join(s['text'] for s in SEGMENTS),ctx=ctx)
    assert any(i.code==PlanningCode.METHOD_CONTRACT and 'ownership changed' in i.detail for i in result.issues)


def test_patch_contract_and_replacement_keep_the_method_beat(monkeypatch):
    method=METHODS['leos'];ctx=PlannerContext(story_method='leos',story_blueprint=blueprint(method))
    raw=raw_script();before=copy.deepcopy(raw)
    def patch(parts,schema,**kw):
        stage=schema['properties']['replacements']['items']['properties']['stage']
        assert stage['properties']['beat_id']['enum']==['beat_1','beat_2']
        fixed=copy.deepcopy(raw['clips'][0]['stages'][1]);fixed['event']='灰色山羊撞上木棍，梁舟后退站稳'
        return {'insertions':[],'replacements':[{'label':'clip_1 stage 2','stage':fixed}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',patch)
    result=requests.patch_plan(raw,[],{'clip_1 stage 2':['接触过程不清']},SEGMENTS,['梁舟'],['溪岸'],ctx=ctx)
    assert raw==before and result['clips'][0]['stages'][1]['beat_id']=='beat_2'


def test_compilation_obeys_authored_camera_and_default_requests_stay_fixed():
    b=StoryBible.model_validate(bible_data());ctx=PlannerContext(story_method='leos',story_blueprint=blueprint(METHODS['leos']))
    validated=validate_and_normalize(raw_script(),SEGMENTS,b,{'溪岸':b.locations[0]},''.join(s['text'] for s in SEGMENTS),ctx=ctx)
    clip={'request_seconds':10,'shots':validated.shots}
    fixed=compiler_options(environ={})
    authored=replace(fixed,camera_policy='authored')
    normal=ClipCompiler(fixed).compile_prompt(clip,b,['梁舟'],[],'溪岸')
    creative=ClipCompiler(authored).compile_prompt(clip,b,['梁舟'],[],'溪岸')
    assert '阶段内机位固定不运镜' in normal and '固定道具位置' in normal
    assert '阶段内机位固定不运镜' not in creative and '固定道具位置' not in creative
    assert '随梁舟后退缓慢后移' in creative


def test_profile_selection_cli_override_context_reset_and_frozen_packing(tmp_path,monkeypatch):
    source=tmp_path/'source.txt';source.write_text('第一章 溪岸\n'+'\n'.join(s['text'] for s in SEGMENTS)*8)
    bible=tmp_path/'bible.json';bible.write_text(json.dumps(bible_data(),ensure_ascii=False))
    root=tmp_path/'out';book=root/'demo';book.mkdir(parents=True)
    (book/'profile.json').write_text('{"story_method":"leos","style":"3d","frame":"16:9"}')
    common=['plan_chapter_thin.py',str(source),'--novel-id','demo','--bible',str(bible),'--output-root',str(root),'--dry-run']
    ctx=PlannerContext()
    monkeypatch.setattr(sys,'argv',common);assert cli.main(context=ctx)==0
    assert ctx.story_method=='leos' and ctx.spoken_range[0]==0
    request=json.loads((book/'demo_1/method_request_dry_run.json').read_text())
    assert request['method']['id']=='leos' and request['payload']['production_profile']['frame']=='16:9'
    monkeypatch.setattr(sys,'argv',common+['--story-method','visual']);assert cli.main(context=ctx)==0
    assert ctx.story_method=='visual'
    monkeypatch.setattr(sys,'argv',common+['--story-method','default']);assert cli.main(context=ctx)==0
    assert ctx.story_method=='' and ctx.story_blueprint=={} and ctx.spoken_range[0]>0
    episode=book/'demo_1'
    (book/'profile.json').write_text('{"story_method":"leos","style":"2d","frame":"9:16"}')
    (episode/'chapter_script.json').write_text(json.dumps({'story_method':METHODS['visual'].describe(),
        'profile':{'style':'3d','frame':'16:9'},'shots':[]}))
    frozen=load_context(episode,bible)
    assert frozen['compiler_options'].camera_policy=='authored' and frozen['profile']['story_method']=='visual'
    assert frozen['profile']['frame']=='16:9' and frozen['profile']['style']=='3d'
    assert load_context(episode,bible,frame='9:16')['profile']['frame']=='9:16'
    plan={'limits':{'max_clip_seconds':15,'camera_policy':'authored'},'totals':{'profile':{'frame':'16:9','style':'3d'}}}
    (episode/'chapter_script.json').write_text('{"shots":[]}')
    assert context_for_plan(episode,bible,plan)['compiler_options'].camera_policy=='authored'
    assert load_context(episode,bible)['compiler_options'].camera_policy=='fixed'


def test_list_methods_needs_no_source_or_models(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['plan_chapter_thin.py','--list-methods'])
    assert cli.main()==0
    assert {r['id'] for r in json.loads(capsys.readouterr().out)}==set(METHODS)


def test_method_replay_publishes_native_artifacts_and_retires_old_failure(tmp_path,monkeypatch):
    source=tmp_path/'source.txt';source.write_text('第一章 溪岸\n'+'\n'.join(s['text'] for s in SEGMENTS))
    bible=tmp_path/'bible.json';bible.write_text(json.dumps(bible_data(),ensure_ascii=False))
    args=['plan_chapter_thin.py',str(source),'--novel-id','demo','--bible',str(bible),
          '--output-root',str(tmp_path/'out'),'--story-method','leos','--max-redo','0']
    monkeypatch.setattr(sys,'argv',[*args,'--dry-run']);assert cli.main()==0
    directory=tmp_path/'out/demo/demo_1'
    segments=json.loads((directory/'request_dry_run.json').read_text())['segments']
    model=tmp_path/'model';model.mkdir()
    (model/'analysis_attempt_01.txt').write_text(json.dumps(blueprint(METHODS['leos'],segments),ensure_ascii=False))
    raw=model/'response_attempt_01.raw.json';raw.write_text(json.dumps(raw_script(segments),ensure_ascii=False))
    (directory/'planning_failed.json').write_text('{"errors":["previous attempt"]}')
    monkeypatch.setattr(sys,'argv',[*args,'--replay',str(raw)]);assert cli.main()==0
    result=json.loads((directory/'chapter_script.json').read_text())
    assert result['story_method']['id']=='leos' and all(s['beat_id'] for s in result['shots'])
    assert not (directory/'planning_failed.json').exists()
    report=json.loads((directory/'chapter_script_report.json').read_text())
    assert report['outline_mode']=='method:leos'
    assert report['story_method_checks']['semantic_quality_review']=='not_run'
