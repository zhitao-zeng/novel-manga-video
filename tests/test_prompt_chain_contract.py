import copy
import pytest
from novel_manga.application.rendering import h3 as translator
from novel_manga.story.h3 import compose, request_issues
from novel_manga.application.review import cast_video
from novel_manga.application.repair.agent import validate_prompt
from novel_manga.media import speech
from novel_manga.media.analysis import with_dialogue_timing
from novel_manga.review.prompts import shot_contract


def clip():
    return {'clip_id':'c','request_seconds':8,'render_family':'2d',
            'prompt':'【阶段一】甲走到桌边。声音：中文普通话，平静，甲开口说：{走吧。}结束时：甲转身离开。',
            'references':[{'role':'character','name':'甲','path':'a.jpg'}]}


def test_speech_stays_between_opening_and_result_in_actual_compilation(monkeypatch):
    c=clip();before=copy.deepcopy(c)
    def translate(parts,schema,**kw):
        assert 'words_for_timing_only' in parts[0]['text']
        return {'shots':['<Subject 1> reaches the desk. <DIALOGUE_1> He turns and leaves.'], 'soundscape':'Room tone.'}
    monkeypatch.setattr(translator,'ask_json',translate)
    assert translator.convert(c)
    text=c['prompt_h3']
    assert text.index('reaches the desk') < text.index('<d>') < text.index('turns and leaves')
    assert text.count('<d>')==1 and '<DIALOGUE_' not in text
    assert 'holds a silent reaction' not in text and 'worn, removed or wet' not in text
    assert c['prompt']==before['prompt']


def test_missing_dialogue_slot_is_rejected_without_silently_appending_words(monkeypatch):
    c=clip()
    monkeypatch.setattr(translator,'ask_json',lambda *a,**kw:{'shots':['He speaks and finishes.']})
    assert not translator.convert(c,tries=1) and not c.get('prompt_h3')


def test_final_request_state_works_without_a_visor_ledger():
    request='The silver faceplate remains raised throughout the shot.'
    row={'name':'甲','same_entity':True,'visibility':'visible','kind':'state','state_stage':0,
         'expected_state':'open'}
    assert cast_video.look_result(row,states={},request=request)['state_wrong']
    # A state claim goes to frame adjudication; a missing ledger cannot silently pass it.
    assert cast_video.look_result({**row,'expected_state':'unknown'},states={},request=request)['state_wrong']
    # The actual final request overrides a stale source-state ledger.
    assert cast_video.look_result(row,states={'甲':{'0':'closed'}},request=request)['state_wrong']
    assert not cast_video.look_result({**row,'visibility':'uncertain'},request=request)['state_wrong']


def test_extra_limbs_reach_existing_visual_failure_path():
    answer={'body_artifact':True,'note':'可见三条手臂','looks':[]}
    result=cast_video.merge({'severity':'pass'},answer,{'cast':['甲']},{})
    assert result['severity']=='fail' and result['visual_defects']
    assert 'body_artifact' in cast_video.findings(answer)


def test_short_unmatched_chunks_are_one_extra_utterance_not_subtitle_noise():
    rows=[{'start':0,'end':1,'hypothesis':'你好'},
          {'start':1.5,'end':2.6,'hypothesis':'是拽的瑞纳'},
          {'start':3,'end':4,'hypothesis':'妹面炙热'},
          {'start':4.5,'end':5.5,'hypothesis':'再见'}]
    result=speech.evaluate('你好。再见。',rows,-20,-2)
    assert 'excess_unplanned_speech' in result['issues'] and not result['passed']
    assert not speech.unplanned_segments('你好。再见。',[rows[0],{'start':2,'end':2.2,'hypothesis':'嗯'},rows[-1]])


def test_dialogue_tail_is_reported_without_rejecting_or_trimming_the_take():
    result={'passed':True,'duration':12.282,'reference':'再见。','chunks':[{'start':6,'end':7.757,'hypothesis':'再见'}]}
    before=copy.deepcopy(result);timed=with_dialogue_timing(result)
    assert timed['trailing_no_dialogue_seconds']==4.525 and timed['passed']
    assert result==before and timed['duration']==12.282


def test_same_identity_can_be_described_concisely_without_changing_reference():
    c=clip();c['prompt_h3']=compose(c,['A person stands.'],[('',[])])
    revised=c['prompt_h3'].replace('is the subject shown in','is the young man in')
    assert not request_issues({**c,'prompt_h3':revised})
    validate_prompt(c,revised)
    with pytest.raises(ValueError):validate_prompt(c,revised.replace('<Picture 1>','<Picture 2>'))


