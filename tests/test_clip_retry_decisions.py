import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
import novel_manga.application.rendering.flow as render
from novel_manga.media.cache import CacheMiss
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.media import retries, policy
import novel_manga.application.repair.history as history
from test_review4_fixes import runner


def retry_cases():
    results={}
    modes=['blocked','approved','source','success','second_success','free_cached','paid_cached','late_error','cache_miss','uncertain','privacy','privacy_fallback','text','output','rewrite_fallback','cache_limit']
    for mode in modes:
     with tempfile.TemporaryDirectory(prefix='nmv-retry-') as tmp:
      base=Path(tmp);r=runner(base,local='pool' if mode in {'free_cached','cache_limit'} else None)
      clip={'clip_id':'clip_01','kind':'video','prompt':'scene','references':[]};events=[];count=[0]
      if mode=='blocked':r.context._blocked_clips={'clip_01':['missing asset']}
      if mode=='approved':r.context._approved_cached={'clip_01':{'video':str(base/'saved.mp4')}}
      if mode=='paid_cached':r.context.max_attempts=1
      if mode=='rewrite_fallback':clip['_softened']=True
      def generate(c,attempt):
       count[0]+=1;events.append(['generate',attempt,{k:v for k,v in c.items() if k.startswith('_')}])
       if mode=='late_error' and count[0]==2:raise RuntimeError('unavailable')
       if mode=='cache_miss' and count[0]==2:raise CacheMiss('missing cache')
       if mode=='uncertain' and count[0]==2:raise SubmissionUncertain('answer lost')
       if mode in {'privacy','privacy_fallback'} and count[0]==1:raise RuntimeError(render.PRIVACY_MARKER+' content[1]')
       if mode=='text' and count[0]<=2:raise RuntimeError(render.INPUT_TEXT_MARKER)
       if mode=='output' and count[0]==1:raise RuntimeError(render.OUTPUT_MODERATION_MARKERS[0])
       if mode=='rewrite_fallback' and count[0]==1:raise RuntimeError(render.INPUT_TEXT_MARKER+' '+render.OUTPUT_MODERATION_MARKERS[0])
       c['_generated']=False if mode=='cache_limit' or (mode=='free_cached' and count[0]<3) or (mode=='paid_cached' and count[0]==1) else True
       return base/f'take-{attempt}.mp4'
      def analyse(c,path):
       passed= mode not in {'late_error','cache_miss','uncertain','cache_limit'} and (count[0]>1 if mode in {'second_success','paid_cached'} else count[0]>=3 if mode=='free_cached' else True)
       return {'video':str(path),'passed':passed,'issues':[] if passed else ['missing_0.7_over_0.5'],'cer':0 if passed else .7,'max_volume_db':-5}
      def repair_ref(c,index):events.append(['repair_reference',index]);return [] if mode=='privacy_fallback' else ['fixed']
      def repair_all(c):events.append(['repair_all']);return ['fixed']
      def rewrite(c,attempt):events.append(['rewrite',attempt]);return mode!='rewrite_fallback'
      def cached(c,attempt):events.append(['probe_cache',attempt]);return mode=='paid_cached'
      r.generate_clip,r.analyse_clip,r.repair_rejected_reference,r.repair_privacy_cards,r.repair_refused_prompt,r.cached_take=generate,analyse,repair_ref,repair_all,rewrite,cached
      with patch.object(history,'source_accepted_take',lambda *a:base/'source.mp4' if mode=='source' else None),patch.object(render,'record_privacy_ok',lambda *a:events.append(['accepted_assets'])):
       value=r.process_clip(clip)
      results[mode]=json.loads(json.dumps({'events':events,'result':value,'flags':{k:v for k,v in clip.items() if k.startswith('_')}},ensure_ascii=False).replace(str(base),'<root>'))
    return results


def test_all_generation_recovery_cache_and_selection_traces_match_previous_runner():
    expected=json.loads((Path(__file__).parent/'fixtures/clip_retry_before.json').read_text())
    assert retry_cases()==expected


@pytest.mark.parametrize('generated,free,next_cached,expected,limit', [
    (True,False,None,'continue',2), (False,False,None,'check_cache',2),
    (False,False,False,'continue',2), (False,False,True,'continue',3),
    (False,True,None,'continue',3),
])
def test_cache_extension_is_explicit_and_does_not_mutate_state(generated,free,next_cached,expected,limit):
    state=retries.RetryState(1,2)
    before=copy.deepcopy(state)
    result=retries.after_analysis(state,{'passed':False},generated=generated,free_retries=free,next_cached=next_cached)
    assert result.action==expected and result.limit==limit and state==before


def test_pass_and_exhausted_budget_never_request_a_cache_probe():
    state=retries.RetryState(8,8)
    assert retries.after_analysis(state,{'passed':False},generated=False,free_retries=False).action=='stop'
    assert retries.after_analysis(state,{'passed':True},generated=False,free_retries=False).action=='stop'


def test_recovery_priority_and_flags_are_decided_without_performing_work():
    state=retries.RetryState(1,2);clip={}
    error=RuntimeError(policy.PRIVACY_MARKER+' content[02] '+policy.INPUT_TEXT_MARKER)
    steps=retries.recovery_steps(error,clip,state,moderation_repair=True)
    assert [s.action for s in steps]==['reference','soften','rewrite']
    assert steps[0].reference_slot=='02' and state.privacy_repairs==0 and clip=={}
    assert retries.recovery_steps(error,{'_softened':True,'_repaired':True},retries.RetryState(1,2,2),moderation_repair=True)==[]


def test_uncertain_submission_keeps_existing_take_and_cache_miss_keeps_its_error():
    attempts=[{'passed':False,'video':'old'}]
    kept=retries.failure_result('c',attempts,SubmissionUncertain('answer lost'))
    assert kept['selected']==attempts[0] and 'retake_error' in kept and 'error' not in kept
    missing=retries.failure_result('c',attempts,CacheMiss('missing'))
    assert missing['selected']==attempts[-1] and 'error' in missing
