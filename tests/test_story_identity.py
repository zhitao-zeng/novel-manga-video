import json
from pathlib import Path

import pytest

import story_identity as identity
from novel_manga.util import atomic_write_json


def book(tmp_path, names=('甲', '乙')):
    novel = tmp_path / 'book'
    novel.mkdir()
    atomic_write_json(novel / 'story_bible.json', {'characters': [
        {'name': name, 'role': '人物', 'appearance': '艺术设计'} for name in names]})
    return novel


def reading(entities, mentions=(), relations=()):
    return {'policy': identity.POLICY, 'entities': entities, 'mentions': list(mentions), 'relations': list(relations), 'appearances': []}


def test_alias_file_and_index_are_both_used(tmp_path):
    novel = book(tmp_path, ('正式名',))
    atomic_write_json(novel / 'entity_index.json', {'characters': [{'name': '正式名', 'forms': {'索引旧名': 5}}]})
    atomic_write_json(novel / 'bible_aliases.json', {'后来确认的叫法': '正式名'})
    catalog = identity.IdentityCatalog(novel)
    assert catalog.by_name['正式名']['forms'] == {'正式名', '索引旧名', '后来确认的叫法'}


def test_book_switch_clears_previous_aliases(tmp_path):
    import plan_chapter_thin as planner
    a = tmp_path / 'a'; b = tmp_path / 'b'
    a.mkdir(); b.mkdir()
    atomic_write_json(a / 'bible_aliases.json', {'同一称谓': '甲'})
    atomic_write_json(b / 'bible_aliases.json', {'另一个称谓': '乙'})
    planner.load_entity_index(a)
    assert planner.ALIASES['同一称谓'] == '甲'
    planner.load_entity_index(b)
    assert planner.ALIASES == {'另一个称谓': '乙'}


def test_ledger_merge_keeps_one_identity_and_both_names(tmp_path):
    novel = book(tmp_path, ('正式名', '旧名字'))
    atomic_write_json(novel / 'entity/entities.json', [
        {'id': 'e071', 'canonical': '正式名', 'asset_id': 'character_001'},
        {'id': 'e099', 'canonical': '旧名字', 'merged_into': 'e071'}])
    catalog = identity.IdentityCatalog(novel)
    assert catalog.by_name['正式名']['id'] == catalog.by_name['旧名字']['id'] == 'e071'
    assert {'正式名', '旧名字'} <= catalog.by_name['正式名']['forms']


def test_contextual_alias_cannot_merge_an_avatar_or_two_owners(tmp_path):
    novel = book(tmp_path, ('本体', '分身'))
    atomic_write_json(novel / 'bible_aliases.json', {'分身': '本体', '先生': '本体'})
    context = reading({'e001': '本体', 'e002': '分身'}, [
        {'form': '分身', 'entity_id': 'e002', 'presence': 'on_stage'},
        {'form': '先生', 'entity_id': 'e001', 'presence': 'on_stage'},
        {'form': '先生', 'entity_id': 'e002', 'presence': 'on_stage'}],
        [{'type': 'avatar_of', 'subject': 'e002', 'object': 'e001'}])
    aliases = identity.effective_aliases(novel, context=context)
    assert '分身' not in aliases and '先生' not in aliases


def test_renamed_lead_is_shared_by_aliases_and_speaker_rows(tmp_path):
    novel = book(tmp_path, ('正式名', '旧名字'))
    context = reading({'e001': '正式名', 'e002': '旧名字'}, [
        {'form': '旧名字', 'entity_id': 'e002', 'presence': 'on_stage'}],
        [{'type': 'same_as', 'subject': 'e002', 'object': 'e001'}])
    catalog = identity.IdentityCatalog(novel)
    assert identity.effective_aliases(novel, context=context)['旧名字'] == '正式名'
    rows = identity.identity_rows(['正式名', '旧名字'], catalog.bible, '旧名字走过来。', context, catalog)
    assert rows[0]['source_names'] == ['旧名字'] and rows[1]['source_names'] == []