def test_reviewer_uses_final_request_not_stale_chinese_picture_numbers():
    c={'prompt':'旧说明：席勒对应图片1。','prompt_h3':'<Subject 1> is Tony from <Picture 1>.'}
    text=shot_contract(c)
    assert 'Tony' in text and '席勒对应图片1' not in text
    assert '旧说明' in shot_contract({'prompt':'旧说明'})


def test_correction_meta_commentary_is_not_a_scene_instruction():
    from novel_manga.story.h3 import clean_note
    text=('The following description reflects the structural and behavioral corrections regarding timing, '
          'mouth state, and audio-visual separation as requested, while acknowledging the absence '
          'of the specific line text in the prompt.')
    assert clean_note(text,'')==''


def test_translator_schema_requires_each_shot_and_slot_context(monkeypatch):
    c=clip()
    def translate(parts,schema,**kw):
        assert schema['properties']['shots']['minItems']==schema['properties']['shots']['maxItems']==1
        assert 'speaker' in parts[0]['text'] and '走吧。' in parts[0]['text']
        return {'shots':['<Subject 1> reaches the desk. <DIALOGUE_1> He leaves.'], 'soundscape':'Room tone.'}
    monkeypatch.setattr(translator,'ask_json',translate)
    assert translator.convert(c,tries=1)


def test_frame_verifier_cannot_mark_reported_extra_limbs_as_fine():
    from novel_manga.review.policy import verify_to_verdict
    result=verify_to_verdict({'verdict':'fine','body_artifact':True,'evidence':'三个独立手臂连接到同一躯干',
                              'instruction':'保持参考图中的正常肢体连接','people':[]})
    assert result['severity']=='fail' and result['visual_defects'] is True
    assert result['verify']['body_artifact'] is True


