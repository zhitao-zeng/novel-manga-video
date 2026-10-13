from novel_manga.media.context import RenderContext
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novel_manga.application.preparation import request_check


def test_scope_recheck_rejects_pose_mislabeled_as_stable_appearance(monkeypatch):
    finding = {'basis': 'request', 'aspect': 'appearance', 'picture': 0, 'lines': [1, 2],
               'subject_specific': True, 'relation': 'contradiction',
               'reason': '参考图站着，请求坐着；身份定义没有坐姿'}
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k: {
        'checks': [{'id': 0, 'scope': 'out_of_scope', 'reason': '角色参考不约束镜头坐站动作'}]})
    lines = [('英文', 'Identity follows Picture 1.'), ('英文', 'The subject sits at the table.')]
    answer = request_check.recheck_scope({'findings': [finding]}, lines, [{'picture': 1, 'role': 'character'}])
    result = request_check.interpret(answer, lines, 1)
    assert result['consistent'] and result['out_of_scope']
    assert finding['subject_specific'] is True


@pytest.mark.parametrize('scope', ['in_scope', 'uncertain'])
def test_scope_recheck_preserves_real_or_uncertain_input_conflicts(monkeypatch, scope):
    finding = {'basis': 'reference', 'aspect': 'appearance', 'picture': 1, 'lines': [1],
               'subject_specific': True, 'relation': 'contradiction', 'reason': '室内宿舍绑定室外街道'}
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k: {
        'checks': [{'id': 0, 'scope': scope, 'reason': '不是姿势问题，空间类型相反'}]})
    answer = request_check.recheck_scope({'findings': [finding]}, [('英文', 'Interior dormitory.')],
                                       [{'picture': 1, 'role': 'location'}])
    if scope == 'uncertain':
        with pytest.raises(request_check.RequestCheckIncomplete):
            request_check.interpret(answer, [('英文', 'Interior dormitory.')], 1)
    else:
        assert request_check.interpret(answer, [('英文', 'Interior dormitory.')], 1)['consistent'] is False


def fixture(tmp_path):
    Image.new('RGB', (64, 64), 'white').save(tmp_path / 'current.jpeg')
    Image.new('RGB', (64, 64), 'gray').save(tmp_path / 'closed.jpeg')
    Image.new('RGB', (64, 64), 'black').save(tmp_path / 'empty-suit.jpeg')
    clip = {'clip_id': 'c1', 'prompt': '席勒穿深色便装', 'prompt_h3': 'Schiller wears dark casual clothes.',
            'references': [{'role': 'character', 'name': '席勒', 'path': 'current.jpeg'},
                           {'role': 'character', 'name': '托尼', 'path': 'closed.jpeg', 'view': 'closed'},
                           {'role': 'prop', 'name': '独立空甲', 'path': 'empty-suit.jpeg'},
                           {'role': 'voice', 'name': '席勒', 'path': 'voice.wav'}]}
    ctx = RenderContext(novel_dir=tmp_path, work=tmp_path / 'work')
    request = {'prompt': clip['prompt_h3'], 'references': [str(tmp_path / r['path']) for r in clip['references'][:-1]],
               'reference_sha256': ['actual-card-1', 'actual-card-2', 'actual-card-3'], 'seed_variant': 0}
    return ctx, clip, request


def test_prepared_renderer_never_calls_a_model_on_a_missing_or_changed_cache(tmp_path,monkeypatch):
    ctx,clip,request=fixture(tmp_path)
    ctx.options.prepared_requests_only=True
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:pytest.fail('model called by submission'))
    with pytest.raises(ValueError,match='prepared request is stale'):
        request_check.before_generation(ctx,clip,request)
    from novel_manga.util import atomic_write_json
    ctx.options.prepared_requests_only=False
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:{'consistent':True,'problems':[]})
    request_check.before_generation(ctx,clip,request)
    ctx.options.prepared_requests_only=True
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:pytest.fail('model called by submission'))
    assert request_check.before_generation(ctx,clip,request)['consistent']
    request['reference_sha256'][0]='changed image'
    with pytest.raises(ValueError,match='prepared request is stale'):
        request_check.before_generation(ctx,clip,request)


