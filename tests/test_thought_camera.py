from novel_manga.application.preparation import readiness
from novel_manga.models.bible import Character, StoryBible
from novel_manga.story.compilation import ClipCompiler, CompilerOptions

BIBLE = StoryBible(novel_title='t', genre='g', visual_style='2d', palette='c', style_fingerprint='f',
                   characters=[Character(name='席勒', role='主角', appearance='a', wardrobe='w'),
                               Character(name='托尼·斯塔克', role='配角', appearance='b', wardrobe='x')],
                   locations=['诊所：房间'])


def stage(in_frame, inner=True):
    return {'label': 'clip_14 stage 1', 'index': 1, 'origin_index': 1, 'location': '诊所', 'shot_scale': '特写',
            'characters': ['席勒', '托尼·斯塔克'], 'in_frame': in_frame, 'listeners': [], 'extras': [], 'actions': [],
            'visual_prompt': '斯塔克站在桌前等待回答，席勒手指轻敲桌面', 'motion_prompt': '席勒内心分析斯塔克的处境',
            'end_state': '席勒做出决定', 'camera': '平视，正面拍摄席勒', 'light': '台灯暖光', 'sfx': '', 'avoid': '',
            'turns': [{'delivery_mode': 'offscreen_dialogue', 'speaker_name': '席勒', 'text': '斯塔克生活不能自理。',
                       'emotion': '冷静', **({'inner_monologue': True} if inner else {})}]}


def stage_line(shot):
    from novel_manga.application.profiles import frame_spec
    compiler = ClipCompiler(CompilerOptions(15, 9, 3, 'execution', 8, frame=frame_spec({'frame': '16:9'})))
    clip = {'clip_id': 'clip_14', 'kind': 'video', 'request_seconds': 7, 'references': [], 'lines': [], 'shots': [shot]}
    prompt = compiler.compile_prompt(clip, BIBLE, ['席勒', '托尼·斯塔克'], [], '诊所')
    return next(line for line in prompt.splitlines() if '【阶段' in line), prompt


def test_thought_does_not_rewrite_authored_camera():
    for people in [['席勒', '托尼·斯塔克'], ['席勒'], ['托尼·斯塔克']]:
        assert '机位：平视，正面拍摄席勒。' in stage_line(stage(people))[0]


def test_postmix_resolves_the_native_speech_risk_without_hiding_the_face(tmp_path):
    _, prompt = stage_line(stage(['席勒', '托尼·斯塔克']))
    clip = {'clip_id': 'clip_14', 'kind': 'video', 'request_seconds': 7, 'prompt': prompt,
            'references': [{'role': 'character', 'name': '席勒', 'path': 'c1.jpeg'}],
            'lines': [{'speaker_name': '席勒', 'delivery_mode': 'offscreen_dialogue', 'text': '斯塔克生活不能自理。',
                       'inner_monologue': True}]}
    assert any('心声' in risk for risk in readiness.render_risks(clip, tmp_path)[1])
    clip['audio_delivery'] = 'postmix'
    assert not any('心声' in risk for risk in readiness.render_risks(clip, tmp_path)[1])