def test_short_word_is_only_a_person_when_source_reading_says_so(tmp_path):
    novel = book(tmp_path, ('零',))
    catalog = identity.IdentityCatalog(novel)
    # Both meanings are retrieved, so no spelling whitelist hides the candidate.
    assert catalog.candidates('温度降到了零。')[0]['name'] == '零'
    nonperson = reading({'e001': '零'}, [{'form': '零', 'entity_id': 'UNKNOWN', 'presence': 'not_entity'}])
    assert identity.identity_rows(['零'], catalog.bible, '温度降到了零。', nonperson, catalog)[0]['source_names'] == []
    person = reading({'e001': '零'}, [{'form': '零', 'entity_id': 'e001', 'presence': 'on_stage'}])
    assert identity.identity_rows(['零'], catalog.bible, '零推门进来。', person, catalog)[0]['source_names'] == ['零']


def test_source_quotes_and_ids_are_checked_before_saving(tmp_path):
    novel = book(tmp_path)
    catalog = identity.IdentityCatalog(novel)
    answer = {'mentions': [{'form': '甲', 'entity_id': 'e001', 'paragraphs': [1]}], 'relations': [], 'appearances': []}
    rows = identity.ground(answer, catalog, [{'text': '甲推门进来。'}])
    assert rows['mentions'][0]['source_quote'] == '甲推门进来。'
    answer['mentions'][0]['form'] = '原文没有的人'
    with pytest.raises(ValueError, match='does not occur'):
        identity.ground(answer, catalog, [{'text': '甲推门进来。'}])
    answer['mentions'][0].update(form='甲', entity_id='e999')
    with pytest.raises(ValueError, match='absent entity'):
        identity.ground(answer, catalog, [{'text': '甲推门进来。'}])


def test_one_source_reading_is_reused_and_source_edits_invalidate_it(tmp_path, monkeypatch):
    from novel_manga import model_client
    novel = book(tmp_path)
    directory = novel / 'book_1'
    atomic_write_json(directory / 'segments.json', [{'segment_id': 'seg_1', 'text': '甲推门进来。'}])
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        assert '艺术设计' not in str(args)
        return {'actors': [{'source_id':1,'name':'甲','forms':[{'form':'甲','kind':'proper','paragraphs':[1]}],
                           'presence':'on_stage','paragraphs':[1],'kind':'individual','appearance':''}], 'relations': []}
    monkeypatch.setattr(model_client, 'ask_json', ask)
    first = identity.resolve_chapter(directory)
    assert identity.resolve_chapter(directory) == first and calls == ['chapter_identity_source']
    atomic_write_json(directory / 'segments.json', [{'segment_id': 'seg_1', 'text': '甲转身离开。'}])
    assert identity.current_context(directory) == {}
    identity.resolve_chapter(directory)
    assert calls == ['chapter_identity_source', 'chapter_identity_source']


def test_source_name_beats_a_conflicting_old_alias(tmp_path):
    novel = book(tmp_path, ('寒暴', '寒'))
    atomic_write_json(novel/'entity_index.json', {'characters':[{'name':'寒暴','forms':{'寒痕':20}}]})
    catalog = identity.IdentityCatalog(novel)
    actor={'source_id':1,'name':'寒','forms':[{'form':'寒','kind':'proper','paragraphs':[1]},
          {'form':'寒痕之龙','kind':'proper','paragraphs':[1]}],'presence':'on_stage','kind':'individual','appearance':'','paragraphs':[1]}
    r=identity.map_source_reading({'actors':[actor],'relations':[]},catalog,[{'text':'寒痕之龙，寒！'}])
    assert {m['entity_id'] for m in r['mentions']} == {'e002'}