def test_request_check_receives_exact_cards_in_request_order_with_binding_legend(tmp_path, monkeypatch):
    ctx, clip, request = fixture(tmp_path)
    seen = []
    monkeypatch.setattr(request_check, 'image_part', lambda p, size: seen.append(Path(p)) or {'type': 'image_url', 'image_url': {'url': str(p)}})
    def ask(parts, schema, **kw):
        if kw['name'] == 'request_conflict_scope':
            return {'checks': [{'id': 0, 'scope': 'in_scope', 'reason': '明确服装冲突仍须阻塞'}]}
        assert len(parts) == 4 and '独立空甲' in parts[-1]['text'] and 'closed' in parts[-1]['text']
        assert 'dark casual clothes' in parts[-1]['text'] and '明确穿戴关系' in parts[-1]['text']
        assert '没有列出白衬衫、马甲不表示脱掉或替换' in parts[-1]['text']
        assert schema['properties']['findings']['items']['properties']['relation']['enum'] == [
            'contradiction', 'underspecified', 'compatible']
        return {'observations': ['图1白衬衫灰马甲'], 'findings': [{'basis': 'reference', 'aspect': 'appearance',
                'picture': 1, 'lines': [1], 'subject_specific': True, 'relation': 'contradiction', 'reason': '请求深色便装与图1衣着矛盾'}]}
    monkeypatch.setattr(request_check, 'ask_json', ask)
    with pytest.raises(ValueError, match='reference mismatch'):
        request_check.before_generation(ctx, clip, request)
    assert seen == [Path(p) for p in request['references']]
    assert ctx.state.blocked_clips['c1'][0].startswith('request: 请求深色便装与图1衣着矛盾')
    assert not (tmp_path / 'repair_history/history.json').exists()


def test_reference_gate_does_not_follow_translation_only_override(tmp_path, monkeypatch):
    ctx, clip, request = fixture(tmp_path)
    monkeypatch.setenv('QWEN38_LOCAL_BASE_URL', 'http://172.28.4.52:8831/v1')
    monkeypatch.setenv('QWEN38_LOCAL_MODEL', 'DeepSeek-V4.1-Flash')
    monkeypatch.setenv('NOVEL_H3_TRANSLATION_ENDPOINT', 'flashnext')
    monkeypatch.delenv('NOVEL_H3_REQUEST_CHECK_ENDPOINT', raising=False)
    calls=[]
    def ask(*args, **kwargs):
        calls.append(kwargs['settings'].model)
        return {'observations': [], 'findings': []}
    monkeypatch.setattr(request_check, 'ask_json', ask)
    assert request_check.before_generation(ctx, clip, request)['consistent']
    assert calls == ['DeepSeek-V4.1-Flash']


def test_seed_only_retry_reuses_check_but_image_or_word_changes_recheck(tmp_path, monkeypatch):
    ctx, clip, request = fixture(tmp_path)
    calls = []
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k: calls.append(1) or
                        {'observations': ['一致'], 'findings': []})
    request_check.before_generation(ctx, clip, request)
    request['seed_variant'] = 1
    request_check.before_generation(ctx, clip, request)
    assert len(calls) == 1
    request['reference_sha256'][0] = 'new-image-at-same-path'
    request_check.before_generation(ctx, clip, request)
    assert len(calls) == 2
    request['prompt'] += ' The faceplate is closed.'
    request_check.before_generation(ctx, clip, request)
    assert len(calls) == 3


def test_missing_reference_fails_before_any_model_or_video_request(tmp_path, monkeypatch):
    ctx, clip, request = fixture(tmp_path)
    (tmp_path / 'current.jpeg').unlink()
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k: pytest.fail('missing image cannot be checked'))
    with pytest.raises(FileNotFoundError):
        request_check.before_generation(ctx, clip, request)


