"""Full planner retry traces, with every model and validation outcome simulated."""
import copy
import io
import json
import tempfile
import time
import sys
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
import httpx
import plan_chapter_thin as command
import planner_requests_thin as requests
import identity_flow_thin as identity
from novel_manga.planning.context import PlannerContext
from novel_manga.planning import validation
from novel_manga.planning.issues import ValidationResult, PlanningIssue, PlanningCode


def planner_traces():
    results={}
    for mode in ['patch','rewrite','floor','strict','repeat','truncated','patch_timeout']:
     with tempfile.TemporaryDirectory(prefix='nmv-planner-flow-') as tmp:
      base=Path(tmp);source=base/'novel.txt';source.write_text('第一章 庭院\n'+'主角走入庭院，看见院门紧闭。\n'*40)
      bible=base/'bible.json';bible.write_text(json.dumps({'novel_title':'测试','genre':'通用','visual_style':'国漫','palette':'青','style_fingerprint':'test','characters':[{'name':'主角','role':'主角','appearance':'黑发','wardrobe':'青衣'}],'locations':['庭院：空旷院落']}))
      shot={'origin_index':1,'clip_hint':'clip_1','segment_id':'seg_1','source_quote':'主角走入庭院，看见院门紧闭。','location':'庭院','characters':['主角'],'visual_prompt':'主角站在门口','motion_prompt':'主角停下','end_state':'主角停下','camera':'固定','light':'日光','shot_scale':'中景','turns':[{'speaker_name':'主角','delivery_mode':'visible_dialogue','text':'院门紧闭。','emotion':'平静','chat_target':''}]}
      raw={'video_title':'庭院','hook':'院门紧闭','summary':'主角来到庭院','clips':[]}
      calls=[];patches=[];clock=[0.0];count=[0]
      def call_model(**kwargs):
       calls.append(copy.deepcopy({k:v for k,v in kwargs.items() if k!='ctx'}));clock[0]+=1;count[0]+=1
       if mode=='truncated' and count[0]==1:return 'incomplete JSON',{'finish_reason':'length'}
       return json.dumps(raw),{}
      def checked(value,*args,**kwargs):
       code=detail=stage=None
       if mode in {'rewrite','repeat'}:code,detail,stage='UNKNOWN_LOCATION',"unknown location 'bad'",'clip_1 stage 1'
       elif mode=='floor':code,detail='DURATION_BELOW_MINIMUM','全集估算只有 10 秒，低于本次要求的下限 90 秒'
       elif mode in {'patch','patch_timeout'} and not value.get('patched'):code,detail,stage='VISIBLE_SPEAKER','missing speaker','clip_1 stage 1'
       return ValidationResult([PlanningIssue(getattr(PlanningCode,code),detail,stage=stage)] if code else [],[],[copy.deepcopy(shot)])
      def patch_plan(value,missing,faulty,*a,timeout,**kw):
       patches.append({'missing':missing,'faulty':faulty,'timeout':timeout});clock[0]+=timeout if mode=='patch_timeout' else 2
       if mode=='patch_timeout':raise TimeoutError('simulated patch timeout')
       return {**value,'patched':True}
      def strict(*a,**kw):
       message='发声字数 5 远低于下限 100'
       return [PlanningIssue(PlanningCode.STRICT_SPEECH_BELOW_MINIMUM,message)]
      ctx=PlannerContext.from_env();ctx.strict_plan=mode=='strict'
      args=['plan_chapter_thin.py',str(source),'--novel-id','book','--bible',str(bible),'--output-root',str(base/'out'),'--max-redo','2','--model','frozen-model','--base-url','http://model.invalid/v1']
      with patch.object(sys,'argv',args),patch.object(time,'monotonic',lambda:clock[0]),patch.object(requests,'call_model',call_model),patch.object(requests,'patch_plan',patch_plan),patch.object(validation,'validate_and_normalize',checked),patch.object(validation,'strict_plan_issues',strict),patch.object(identity,'resolve_chapter',lambda *a,**k:{}),patch.object(httpx.HTTPTransport,'handle_request',side_effect=AssertionError('no real HTTP')),redirect_stdout(io.StringIO()):
       code=command.main(context=ctx)
      directory=base/'out/book/book_1'
      reports={p.name:json.loads(p.read_text()) for p in directory.glob('*.json') if p.name in ['planning_failed.json','chapter_script_report.json','chapter_script.json','episode_plan.json']}
      result={'exit':code,'requests':calls,'patches':patches,'reports':reports}
      results[mode]=json.loads(json.dumps(result,ensure_ascii=False).replace(str(base),'<root>'))
    return results