def test_two_source_actors_do_not_collapse_through_a_legacy_alias(tmp_path):
    novel=book(tmp_path,('本体',))
    atomic_write_json(novel/'bible_aliases.json',{'化身':'本体'})
    actors=[{'source_id':i,'name':name,'forms':[{'form':name,'kind':'proper','paragraphs':[1]}],
             'kind':'individual','presence':'on_stage','appearance':'','paragraphs':[1]}
            for i,name in [(1,'本体'),(2,'化身')]]
    r=identity.map_source_reading({'actors':actors},identity.IdentityCatalog(novel),[{'text':'本体站在化身对面。'}])
    assert [(m['form'],m['entity_id']) for m in r['mentions']]==[('本体','e001'),('化身','UNKNOWN')]
    assert r['unmatched_actors'][0]['name']=='化身'


def test_catalog_update_rematches_source_actors_without_another_model_read(tmp_path,monkeypatch):
    from novel_manga import model_client
    novel=book(tmp_path,('甲',));directory=novel/'book_1'
    atomic_write_json(directory/'segments.json',[{'segment_id':'seg_1','text':'甲走进房间。'}])
    calls=[]
    def ask(*a,**k):
        calls.append(1)
        return {'actors':[{'source_id':1,'name':'甲','forms':[{'form':'甲','kind':'proper','paragraphs':[1]}],
                           'kind':'individual','presence':'on_stage','appearance':'','paragraphs':[1]}]}
    monkeypatch.setattr(model_client,'ask_json',ask)
    identity.resolve_chapter(directory)
    atomic_write_json(novel/'bible_aliases.json',{'某称呼':'甲'})
    identity.resolve_chapter(directory)
    assert calls==[1]


def test_design_body_is_not_exposed_as_source_truth(tmp_path):
    novel = book(tmp_path, ('某角色',))
    directory = novel / 'book_1'
    atomic_write_json(directory / 'segments.json', [{'segment_id': 'seg_1', 'text': '某角色盘旋，鳞片泛光。'}])
    ctx = identity.prompt_context(directory)
    assert 'design_reference_only' in ctx['candidates_not_source_facts'][0]
    assert ctx['chapter_reading']['appearances'] == []


def test_planner_and_packer_share_the_same_scoped_identity(tmp_path):
    import plan_chapter_thin as planner
    import build_clip_plan_thin as packer
    novel=book(tmp_path,('正式名', '旧名字'))
    directory=novel/'book_1'
    atomic_write_json(directory/'segments.json',[{'segment_id':'seg_1','text':'旧名字递出物品。'}])
    ctx=reading({'e001':'正式名','e002':'旧名字'},[
        {'form':'旧名字','entity_id':'e001','presence':'on_stage','kind':'proper'}])
    ctx['inputs']=identity.chapter_inputs(directory)
    atomic_write_json(directory/'identity_context.json',ctx)
    planner.load_entity_index(novel,1)
    assert planner.canonical('旧名字')=='正式名'
    script={'shots':[{'index':1,'segment_id':'seg_1','characters':['旧名字'],
        'turns':[{'speaker_name':'旧名字','chat_target':'旧名字','text':'原话。'}],
        'actions':[{'actor':'旧名字','target':'旧名字','action':'递出物品'}]}]}
    shots=packer.prepared_shots(script,directory)
    assert shots[0]['characters']==['正式名']
    assert shots[0]['turns'][0]['speaker_name']==shots[0]['actions'][0]['actor']=='正式名'
    assert shots[0]['actions'][0]['target']==shots[0]['turns'][0]['chat_target']=='正式名'