def test_explicit_wearing_and_empty_suit_are_allowed_without_changing_request(tmp_path, monkeypatch):
    ctx, clip, request = fixture(tmp_path)
    clip['prompt'] = '托尼穿甲，面罩合拢；一套无人空甲随后飞入。'
    request['prompt'] = clip['prompt_h3'] = 'Tony wears the armour and closes the visor. A separate empty suit arrives.'
    before = copy.deepcopy((clip, request))
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k:
                        {'observations': ['穿戴与独立空甲用途清楚'], 'findings': []})
    assert request_check.before_generation(ctx, clip, request)['consistent']
    assert (clip, request) == before


@pytest.mark.parametrize('basis,aspect,specific', [('reference', 'state', True), ('reference', 'action', True),
                                                ('request', 'camera', True), ('request', 'style', True),
                                                ('reference', 'appearance', False)])
def test_card_pose_generic_material_and_camera_preferences_cannot_rewrite_the_scene(basis, aspect, specific):
    finding = {'basis': basis, 'aspect': aspect, 'picture': 1, 'lines': [1], 'subject_specific': specific,
               'relation': 'contradiction', 'reason': '卡面插兜而本镜摸下巴，或通用风格中的fabric被当成机甲改成布'}
    result = request_check.interpret({'observations': [], 'findings': [finding]}, [('中文', '人物摸下巴')], 1)
    assert result['consistent'] is True and not result['problems'] and result['out_of_scope'] == [finding]


def test_textual_closed_helmet_and_visible_eyes_conflict_is_still_checked():
    finding = {'basis': 'request', 'aspect': 'visibility', 'picture': 0, 'lines': [1, 2],
               'subject_specific': True, 'relation': 'contradiction', 'reason': '全程闭合头盔同时要求清晰的眼神表演'}
    result = request_check.interpret({'findings': [finding]}, [('中文', '全程闭合'), ('英文', 'eyes blink')], 1)
    assert not result['consistent'] and result['problems']


@pytest.mark.parametrize('role,consistent', [('location', False), ('character', True)])
def test_reference_space_is_binding_only_for_an_actual_location_card(role, consistent):
    finding = {'basis': 'reference', 'aspect': 'space', 'picture': 1, 'lines': [1],
               'subject_specific': True, 'relation': 'contradiction',
               'reason': '参考图是室外街道，本镜要求同一场景为室内大厅'}
    result = request_check.interpret({'findings': [finding]}, [('英文', 'An interior hall.')], 1,
                                     references=[{'picture': 1, 'role': role}])
    assert result['consistent'] is consistent


@pytest.mark.parametrize('relation', ['underspecified', 'compatible'])
def test_generic_attire_or_omitted_layers_do_not_block_generation(tmp_path, monkeypatch, relation):
    ctx, clip, request = fixture(tmp_path)
    clip['prompt'] = '席勒穿便装，保留角色卡服装。'
    request['prompt'] = 'Schiller wears casual clothes, retaining the garment design in Picture 1.'
    original = copy.deepcopy((clip, request))
    finding = {'basis': 'reference', 'aspect': 'appearance', 'picture': 1, 'lines': [1],
               'subject_specific': True, 'relation': relation,
               'reason': '图1有白衬衫灰马甲；便装未列出每层衣物，但没有要求替换参考服装'}
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k:
                        {'observations': ['图1白衬衫灰马甲'], 'findings': [finding]})
    result = request_check.before_generation(ctx, clip, request)
    assert result['consistent'] and not result['problems'] and not ctx.state.blocked_clips
    assert result['findings'] == [finding] and (clip, request) == original


