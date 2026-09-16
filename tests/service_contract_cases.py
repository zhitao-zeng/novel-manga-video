"""Frozen source-reading and speaker request cases; every model response is simulated."""
import json
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch
import httpx
from novel_manga import model_client
from novel_manga.util import atomic_write_json
import identity_flow_thin as flow
import identity_context_thin as views
import repair_flow_thin as judges


def service_contracts():
    actor={'source_id':1,'name':'甲','forms':[{'form':'甲','kind':'proper','paragraphs':[1]}],
           'presence':'on_stage','paragraphs':[1],'kind':'individual','appearance':''}
    normal={'actors':[actor]}
    cases={'normal':[normal], 'unreadable':[{'source_readable':False,'actors':[]},{'source_readable':False,'source_problem':'字序乱了'}],
     'readable_retry':[{'source_readable':False,'actors':[]},{'source_readable':True},normal],
     'empty_retry':[{'actors':[]},{'actors':[],'actorless_confirmed':True}],
     'evidence_retry':[{'actors':[{**actor,'forms':[{'form':'不存在','kind':'proper','paragraphs':[1]}]}]},normal]}
    results={}
    with patch.object(httpx.HTTPTransport,'handle_request',side_effect=AssertionError('real HTTP forbidden')):
     for name,answers in cases.items():
      with tempfile.TemporaryDirectory(prefix='nmv-identity-freeze-') as tmp:
       novel=Path(tmp)/'book';directory=novel/'book_1'
       atomic_write_json(novel/'story_bible.json',{'characters':[{'name':'甲','role':'人物','appearance':'艺术设计'},{'name':'乙','role':'人物'}]})
       atomic_write_json(novel/'entity_index.json',{'characters':[{'name':'甲','forms':{'甲':1,'甲兄':1}}]})
       atomic_write_json(novel/'bible_aliases.json',{'甲兄':'甲'})
       atomic_write_json(directory/'segments.json',[{'segment_id':'s1','text':'甲说：“开门。”'}])
       calls=[];answers=iter(copy.deepcopy(answers))
       def ask(parts,schema,**kwargs):
        calls.append({'parts':parts,'schema':schema,'options':kwargs});return next(answers)
       with patch.object(model_client,'ask_json',ask):
        try:
         value=flow.resolve_chapter(directory)
         cached=flow.resolve_chapter(directory)==value
         context=views.prompt_context(directory,['甲'])
         value={k:v for k,v in value.items() if k not in {'at','inputs'}}
         results[name]={'value':value,'context':context,'cached':cached,'calls':calls}
        except ValueError as error:results[name]={'error':str(error),'calls':calls}
       # Preserve exact write sequence/content excluding only recorded time/path/stat metadata.
       files={}
       for p in sorted(directory.glob('*.json')):
        value=json.loads(p.read_text());files[p.name]={k:v for k,v in value.items() if k not in {'at','inputs'}} if isinstance(value,dict) else value
       results[name]['files']=files
     passage='甲说：“开门。”'
     shots=[{'origin_index':1,'turns':[{'speaker_name':'乙','delivery_mode':'visible_dialogue','text':'开门。'}]}]
     identities=[{'name':'甲','source_names':['甲'],'entity_id':'e001'},{'name':'乙','source_names':[],'entity_id':'e002'}]
     calls=[];evidence=[]
     def ask(parts,schema,**kwargs):
      kwargs.pop('settings',None)
      calls.append({'parts':parts,'schema':schema,'options':kwargs})
      return {'speakers':[{'stage':1,'turn':1,'source_quote':passage,'source_speaker_phrase':'甲','relation':'verbatim','speaker':'甲'}]}
     with patch.object(judges,'ask_json',ask):
      value=judges.speaker_contract(passage,shots,['甲','乙'],identities,evidence_out=evidence,identity_context={'policy':'test'})
      repeat=judges.speaker_contract(passage,shots,['甲','乙'],identities,fixed=evidence,identity_context={'policy':'test'})
     results['speaker']={'value':[[*k,v] for k,v in value.items()],'repeat':[[*k,v] for k,v in repeat.items()],'calls':calls,'evidence':evidence}
    return results
