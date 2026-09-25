from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
from novel_manga.models.bible import Character, StoryBible
from novel_manga.story.h3 import compose, stages_of


def test_listener_is_not_forced_to_face_the_camera_and_turn_away_together():
    shot={'index':1,'characters':['甲','乙'],'listeners':['乙'],'in_frame':['甲','乙'],
          'extras':[],'actions':[],'visual_prompt':'甲在左，乙在右背对镜头。','motion_prompt':'甲开口，乙听着。',
          'end_state':'乙保持背对镜头。','camera':'甲的正面机位','light':'台灯','shot_scale':'中景',
          'turns':[{'speaker_name':'甲','delivery_mode':'visible_dialogue','text':'听我说。','emotion':'平静'}]}
    bible=StoryBible(novel_title='t',genre='generic',visual_style='2d',palette='',style_fingerprint='t',
                    characters=[Character(name=n,appearance='黑发',wardrobe='外套') for n in ['甲','乙']],locations=['房间'])
    prompt=ClipCompiler(compiler_options()).compile_prompt({'shots':[shot],'request_seconds':6},bible,['甲','乙'],[],'房间')
    assert '甲、乙正脸入镜' not in prompt
    assert '两人侧面相对' not in prompt
    assert '只露背影或在画外' not in prompt
    assert '画面中始终只有这2位人物' not in prompt
    assert '乙保持背对镜头' in prompt


def test_legacy_stage_preserves_explicit_bystander_and_empty_membership():
    shot = {'index': 1, 'characters': ['甲', '乙'], 'in_frame': ['甲', '乙'],
            'listeners': [], 'extras': [],
            'actions': [{'actor': '甲', 'action': '抬起右手', 'target': ''}],
            'visual_prompt': '甲站在桌边', 'motion_prompt': '甲抬起右手',
            'end_state': '甲保持举手', 'shot_scale': '中景', 'turns': []}
    empty = {**shot, 'index': 2, 'in_frame': [], 'actions': [],
             'visual_prompt': '空杯静置在桌上', 'motion_prompt': '杯子静止',
             'end_state': '杯子仍在桌上'}
    bible = StoryBible(novel_title='t', genre='generic', visual_style='2d', palette='',
                       style_fingerprint='t', characters=[
                           Character(name=n, appearance='黑发', wardrobe='外套')
                           for n in ['甲', '乙']], locations=['房间'])
    prompt = ClipCompiler(compiler_options()).compile_prompt(
        {'shots': [shot, empty], 'request_seconds': 10}, bible, ['甲', '乙'], [], '房间')
    first, second = stages_of(prompt)
    # Translation consumes each stage without the clip-wide cast header.
    assert '入镜：甲、乙。' in first[0]
    assert '入镜：无具名人物。' in second[0]
    assert '两人侧面相对' not in prompt


def test_hand_only_inner_voice_does_not_add_eye_acting_after_translation():
    clip={'request_seconds':6,'references':[{'role':'character','name':'甲','path':'a.jpeg'}],
          'dialogue_bindings':[{'stage':1,'speaker_name':'甲','delivery_mode':'offscreen_dialogue','inner_monologue':True,'text':'我先想想。'}]}
    result=compose(clip,['Only the hands are visible. The head stays outside the frame.'],[('',[])])
    assert 'private thought' in result
    assert 'only eyes and breathing carry the reaction' not in result
    assert 'The speaker finishes, closes their mouth' not in result


def test_last_utterance_finishes_before_a_silent_reaction_shot():
    clip={'request_seconds':10,'references':[{'role':'character','name':'甲','path':'a.jpeg'}],
          'shot_timing':[{'seconds':5},{'seconds':5}],
          'dialogue_bindings':[{'stage':1,'speaker_name':'甲','delivery_mode':'visible_dialogue','text':'事情就是这样……'}]}
    result=compose(clip,['<Subject 1> speaks.','The empty cup remains still.'],[('',[]),('',[])])
    before,after=result.split('[Shot 2]')
    assert '事情就是这样.</d>' in before
    assert 'The quoted words complete' in before
    assert '<d>' not in after