def test_reading_view_keeps_raw_source_and_does_not_rewrite_inserted_names(tmp_path):
    novel = book(tmp_path, ('正式名', '旧名', '正式'))
    directory = novel / 'book_1'
    raw = [{'segment_id': 'seg_1', 'text': '旧\n名走近化身，旧名向正式点头。'}]
    atomic_write_json(directory / 'segments.json', raw)
    context = reading({'e001': '正式名', 'e002': '旧名', 'e003': '正式'}, [
        {'form': '旧名', 'entity_id': 'e001', 'kind': 'proper', 'presence': 'on_stage'},
        {'form': '正式', 'entity_id': 'e002', 'kind': 'proper', 'presence': 'on_stage'},
        {'form': '化身', 'entity_id': 'UNKNOWN', 'kind': 'proper', 'presence': 'on_stage'}])
    result = identity.reading_segments(directory, context)
    assert result[0]['text'].count('〔身份注：') == 2
    assert '旧\n名〔身份注：用于人物指称时即正式名，同一实体〕' in result[0]['text']
    assert '化身〔' not in result[0]['text']
    assert identity.read(directory / 'segments.json') == raw


def test_legacy_alias_not_supported_by_current_reading_is_only_a_retrieval_candidate(tmp_path):
    novel = book(tmp_path, ('本体',))
    atomic_write_json(novel / 'bible_aliases.json', {'第二身体': '本体'})
    directory = novel / 'book_1'
    atomic_write_json(directory / 'segments.json', [{'segment_id': 'seg_1', 'text': '本体走进屋里。'}])
    context = reading({'e001': '本体'}, [
        {'form': '本体', 'entity_id': 'e001', 'kind': 'proper', 'presence': 'on_stage'}])
    assert identity.effective_aliases(novel, context=context) == {}
    prompt = identity.prompt_context(directory, context=context)
    assert prompt['legacy_alias_candidates'] == {}
    assert '第二身体' not in prompt['candidates_not_source_facts'][0]['known_forms']


def test_bad_auxiliary_mention_does_not_discard_the_grounded_actor():
    a = {'source_id':1, 'name':'甲', 'forms':[
        {'form':'甲', 'kind':'proper', 'paragraphs':[1]},
        {'form':'她', 'kind':'contextual', 'paragraphs':[1]}]}
    clean, dropped, pending = identity.clean_reading({'actors':[a]}, [{'text':'甲走进屋里。'}])
    assert [f['form'] for f in clean['actors'][0]['forms']] == ['甲']
    assert dropped[0]['form'] == '她' and pending == []
    a['forms'] = [a['forms'][1]]
    clean, dropped, pending = identity.clean_reading({'actors':[a]}, [{'text':'甲走进屋里。'}])
    assert clean['actors'] == [] and pending == [a]


def test_declared_prop_never_uses_a_character_card(tmp_path):
    import build_clip_plan_thin as packer
    novel = book(tmp_path, ('甲', '宝器'))
    directory = novel/'book_1'
    atomic_write_json(novel/'entity/types.json', {'宝器':{'kind':'object'}})
    atomic_write_json(directory/'segments.json', [{'segment_id':'seg_1','text':'甲收起宝器。'}])
    script={'shots':[{'index':1,'segment_id':'seg_1','characters':['甲','宝器'],
                     'in_frame':['甲','宝器'],'motion_prompt':'甲收起宝器。','turns':[],
                     'actions':[{'actor':'甲','target':'宝器','action':'收起'}]}]}
    shot=packer.prepared_shots(script,directory)[0]
    assert shot['characters']==['甲'] and shot['in_frame']==['甲']
    assert '宝器' in shot['motion_prompt']