def test_one_generation_reads_source_images_manner_and_replaces_correction(tmp_path, monkeypatch):
    import json
    from PIL import Image
    directory = tmp_path / 'book' / 'book_1'
    directory.mkdir(parents=True)
    Image.new('RGB', (16, 16), 'silver').save(directory.parent / 'a.jpg')
    (directory / 'segments.json').write_text(json.dumps([
        {'segment_id': 's1', 'text': '他关掉推进器，然后说走吧。'},
        {'segment_id': 's2', 'text': '下一场飞进另一台空甲。'}], ensure_ascii=False))
    (directory / 'chapter_script.json').write_text(json.dumps({'shots':[
        {'index': 7, 'origin_index': 99, 'motion_prompt': '关掉推进器', 'wears': {'甲': '银甲'},
         'actions':[{'actor':'甲','action':'叉腰','target':''}]}]}, ensure_ascii=False))
    c = clip()
    c.update(segment_ids=['s1'], shot_indexes=[7], dialogue_bindings=[
        {'stage': 1, 'speaker_name': '甲', 'text': '走吧。', 'delivery_mode': 'visible_dialogue', 'emotion': '低声'}])
    calls=[]
    def generate(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        assert len(parts)==2 and parts[1]['type']=='image_url'
        text=parts[0]['text']
        assert '他关掉推进器' in text and '下一场飞进另一台空甲' not in text
        assert '银甲' in text and '低声' in text and '去掉电流音效' in text
        assert '"stage_index": 7' in text and '"origin_index": 99' in text
        assert '叉腰' in text
        assert 'manners' not in schema['properties']
        return {'shots':['<Subject 1> switches off the thrusters. The line is delivered in a low voice. <DIALOGUE_1> He turns to the door.'],
                'soundscape':'Room tone.', 'manners':['The line is delivered in a low voice.'], 'conflicts':[]}
    monkeypatch.setattr(translator, 'ask_json', generate)
    assert translator.convert(c, note='去掉电流音效。', episode_dir=directory)
    assert calls==['h3prompt']
    final=c['prompt_h3']
    assert final.count('low voice')==1 and final.count('switches off')==1
    assert 'thrusters' not in final.split('summary:')[1].split('retention_analysis:')[0]
    assert '电流' not in final


def test_unresolved_source_conflict_preserves_existing_request(monkeypatch):
    c=clip();c['prompt_h3']='previous candidate';before=copy.deepcopy(c)
    monkeypatch.setattr(translator,'ask_json',lambda *a,**k:{'shots':[], 'soundscape':'', 'manners':[],
                        'conflicts':['原文为穿甲人物，分镜却要求无人空甲说话']})
    problems=[]
    assert not translator.convert(c, tries=1, note='保留原文人物。', problems=problems)
    assert problems==['原文为穿甲人物，分镜却要求无人空甲说话']
    assert c==before


def test_preparation_hands_compiler_conflict_to_existing_repair_status(tmp_path, monkeypatch):
    from novel_manga.application.preparation import actions
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    c=clip();c['kind']='video'
    def compile_request(c, *, note, episode_dir, problems, source_conflicts):
        assert episode_dir==directory
        problems.append('原文穿甲者与分镜无人空甲冲突')
        source_conflicts.extend(problems)
        return False
    monkeypatch.setattr(translator,'convert',compile_request)
    monkeypatch.setattr(actions,'record',lambda directory,status,**kw:{'status':status,**kw})
    result=actions.translate_and_check(directory,{'clips':[c]}, {})
    assert result['status']=='needs_repair'
    assert result['request_conflicts']=={'c':{'stage_indexes':[], 'problems':['原文穿甲者与分镜无人空甲冲突']}}
    assert not (directory/'clip_plan.json').exists()


def test_changing_delivery_invalidates_the_compiled_request():
    from novel_manga.application.profiles import h3_stamp
    c=clip();c['dialogue_bindings']=[{'stage':1,'speaker_name':'甲','text':'走吧。',
                                    'delivery_mode':'visible_dialogue','emotion':'低声'}]
    old=h3_stamp(c)
    c['dialogue_bindings'][0]['emotion']='高声'
    assert h3_stamp(c)!=old


def test_action_only_correction_cannot_reuse_the_old_english():
    from novel_manga.application.profiles import h3_stamp, h3_prompt_outdated
    c=clip();c['action_bindings']=[{'stage':1,'source_stage':7,'actions':[
        {'actor':'甲','action':'站起来','target':''}]}]
    c['prompt_h3']='Existing English';c['prompt_h3_of']=h3_stamp(c)
    assert not h3_prompt_outdated(c, strict=True)
    c['action_bindings'][0]['actions'][0]['action']='站起来并叉腰'
    assert h3_prompt_outdated(c, strict=True)


def test_compile_conflict_reaches_source_audit_even_after_cached_pass(tmp_path, monkeypatch):
    from novel_manga.application.preparation import actions
    from novel_manga.util import atomic_write_json
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    passed={'source_readable':True,'issues':[]}
    atomic_write_json(directory/'pre_render_story_audit.json', {
        'inputs':actions.inputs(directory),'policy':actions.POLICY,'answer':passed})
    concerns={'c':{'stage_indexes':[8], 'problems':['穿戴者和无人空甲冲突']}}
    seen=[]
    def audit(directory, *, concerns=None):
        seen.append(concerns)
        return passed
    monkeypatch.setattr(actions,'audit',audit)
    assert actions.check_script(directory,{'clips':[]},concerns=concerns)==(passed,None)
    assert seen==[concerns]


def test_missing_reference_does_not_silently_generate_from_names(tmp_path, monkeypatch):
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    c=clip();before=copy.deepcopy(c)
    monkeypatch.setattr(translator,'ask_json',lambda *a,**k:pytest.fail('missing image must not reach the writer'))
    assert not translator.convert(c,episode_dir=directory)
    assert c==before


def test_postmixed_thoughts_are_explicitly_not_native_speech(monkeypatch):
    c=clip();c.update(audio_delivery='postmix', inner_voice={'text':'今天真麻烦。'},
                     action_bindings=[{'stage':1,'source_stage':1,'actions':[
                         {'actor':'甲','action':'在心里说今天真麻烦','target':''}]}])
    def write(parts,schema,**kw):
        text=parts[0]['text']
        assert '"audio_delivery": "silent_visual"' in text
        assert 'empty means no generated speech' in text
        assert 'output markers, not these words): []' in text
        assert '在心里说今天真麻烦' in text  # source thought remains evidence, not a native utterance
        return {'shots':['<Subject 1> drinks coffee and puts the cup down.'],
                'soundscape':'A sip, then a cup clink.', 'conflicts':[]}
    monkeypatch.setattr(translator,'ask_json',write)
    assert translator.convert(c)
    assert '<d>' not in c['prompt_h3'] and '<DIALOGUE_' not in c['prompt_h3']
    assert c['inner_voice']['text']=='今天真麻烦。'