@pytest.mark.parametrize('aspect,reason', [
    ('appearance', '图1为白衬衫灰马甲，请求明确要求黑色西装外套和领带'),
    ('wearing', '同一人物同时被要求穿着机甲且不穿任何机甲'),
    ('action', '同一时刻同一空甲被要求已经落地且正在首次飞入'),
    ('visibility', '同一人物被要求全程画外且清晰出现在前景'),
])
def test_explicit_incompatible_facts_remain_blocking(aspect, reason):
    finding = {'basis': 'reference' if aspect == 'appearance' else 'request', 'aspect': aspect,
               'picture': 1 if aspect == 'appearance' else 0, 'lines': [1, 2],
               'subject_specific': True, 'relation': 'contradiction', 'reason': reason}
    result = request_check.interpret({'findings': [finding]}, [('中文', '当前要求'), ('英文', 'current request')], 1)
    assert not result['consistent'] and reason in result['problems'][0]


def test_unclassified_old_boolean_answer_cannot_silently_pass():
    finding = {'basis': 'reference', 'aspect': 'appearance', 'picture': 1, 'lines': [1],
               'subject_specific': True, 'conflict': True, 'reason': '旧回答未区分矛盾与省略'}
    with pytest.raises(ValueError, match='did not classify'):
        request_check.interpret({'findings': [finding]}, [('中文', '便装')], 1)


@pytest.mark.parametrize('old_policy', ['request-reference-v1', 'request-reference-v2-scoped-evidence',
                                    'request-reference-v3-scoped-evidence', 'request-reference-v4-current-input-only'])
@pytest.mark.parametrize('old_consistent', [True, False])
def test_policy_change_rechecks_old_rejections_but_reuses_unchanged_passes(tmp_path, monkeypatch,
                                                                         old_policy, old_consistent):
    ctx, clip, request = fixture(tmp_path)
    calls = []
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k: calls.append(1) or
                        {'observations': ['当前输入一致'], 'findings': []})
    request_check.cached_consistency(ctx.novel_dir, ctx.work, clip, request)
    path = ctx.work / 'request_checks' / 'c1.json'
    saved = json.loads(path.read_text())
    saved['inputs']['policy'] = saved['answer']['policy'] = old_policy
    saved['answer']['consistent'] = old_consistent
    saved['answer']['problems'] = [] if old_consistent else ['便装未提马甲']
    path.write_text(json.dumps(saved))
    calls.clear()
    answer = request_check.cached_consistency(ctx.novel_dir, ctx.work, clip, request)
    assert answer['consistent'] and answer['policy'] == request_check.POLICY
    assert len(calls) == (0 if old_consistent else 1)
    if old_consistent:
        assert answer['reused_from'] == old_policy
    assert json.loads(path.read_text())['inputs']['policy'] == request_check.POLICY


def test_compiler_style_is_not_misread_as_the_armoured_characters_clothing(tmp_path, monkeypatch):
    ctx, clip, request = fixture(tmp_path)
    clip['h3_style_line'] = 'Natural skin tones and plain fabric surfaces follow the character references.'
    request['prompt'] = clip['h3_style_line'] + '\nThe character wears silver metal armour.'
    original = copy.deepcopy(request)
    def ask(parts, schema, **kw):
        text = parts[-1]['text'].split('请求行：\n', 1)[-1]
        assert clip['h3_style_line'] not in text and 'silver metal armour' in text
        return {'observations': ['银白金属机甲'], 'findings': []}
    monkeypatch.setattr(request_check, 'ask_json', ask)
    assert request_check.before_generation(ctx, clip, request)['consistent']
    assert request == original


