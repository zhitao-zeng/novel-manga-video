"""Exercise production entrypoints with model responses, not a mocked semantic service."""
import copy
import json
import pytest
from pathlib import Path
from novel_manga.application.identity import ledger_store as store, ledger_resolution as resolve, ledger_judges as judges, ledger_flow as ledger_flow, semantic_review as review, flow, context as context_views
from novel_manga.story.source_identity import active_cast_names
from novel_manga.application.identity.reading_roster import decided_extras
from novel_manga.application.planning.source_menu import speakers
from novel_manga.llm import client


def book(path):
    store.write_json(path/'story_bible.json',{'characters':[{'name':'云','role':'人物'},{'name':'云苍冥','role':'人物'}], 'locations':[]})
    ledger=store.Ledger(path);ledger.save()
    return ledger


def raw(target='e002'):
    return {'mentions':[{'form':'云柔','entity':target,'entity_name':'云苍冥' if target=='e002' else '云',
                          'kind':'proper','presence':'on_stage','evidence':'云柔站在父亲身旁'}],
            'new_entities':[],'claims':[],'relations':[]}


def test_book_entrypoint_corrects_wrong_valid_id_before_writing(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    monkeypatch.setattr(judges,'judge_settings',lambda:None)
    monkeypatch.setattr(judges,'judge_link',lambda form,ev,assigned,*rest:{'entity':assigned['id'],'why':'old lexical assignment'})
    replies=iter([{'approved':False,'reason':'女孩是云，不是叔父','correction':raw('e001')},
                  {'approved':True,'reason':'最终归属符合原文','correction':None}])
    prompts=[]
    def ask(parts,*args,**kwargs):prompts.append(parts[0]['text']);return next(replies)
    monkeypatch.setattr(client,'ask_json',ask)
    store.write_json(ledger.raw_path(1),raw())
    ledger_flow.run_resolve(tmp_path,[1],workers=1,texts={1:'云柔站在父亲身旁。她就是云，云苍冥是她叔父。'})
    assert store.read_json(ledger.mentions_path(1),[])[0]['entity']=='e001'
    assert store.read_json(ledger.raw_path(1),{})['semantic_review']['approved']
    assert len(prompts)==2 and 'resolved' in prompts[0]
    assert store.read_json(tmp_path/'entity_index.json',{})==store.read_json(ledger.base/'index.json',{})


def test_failed_final_review_keeps_all_authoritative_data(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    monkeypatch.setattr(judges,'judge_settings',lambda:None)
    monkeypatch.setattr(judges,'judge_link',lambda form,ev,assigned,*rest:{'entity':assigned['id'],'why':''})
    before={p.name:p.read_bytes() for p in ledger.base.glob('*.json')}
    monkeypatch.setattr(client,'ask_json',lambda *a,**kw:{'approved':False,'reason':'仍无依据','correction':raw('e001')})
    with pytest.raises(ValueError,match='final semantic review'):
        resolve.resolve_chapter(ledger,1,'云柔站在父亲身旁。',raw(),workers=1)
    assert not ledger.mentions_path(1).exists()
    assert before=={p.name:p.read_bytes() for p in ledger.base.glob('*.json')}
    assert ledger.entities[0]['canonical']=='云' and not ledger.asked


def test_review_cannot_fabricate_quotation(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    monkeypatch.setattr(judges,'judge_settings',lambda:None)
    monkeypatch.setattr(judges,'judge_link',lambda form,ev,assigned,*rest:{'entity':assigned['id'],'why':''})
    bad=raw('e001');bad['mentions'][0]['evidence']='原文没有这句证据'
    monkeypatch.setattr(client,'ask_json',lambda *a,**kw:{'approved':False,'reason':'改归属','correction':bad})
    with pytest.raises(ValueError,match='ungrounded mention'):
        resolve.resolve_chapter(ledger,1,'云柔站在父亲身旁。',raw(),workers=1)
    assert not ledger.mentions_path(1).exists()


def actor(name='云柔'):
    return {'source_id':1,'name':name,'forms':[{'form':name,'kind':'proper','paragraphs':[1]}],
            'kind':'individual','count':1,'presence':'on_stage','appearance':'','paragraphs':[1]}


def test_prepare_identity_repairs_binding_and_caches_review(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ledger.novel_dir/'bible_aliases.json',{'云柔':'云苍冥'})
    store.write_json(ep/'segments.json',[{'segment_id':'s1','text':'云柔站在父亲身旁。她就是云，云苍冥是她叔父。'}])
    corrected={'actors':[actor()], 'bindings':[{'source_id':1,'entity_id':'e001','decision':'catalogue','paragraphs':[1],'reason':'父亲身边女儿'}]}
    calls=[]
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='chapter_identity_source':return {'source_readable':True,'source_problem':'','actors':[actor()]}
        if calls.count('chapter_binding_review')==1:return {'approved':False,'reason':'旧别名错指叔父','correction':corrected}
        return {'approved':True,'reason':'已校核','correction':None}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    assert result['mentions'][0]['entity_id']=='e001'
    assert flow.resolve_chapter(ep)==result
    assert calls==['chapter_identity_source','chapter_binding_review','chapter_binding_review']


def test_binding_discards_unsupported_alternate_form_before_final_review(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔站在父亲身旁。'}])
    corrected_actor=actor()
    corrected_actor['forms'].append({'form':'原文没有的称呼','kind':'contextual','paragraphs':[1]})
    correction={'actors':[corrected_actor], 'bindings':[{'source_id':1,'entity_id':'e001',
        'decision':'catalogue','paragraphs':[1],'reason':'依据实际原文'}]}
    prompts=[]
    def ask(parts,*args,**kwargs):
        if kwargs['name']=='chapter_identity_source':return {'source_readable':True,'source_problem':'','actors':[actor()]}
        prompts.append(parts[0]['text'])
        return {'approved':len(prompts)>1,'reason':'核对源文','correction':None if len(prompts)>1 else correction}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    assert result['mentions'][0]['entity_id']=='e001'
    assert result['source_actors'][0]['forms']==actor()['forms']
    assert '原文没有的称呼'not in prompts[-1]
    assert result['semantic_review']['approved']


def test_binding_still_rejects_actor_with_no_source_form(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔站在父亲身旁。'}])
    correction={'actors':[actor('不存在的人')], 'bindings':[{'source_id':1,'entity_id':'e001',
        'decision':'catalogue','paragraphs':[1],'reason':'错误候选'}]}
    def ask(*args,**kwargs):
        if kwargs['name']=='chapter_identity_source':return {'source_readable':True,'source_problem':'','actors':[actor()]}
        if kwargs['name']=='chapter_binding_review_correction':return correction
        return {'approved':False,'reason':'修订','correction':correction}
    monkeypatch.setattr(client,'ask_json',ask)
    with pytest.raises(ValueError,match='ungrounded source forms.*不存在的人'):flow.resolve_chapter(ep)
    assert not (ep/'identity_context.json').exists()


def test_invalid_legacy_source_actors_are_read_again_even_with_rewritten_cache_stamp(tmp_path,monkeypatch):
    from novel_manga.application.identity.store import chapter_inputs
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔站在父亲身旁。'}])
    store.write_json(ep/'identity_context.json',{'policy':'story-identity-v5-grounded-types',
        'inputs':chapter_inputs(ep),'source_actors':[actor('上一章的人')],'mentions':[]})
    calls=[]
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='chapter_identity_source':return {'source_readable':True,'source_problem':'','actors':[actor()]}
        return {'approved':True,'reason':'实际原文归属正确','correction':None}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    assert result['source_actors'][0]['name']=='云柔'
    assert calls==['chapter_identity_source','chapter_binding_review']


def test_invalid_binding_correction_gets_exact_feedback_and_independent_final_review(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔站在父亲身旁。'}])
    def candidate(name):return {'actors':[actor(name)],'bindings':[{'source_id':1,'entity_id':'e001',
        'decision':'catalogue','paragraphs':[1],'reason':'按原文归属'}]}
    calls=[];feedback=[]
    def ask(parts,*args,**kwargs):
        name=kwargs['name'];calls.append(name)
        if name=='chapter_identity_source':return {'source_readable':True,'source_problem':'','actors':[actor()]}
        if name=='chapter_binding_review_correction':
            feedback.append(parts[0]['text']);return candidate('云柔')
        return {'approved':calls.count('chapter_binding_review')>1,'reason':'核对姓名',
                'correction':None if calls.count('chapter_binding_review')>1 else candidate('杜撰人名')}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    assert result['semantic_review']['approved'] and result['source_actors'][0]['name']=='云柔'
    assert calls==['chapter_identity_source','chapter_binding_review','chapter_binding_review_correction','chapter_binding_review']
    assert 'ungrounded source forms: 杜撰人名'in feedback[0]


def test_binding_protocol_correction_is_bounded_and_cannot_approve_itself(monkeypatch):
    calls=[]
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='review_correction':return {'invalid':True}
        return {'approved':False,'reason':'有错','correction':{'invalid':True}}
    monkeypatch.setattr(client,'ask_json',ask)
    def validate(value):raise ValueError('still invalid')
    with pytest.raises(ValueError,match='still invalid'):
        review.close({}, {}, {},name='review',validate=validate)
    assert calls==['review','review_correction']


def test_unnamed_actor_becomes_a_reviewed_extra_without_global_renaming(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'持令牌的少女走到前面。'}])
    a=actor('持令牌的少女');a['forms'][0]['kind']='contextual'
    correction={'actors':[a],'bindings':[{'source_id':1,'entity_id':'UNKNOWN','decision':'extra','paragraphs':[1],'reason':'无名侍女'}]}
    calls=[]
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='chapter_identity_source':return {'actors':[a],'source_readable':True,'source_problem':''}
        return {'approved':calls.count('chapter_binding_review')>1,'reason':'匿名可演','correction':None if calls.count('chapter_binding_review')>1 else correction}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    assert result['semantic_extras']==['持令牌的少女'] and active_cast_names(result)==set()
    assert decided_extras({'roster':[], 'declined':[]}, result['unmatched_actors'], 1, lambda n:[n], context=result)==(['持令牌的少女'], [])
    assert speakers([], result)==['持令牌的少女']
    assert not (ledger.novel_dir/'reading_cast.json').exists()


def test_normal_review_reuses_cache_and_unrelated_entity_does_not_invalidate_it(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    monkeypatch.setattr(judges,'judge_settings',lambda:None)
    monkeypatch.setattr(judges,'judge_link',lambda form,ev,assigned,*rest:{'entity':assigned['id'],'why':''})
    calls=[]
    monkeypatch.setattr(client,'ask_json',lambda *a,**kw:calls.append(1) or {'approved':True,'reason':'正确','correction':None})
    source='云柔站在父亲身旁。'
    resolve.resolve_chapter(ledger,1,source,raw('e001'),workers=1)
    resolve.resolve_chapter(ledger,1,source,workers=1)
    assert len(calls)==1
    ledger._add_entity('无关角色','person',True,'',99);ledger.save()
    resolve.resolve_chapter(ledger,1,source,workers=1)
    assert len(calls)==1


def test_reviewed_named_actor_is_registered_for_existing_asset_growth(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'问天站在门口，喊道：我是问天。'}])
    a=actor('问天')
    decision={'source_id':1,'entity_id':'UNKNOWN','entity_kind':'person','decision':'named_missing','paragraphs':[1],'reason':'原文自报名'}
    calls=[]
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='chapter_identity_source':return {'actors':[a],'source_readable':True,'source_problem':''}
        return {'approved':calls.count('chapter_binding_review')>1,'reason':'原文明示姓名','correction':None if calls.count('chapter_binding_review')>1 else {'actors':[a],'bindings':[decision]}}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    after=store.Ledger(ledger.novel_dir)
    assert result['semantic_new_names']==['问天']
    assert after.by_name['问天']['named'] and after.by_name['问天']['kind']=='person'
    assert '问天' in store.read_json(ledger.novel_dir/'reading_cast.json',{})['characters']
    assert after.by_name['问天']['asset_id'] is None


def test_unapproved_or_failed_binding_does_not_save_success_context(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔站在父亲身旁。'}])
    def ask(*args,**kwargs):
        if kwargs['name']=='chapter_identity_source':return {'actors':[actor()],'source_readable':True,'source_problem':''}
        return {'approved':False,'reason':'无法确认','correction':None}
    monkeypatch.setattr(client,'ask_json',ask)
    with pytest.raises(ValueError,match='unresolved'):flow.resolve_chapter(ep)
    assert not (ep/'identity_context.json').exists()


def test_old_book_success_without_semantic_review_is_not_skipped(tmp_path):
    ledger=book(tmp_path)
    store.write_json(ledger.mentions_path(1),[])
    store.write_json(ledger.raw_path(1),raw())
    assert not review.committed_current(ledger,1,'源文本')
    store.write_json(ledger.raw_path(1),{'semantic_review':{'approved':True,'inputs':{'context':{'source':{'1':'源文本'}}}}})
    assert review.committed_current(ledger,1,'源文本')
    assert not review.committed_current(ledger,1,'修改后的源文本')


def test_source_review_reuses_legacy_extraction_but_not_legacy_approval(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔站在父亲身旁。'}])
    from novel_manga.application.identity.store import chapter_inputs
    store.write_json(ep/'identity_context.json', {'policy':'story-identity-v6-catalogue-bindings',
                     'inputs':chapter_inputs(ep),'source_actors':[actor()],'mentions':[],
                     'semantic_review':{'approved':True}})
    calls=[]
    correction={'actors':[actor()], 'bindings':[{'source_id':1,'entity_id':'e001','decision':'catalogue','paragraphs':[1],'reason':'原文同指'}]}
    def ask(*args,**kwargs):
        calls.append(kwargs['name'])
        return {'approved':len(calls)>1, 'reason':'重新核对旧归属', 'correction':None if len(calls)>1 else correction}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(ep)
    assert result['mentions'][0]['entity_id']=='e001'
    assert calls==['chapter_binding_review','chapter_binding_review']


def test_checked_binding_cannot_be_silently_undone_by_distinct_body_guard(tmp_path,monkeypatch):
    ledger=book(tmp_path/'book');ep=ledger.novel_dir/'book_1'
    store.write_json(ep/'segments.json',[{'text':'云柔和云苍冥站在桥上。'}])
    actors=[actor(),{**actor('云苍冥'),'source_id':2}]
    bad={'actors':actors, 'bindings':[{'source_id':sid,'entity_id':'e001','decision':'catalogue','paragraphs':[1],'reason':'错误合并'} for sid in [1,2]]}
    def ask(*args,**kwargs):
        if kwargs['name']=='chapter_identity_source':return {'actors':actors}
        if kwargs['name']=='chapter_binding_review_correction':return bad
        return {'approved':False,'reason':'两人须保留','correction':bad}
    monkeypatch.setattr(client,'ask_json',ask)
    with pytest.raises(ValueError,match='distinct source actors'):
        flow.resolve_chapter(ep)
    assert not (ep/'identity_context.json').exists()


def test_changed_identity_record_invalidates_success_but_design_changes_do_not(tmp_path,monkeypatch):
    ledger=book(tmp_path)
    monkeypatch.setattr(judges,'judge_settings',lambda:None)
    monkeypatch.setattr(judges,'judge_link',lambda form,ev,assigned,*rest:{'entity':assigned['id'],'why':''})
    monkeypatch.setattr(client,'ask_json',lambda *a,**kw:{'approved':True,'reason':'正确','correction':None})
    source='云柔站在父亲身旁。'
    resolve.resolve_chapter(ledger,1,source,raw('e001'),workers=1)
    assert review.committed_current(ledger,1,source)
    ledger.entities[0]['description']='画风外貌设计更改'
    assert review.committed_current(ledger,1,source)
    ledger.entities[0]['kind']='animal'
    assert not review.committed_current(ledger,1,source)


def test_story_pass_does_not_grow_from_rejected_identity_and_continues_other_chapters(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.application.planning import reading
    ledger=book(tmp_path)
    store.write_json(tmp_path/'novel.json',{'source':str(tmp_path/'source.txt')})
    store.write_json(ledger.mentions_path(1),[])
    source='云柔站在父亲身旁。她就是云。'
    episodes=[SimpleNamespace(index=i,source_title=f'第{i}章',source_text=source) for i in [1,2]]
    monkeypatch.setattr(reading,'read_novel',lambda *a,**kw:SimpleNamespace(episodes=episodes))
    monkeypatch.setattr(reading,'summarize_chapter',lambda i,*a:{'chapter':i,'summary':'摘要','hook':'悬念'})
    monkeypatch.setattr(reading,'scan_chapter',lambda *a,**kw:{'names':[],'locations':[]})
    monkeypatch.setattr(reading.ledger_extraction,'extract',lambda *a,**kw:raw('e001'))
    monkeypatch.setattr(judges,'judge_settings',lambda:None)
    monkeypatch.setattr(judges,'judge_link',lambda form,ev,assigned,*rest:{'entity':assigned['id'],'why':''})
    replies=iter([{'approved':False,'reason':'仍未解决','correction':raw('e001')},
                  {'approved':False,'reason':'复核未过','correction':raw('e001')},
                  {'approved':True,'reason':'正确','correction':None}])
    monkeypatch.setattr(client,'ask_json',lambda *a,**kw:next(replies))
    grown=[]
    monkeypatch.setattr(reading,'grow_bible',lambda path,text,i,**kw:grown.append(i) or {})
    monkeypatch.setattr('sys.argv',['story_pass','--novel-dir',str(tmp_path),'--min-chapter-chars','1','--scan-workers','1'])
    assert reading.main()==1
    assert grown==[2]
    assert store.read_json(ledger.mentions_path(1),[])==[]
    assert store.read_json(ledger.mentions_path(2),[])[0]['entity']=='e001'
