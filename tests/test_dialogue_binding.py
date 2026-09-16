import copy
from pathlib import Path

from novel_manga.util import atomic_write_json
import dialogue_binding as binding
import story_identity as identity
from build_h3_prompts import compose
from h3_request_checks import request_issues


def fixture(tmp_path):
    d=tmp_path/'book'/'book_1'
    quote='乙惊慌地问：“怎么回事？”甲淡淡一笑。'
    atomic_write_json(d.parent/'story_bible.json',{'characters':[{'name':'甲'},{'name':'乙'}]})
    atomic_write_json(d/'segments.json',[{'segment_id':'seg_1','text':quote}])
    context={'policy':identity.POLICY,'inputs':identity.chapter_inputs(d),'entities':{'e001':'甲','e002':'乙'},
             'mentions':[{'form':n,'kind':'proper','entity_id':eid,'presence':'on_stage'} for eid,n in [('e001','甲'),('e002','乙')]],'relations':[]}
    atomic_write_json(d/'identity_context.json',context)
    fact={'stage':1,'turn':1,'speaker':'乙','source_quote':quote,'source_speaker_phrase':'乙','relation':'verbatim','adapted_text':'怎么回事？'}
    atomic_write_json(d/'source_speaker_contract.json',[fact])
    shots=[{'index':1,'characters':['甲'],'in_frame':['甲'],'turns':[{'speaker_name':'甲','delivery_mode':'visible_dialogue','text':'怎么回事？'}]}]
    return d,shots,fact


def test_explicit_grounded_fact_survives_an_older_policy_and_script_drift(tmp_path):
    d,shots,_=fixture(tmp_path)
    bindings=binding.apply_confirmed_speakers(d,shots)
    assert shots[0]['turns'][0]['speaker_name']=='乙' and '乙' in shots[0]['in_frame']
    assert bindings[(1,1)]['identity_policy']==identity.POLICY


def test_changed_words_or_disagreeing_source_owner_do_not_reuse_a_fact(tmp_path):
    d,shots,fact=fixture(tmp_path)
    shots[0]['turns'][0]['text']='这笔钱归我了。'
    assert not binding.confirmed_bindings(d,shots)
    shots[0]['turns'][0]['text']='怎么回事？'
    atomic_write_json(d/'source_speaker_contract.json',[{**fact,'speaker':'甲'}])
    assert not binding.confirmed_bindings(d,shots)


def test_final_request_uses_structured_owners_and_rejects_a_swapped_tag():
    clip={'request_seconds':5,'references':[{'name':'甲','role':'character'},{'name':'乙','role':'character'}],
          'dialogue_bindings':[{'stage':1,'speaker_name':'乙','text':'怎么回事？','delivery_mode':'visible_dialogue'}]}
    clip['prompt_h3']=compose(clip,['Both characters face each other.'],[(None,[('甲','怎么回事？',False)])])
    assert '<Subject 2> (S1) says' in clip['prompt_h3']
    assert not request_issues(clip)
    clip['prompt_h3']=clip['prompt_h3'].replace('<Subject 2> (S1) says','<Subject 1> (S1) says')
    assert any('dialogue binding' in r for r in request_issues(clip))


def test_offscreen_binding_and_extra_unowned_speech():
    clip={'request_seconds':5,'references':[{'name':'乙','role':'character'}],
          'dialogue_bindings':[{'stage':1,'speaker_name':'乙','text':'我在门外。','delivery_mode':'offscreen_dialogue'}]}
    clip['prompt_h3']=compose(clip,['The door is closed.'],[(None,[])])
    assert not request_issues(clip)
    clip['prompt_h3']=clip['prompt_h3'].replace('overall_soundscape:','<d>[Chinese] 新编旁白</d>\noverall_soundscape:')
    assert request_issues(clip)


def test_split_long_line_keeps_its_confirmed_owner(monkeypatch):
    import build_clip_plan_thin as packer
    monkeypatch.setattr(packer,'MAX_CLIP_SECONDS',5)
    shot={'index':1,'characters':['乙'],'turns':[{'speaker_name':'乙','delivery_mode':'visible_dialogue','text':'这句话很长，需要分成多段来表达，但说话者始终是同一个人。'}]}
    parts=packer.split_long_shot(shot)
    bindings=binding.clip_bindings(parts)
    assert len(parts)>1 and all(r['speaker_name']=='乙' for r in bindings)
    assert ''.join(r['text'] for r in bindings)==shot['turns'][0]['text']


def test_word_changes_are_not_framing_changes_and_actor_swap_is_detected():
    from repair_clips_thin import framing_signature,action_owners
    old=[{'characters':['甲','乙'],'shot_scale':'中景','actions':[{'actor':'甲','target':'甲'}],'visual_prompt':'甲给自己处理伤口'}]
    new=copy.deepcopy(old);new[0]['visual_prompt']='甲认真地给自己处理伤口'
    assert framing_signature(old)==framing_signature(new)
    new[0]['shot_scale']='特写'
    assert framing_signature(old)!=framing_signature(new) and action_owners(old)==action_owners(new)
    new[0]['actions'][0]['target']='乙'
    assert action_owners(old)!=action_owners(new)


def test_source_verifier_finds_current_video_even_when_proposed_request_changed(tmp_path):
    from source_recheck_thin import SourceVerifier
    v=object.__new__(SourceVerifier)
    old=tmp_path/'current.mp4';old.write_bytes(b'old')
    v.current_videos={'clip_01':{'video':str(old)}}
    v.proposed_plan={'clips':[{'clip_id':'clip_01','prompt':'new source-correct request'}]}
    assert v.video_of(tmp_path,'clip_01',{})==old