@pytest.mark.parametrize('confirmed, extracted', [(False, False), (True, True), (True, False)])
def test_readability_false_requires_confirmation_and_preserves_raw_evidence(tmp_path,monkeypatch,confirmed,extracted):
    from novel_manga import model_client
    novel=book(tmp_path,('甲乙',));directory=novel/'book_1'
    raw=[{'segment_id':'seg_1','text':'甲\n乙走进屋里。'}]
    atomic_write_json(directory/'segments.json',raw)
    calls=[]
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='source_readability_confirmation':
            return {'source_readable':confirmed,'source_problem':'' if confirmed else '字符次序错乱'}
        if kwargs['name']=='chapter_identity_readable_source' and extracted:
            return {'source_readable':True,'actors':[{'source_id':1,'name':'甲乙',
                'forms':[{'form':'甲乙','kind':'proper','paragraphs':[1]}],
                'kind':'individual','presence':'on_stage','paragraphs':[1],'appearance':''}]}
        return {'source_readable':False,'source_problem':'人名中间换行','actors':[]}
    monkeypatch.setattr(model_client,'ask_json',ask)
    if confirmed and extracted:
        result=identity.resolve_chapter(directory)
        assert result['mentions'][0]['source_quote']==raw[0]['text']
    else:
        expected=ValueError if confirmed else identity.UnreadableSource
        with pytest.raises(expected) as caught:
            identity.resolve_chapter(directory)
        if confirmed:
            assert not isinstance(caught.value,identity.UnreadableSource)
        assert not (directory/'identity_context.json').exists()
    assert len(calls)==(3 if confirmed else 2)
    assert identity.read(directory/'segments.json')==raw


def test_empty_cached_extraction_is_repaired_not_reused(tmp_path, monkeypatch):
    from novel_manga import model_client
    novel = book(tmp_path)
    directory = novel / 'book_1'
    atomic_write_json(directory / 'segments.json', [{'segment_id': 's', 'text': '乙走进来。'}])
    atomic_write_json(directory / 'identity_context.json', {
        'policy': identity.POLICY, 'inputs': identity.chapter_inputs(directory), 'source_actors': [], 'mentions': []})
    assert identity.current_context(directory) == {}
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        if len(calls) == 1:
            return {'source_readable': True, 'source_problem': '旧名混用', 'actors': []}
        return {'source_readable': True, 'actorless_confirmed': False, 'actors': [{
            'source_id': 1, 'name': '乙', 'paragraphs': [1], 'presence': 'on_stage',
            'kind': 'individual', 'appearance': '', 'forms': [{'form': '乙', 'kind': 'proper', 'paragraphs': [1]}]}]}
    monkeypatch.setattr(model_client, 'ask_json', ask)
    result = identity.resolve_chapter(directory)
    assert identity.active_cast_names(result) == {'乙'}
    assert identity.resolve_chapter(directory) == result
    assert calls == ['chapter_identity_source', 'chapter_identity_empty_confirmation']


@pytest.mark.parametrize('confirmed', [False, True])
def test_actorless_source_needs_positive_confirmation(tmp_path, monkeypatch, confirmed):
    from novel_manga import model_client
    novel = book(tmp_path)
    directory = novel / 'book_1'
    atomic_write_json(directory / 'segments.json', [{'segment_id': 's', 'text': '落日照着空山。'}])
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        return {'source_readable': True, 'actors': [],
                **({'actorless_confirmed': confirmed} if len(calls) > 1 else {})}
    monkeypatch.setattr(model_client, 'ask_json', ask)
    if confirmed:
        result = identity.resolve_chapter(directory)
        assert result['actorless_confirmed'] and not identity.active_cast_names(result)
        assert identity.current_context(directory) == result
        atomic_write_json(novel / 'bible_aliases.json', {'某称呼': '甲'})
        assert identity.resolve_chapter(directory)['actorless_confirmed']
    else:
        with pytest.raises(ValueError, match='no actors'):
            identity.resolve_chapter(directory)
        assert not (directory / 'identity_context.json').exists()
    assert len(calls) == 2


def test_unmatched_actor_blocks_instead_of_borrowing_unrelated_cast():
    with pytest.raises(ValueError, match='新人'):
        identity.active_cast_names({'unmatched_actors': [{'name': '新人', 'kind': 'individual', 'presence': 'on_stage'}]})


def test_actorless_planner_schema_has_no_empty_enum():
    from plan_chapter_thin import build_schema
    schema = build_schema([], ['山顶'], ['s'])
    clip = schema['properties']['clips']['items']['properties']
    assert clip['characters']['maxItems'] == 0
    assert clip['stages']['items']['properties']['in_frame']['maxItems'] == 0
    assert '"enum": []' not in json.dumps(schema)