def test_request_and_scope_checks_send_their_schema_in_model_messages(tmp_path, monkeypatch):
    from novel_manga.llm import client
    import json
    _, clip, request = fixture(tmp_path)
    requests = []
    def post(client_arg, endpoints, headers, payload, **kwargs):
        requests.append(payload)
        if len(requests) == 1:
            answer = {'observations': ['current card'], 'findings': [{
                'basis': 'reference', 'aspect': 'appearance', 'picture': 1, 'lines': [1],
                'subject_specific': True, 'relation': 'contradiction', 'reason': 'actual costume differs'}]}
        else:
            answer = {'checks': [{'id': 0, 'scope': 'in_scope', 'reason': 'stable costume conflict'}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}
    monkeypatch.setattr(client, 'post_any', post)
    checked = request_check.request_consistency(clip, tmp_path, request=request)
    assert not checked['consistent'] and checked['problems']
    assert len(requests) == 2
    for payload in requests:
        assert payload['response_format'] == {'type': 'json_object'}
        text = '\n'.join(p.get('text', '') for p in payload['messages'][0]['content'])
        assert 'Return a JSON object matching this schema:' in text
        assert '"type": "integer"' in text


def test_casting_context_reaches_both_checks_and_its_cache_key(tmp_path,monkeypatch):
    ctx,clip,request=fixture(tmp_path)
    clip.update(cast=['甲'],extras=['另一个商人'])
    seen=[]
    def ask(parts,schema,**kwargs):
        text=parts[-1]['text'];seen.append(text)
        assert '另一个商人' in text and '甲' in text
        if kwargs['name']=='request_conflict_scope':
            return {'checks':[{'id':0,'scope':'out_of_scope','reason':'相似体型不能证明是同一个人'}]}
        return {'observations':['当前图片'], 'findings':[{'basis':'request','aspect':'space','picture':0,
            'lines':[1],'subject_specific':True,'relation':'contradiction','reason':'两人都体型相似所以误认成同一人'}]}
    monkeypatch.setattr(request_check,'ask_json',ask)
    result=request_check.cached_consistency(tmp_path,ctx.work,clip,request)
    assert result['consistent'] and len(seen)==2
    assert request_check.cached_consistency(tmp_path,ctx.work,clip,request)==result and len(seen)==2
    clip['extras']=['另一个商人','第二位客人']
    request_check.cached_consistency(tmp_path,ctx.work,clip,request)
    assert len(seen)==4


@pytest.mark.parametrize('scope_check',[{'error':'timeout'}, {'checks':[{'scope':'uncertain'}]}])
def test_unfinished_scope_review_is_not_reported_as_a_confirmed_conflict(scope_check):
    answer={'findings':[{'basis':'request','aspect':'space','picture':0,'lines':[1],
        'subject_specific':True,'relation':'contradiction','reason':'unconfirmed premise'}],
        'scope_check':scope_check}
    with pytest.raises(ValueError,match='scope.*incomplete'):
        request_check.interpret(answer,[('英文','The scene.')],0)


def test_unresolved_scope_keeps_other_confirmed_conflicts():
    finding = {'basis': 'request', 'aspect': 'space', 'picture': 0, 'lines': [1],
               'subject_specific': True, 'relation': 'contradiction'}
    answer = {'findings': [{**finding, 'reason': 'confirmed location conflict'},
                            {**finding, 'reason': 'identity uncertain'}],
              'scope_check': {'checks': [{'id': 0, 'scope': 'in_scope'},
                                          {'id': 1, 'scope': 'uncertain'}]}}
    with pytest.raises(request_check.RequestCheckIncomplete) as caught:
        request_check.interpret(answer, [('英文', 'The scene.')], 0)
    result = caught.value.result
    assert result['complete'] is False and result['consistent'] is False
    assert len(result['problems']) == 1 and 'confirmed location' in result['problems'][0]
    assert [r['reason'] for r in result['unresolved']] == ['identity uncertain']


@pytest.mark.parametrize('raw', [{}, {'findings': None}, {'findings': [{'relation': 'unknown'}]}])
def test_initial_malformed_judgment_is_incomplete_not_a_content_defect(tmp_path, monkeypatch, raw):
    _, clip, request = fixture(tmp_path)
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **k: raw)
    with pytest.raises(request_check.RequestCheckIncomplete) as caught:
        request_check.request_consistency(clip, tmp_path, request=request)
    assert caught.value.result['complete'] is False
    assert caught.value.result['consistent'] is None
    assert caught.value.result['raw_answer'] == raw
