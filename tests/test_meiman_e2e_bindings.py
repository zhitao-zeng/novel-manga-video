import copy
import wave
from types import SimpleNamespace

from novel_manga.models.bible import Prop
from novel_manga.planning.prompts import props_for_chapter, compact_bible
from novel_manga.story.compilation import ClipCompiler, CompilerOptions
from novel_manga.story.dialogue import clip_bindings
from novel_manga.story.h3 import compose, request_issues
from novel_manga.media import speech, subtitles, generation, cache


def test_action_summary_does_not_repeat_the_events_own_actions():
    from novel_manga.planning.normalization import cast_and_actions
    from novel_manga.planning.context import PlannerContext
    from novel_manga.story.actions import action_text
    actions = [{'actor': '托尼', 'action': '打响指', 'target': ''},
               {'actor': '席勒', 'action': '指着空机甲', 'target': '空机甲'}]
    event = '托尼打响指，另一台空机甲飞入屋内，席勒指着空机甲说话。'
    item = {'characters': ['席勒', '托尼'], 'location': '诊室', 'actions': actions,
            'motion_prompt': event, 'turns': []}
    _, _, _, motion, _ = cast_and_actions(item, ['席勒', '托尼'], ['席勒', '托尼'],
                                          {'诊室': '诊室'}, 1, PlannerContext(), [], [])
    assert motion == event
    assert action_text(actions).count('空机甲') == 1


def shot(who, inner=False):
    return {'index': 1, 'origin_index': 1, 'location': '诊室', 'segment_id': 's1',
            'characters': [who], 'extras': [], 'actions': [], 'shot_scale': '近景',
            'visual_prompt': '人物看着杯子', 'motion_prompt': '轻轻放下杯子', 'end_state': '杯子停稳',
            'turns': [{'speaker_name': who, 'delivery_mode': 'offscreen_dialogue' if inner else 'visible_dialogue',
                       'text': '这次免费。', **({'inner_monologue': True} if inner else {})}]}


def test_prop_explicit_alias_reaches_planner_without_name_guessing():
    armor = Prop(name='马克2机甲', aliases=['马克2'], owner='托尼', wearable=True)
    other = Prop(name='马克3机甲')
    assert props_for_chapter([armor, other], '马克2的面罩打开。') == [armor]
    assert props_for_chapter([other], '马克3的面罩打开。') == []
    bible = SimpleNamespace(novel_title='n', genre='g', characters=[], props=[armor], continuity_rules=[])
    data = compact_bible(bible, {})['props'][0]
    assert data['wearable'] and data['owner'] == '托尼' and data['aliases'] == ['马克2']


def test_short_clip_absorption_does_not_merge_voices_or_inner_and_spoken_lines():
    opts = CompilerOptions(15, 9, 3, 'execution', 8, voices={'席勒': 'a.wav', '托尼': 'b.wav'})
    compiler = ClipCompiler(opts)
    packed = compiler.pack([shot('席勒'), shot('托尼'), shot('托尼', inner=True)])
    assert len(packed) == 3
    assert len(compiler.pack([shot('席勒'), shot('席勒')])) == 1


def test_inner_voice_and_worn_prop_reach_actual_h3_syntax():
    s = shot('托尼', inner=True)
    clip = {'request_seconds': 5, 'dialogue_bindings': clip_bindings([s]), 'references': [
        {'role': 'character', 'name': '托尼', 'path': 'face.jpeg'},
        {'role': 'prop', 'name': '马克2机甲', 'path': 'armor.jpeg', 'wearers': ['托尼']},
        {'role': 'voice', 'name': '托尼', 'path': 'voice.wav'}]}
    prompt = compose(clip, ['<Subject 1> watches the cup.'], [('', [])])
    assert 'private thought' in prompt and 'jaw does not move' in prompt
    assert '<Audio 1> is the voice-timbre reference for <Subject 1> (S1)' in prompt
    assert 'not the clothing' in prompt and 'Ignore any face or human model in this prop picture' in prompt
    assert not request_issues({**clip, 'prompt_h3': prompt})


def test_h3_closes_a_split_clause_without_changing_its_words():
    s = shot('席勒')
    s['turns'][0]['text'] = '这恐怕是你无法体会的艰辛，'
    clip = {'request_seconds': 9, 'dialogue_bindings': clip_bindings([s]), 'references': [
        {'role': 'character', 'name': '席勒', 'path': 'face.jpeg'}]}
    prompt = compose(clip, ['<Subject 1> remains seated.'], [('', [])])
    assert '<d>[Chinese] 这恐怕是你无法体会的艰辛.</d>' in prompt
    assert 'closes their mouth' in prompt
    assert s['turns'][0]['text'].endswith('，')
    assert not request_issues({**clip, 'prompt_h3': prompt})


def test_h3_added_speech_needs_prompt_repair_instead_of_another_seed():
    from novel_manga.media.retries import RetryState, after_analysis
    failed = {'passed': False, 'issues': ['excess_unplanned_speech']}
    for generated in (True, False):
        decision = after_analysis(RetryState(1, 2), failed, generated=generated,
                                  free_retries=True, local_h3=True)
        assert decision.action == 'stop' and decision.attempt == 1
    missing = after_analysis(RetryState(1, 2), {'passed': False, 'issues': ['missing_dialogue']},
                             generated=True, free_retries=True, local_h3=True)
    assert missing.action == 'continue' and missing.attempt == 2


def test_official_asset_subjects_cuts_and_soundscape_are_not_extra_actors():
    clip = {'request_seconds': 9, 'shot_timing': [{'seconds': 3}, {'seconds': 6}],
            'references': [{'role': 'character', 'name': '托尼', 'path': 'actor.jpg'},
                           {'role': 'location', 'name': '诊室', 'path': 'room.jpg'},
                           {'role': 'prop', 'name': '机甲', 'path': 'armor.jpg', 'wearers': ['托尼']}],
            'dialogue_bindings': []}
    prompt = compose(clip, ['<Subject 1> enters <Subject 2>.', 'Two <Subject 3> suits stand beside the desk.'],
                     [('', []), ('', [])], soundscape='Wind enters through the open window.')
    assert '<Subject 2> is the setting in <Picture 2>' in prompt
    assert '<Subject 3> is the prop design in <Picture 3>' in prompt
    assert '[Shot 2] At 00:03.000,' in prompt
    assert '<Subject 1>: partially_preserved' in prompt
    assert 'overall_soundscape:\nWind enters through the open window.' in prompt
    assert not request_issues({**clip, 'prompt_h3': prompt})
    assert request_issues({**clip, 'prompt_h3': prompt.replace('Two <Subject 3>', 'Two <Subject 1>')})


def test_extra_gibberish_fails_speech_and_never_becomes_a_subtitle():
    first = '那你为什么不能直接开着战衣飞过去？'
    last = '因为钢铁侠扛着地狱巴士飞行的画面一定很美。'
    garbage = '您叫纯情和阿楚这不值得即会刚般萃才一弯生嘴'
    rows = [{'start': 1, 'end': 4, 'hypothesis': first}, {'start': 7, 'end': 10.5, 'hypothesis': garbage},
            {'start': 11, 'end': 15, 'hypothesis': last}]
    checked = speech.evaluate(first + last, copy.deepcopy(rows), -16, -1)
    assert checked['missing'] == 0 and not checked['passed']
    assert 'excess_unplanned_speech' in checked['issues']
    ctx = SimpleNamespace(clip_plan={'clips': [{'clip_id': 'c', 'lines': [{'text': first}, {'text': last}]}]})
    events = subtitles.subtitle_events(ctx, 'c', {**checked, 'duration': 15})
    assert garbage not in ''.join(e['text'] for e in events)


def test_same_reference_path_with_new_voice_invalidates_cached_video(tmp_path):
    voice = tmp_path / 'voice.wav'
    def write_voice(sample):
        with wave.open(str(voice), 'wb') as f:
            f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            f.writeframes(sample * 16000)
    write_voice(b'\x00\x01')
    ctx = SimpleNamespace(novel_dir=tmp_path, voice_budget=15, feedback={},
                          settings=SimpleNamespace(local_h3_base_url='pool'))
    clip = {'clip_id': 'c', 'request_seconds': 5, 'prompt_h3': '<Audio 1> is a reference.',
            'references': [{'role': 'voice', 'name': '席勒', 'path': 'voice.wav'}],
            'lines': [{'speaker_name': '席勒', 'text': '这次免费。'}]}
    request, refs, digests, _ = generation.build_request(ctx, clip, 1)
    assert request['reference_audios'] == [str(voice)]
    assert cache.request_matches(ctx, clip, request, refs, digests)
    write_voice(b'\x00\x02')
    assert not cache.request_matches(ctx, clip, request, refs, digests)


def test_thought_label_survives_normalization_and_subtitles():
    from novel_manga.planning.context import PlannerContext
    from novel_manga.planning.normalization import normalize_turns
    s = shot('席勒', inner=True)
    turns, visible = normalize_turns(s, ['席勒'], ['席勒'], 's1', PlannerContext(), [], [])
    assert turns[0]['inner_monologue'] and not visible
    ctx = SimpleNamespace(clip_plan={'clips': [{'clip_id': 'c', 'lines': turns}]})
    events = subtitles.subtitle_events(ctx, 'c', {'duration': 5, 'chunks': [
        {'start': 1, 'end': 3, 'hypothesis': '这次免费'}]})
    assert any('心声' in row['text'] for row in events)


def test_inner_audio_processing_preserves_raw_track_and_duration(tmp_path):
    import math, struct
    from novel_manga.media.postprocess import inner_voice_audio
    from novel_manga.util import media_duration
    path = tmp_path / 'native.wav'
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        f.writeframes(b''.join(struct.pack('<h', round(2000*math.sin(2*math.pi*440*i/16000))) for i in range(8000)))
    before = path.read_bytes()
    clip = {'clip_id': 'c', 'dialogue_bindings': clip_bindings([shot('席勒', inner=True)])}
    result = inner_voice_audio(SimpleNamespace(work=tmp_path), clip, path)
    assert result != path and result.is_file() and path.read_bytes() == before
    assert abs(media_duration(result) - media_duration(path)) < 0.04


def test_worn_armor_persists_until_removed_without_mutating_source():
    from novel_manga.story.scene import resolve_scene, SceneContext
    first = {**shot('托尼'), 'wears': {'托尼': '马克2机甲'}}
    middle = shot('托尼')
    last = {**shot('托尼'), 'wears': {'托尼': None}}
    script = {'shots': [first, middle, last]}
    before = copy.deepcopy(script)
    result = resolve_scene(script, SceneContext())
    assert result.shots[1]['wears'] == {'托尼': '马克2机甲'}
    assert result.shots[1]['props'] == ['马克2机甲']
    assert result.shots[2]['wears'] == {'托尼': None}
    assert not result.shots[2].get('props')
    assert script == before
    assert resolve_scene(result.script, SceneContext()).script == result.script


def test_splitting_question_and_answer_does_not_replay_the_summoning():
    from novel_manga.story.framing import visible_speaker_shots
    base = {**shot('席勒'), 'characters': ['席勒', '托尼'],
            'actions': [{'actor': '托尼', 'action': '召来', 'target': '空机甲'}],
            'motion_prompt': '托尼召来空机甲，席勒发问，托尼回答。'}
    turns = [shot('席勒')['turns'][0], {**shot('托尼')['turns'][0], 'text': '不然呢？'}]
    pieces, _ = visible_speaker_shots(base, turns, ['席勒', '托尼'], 'c stage 1')
    assert len(pieces) == 2
    assert '召来' in pieces[0]['motion_prompt']
    assert '召来' not in pieces[1]['motion_prompt'] and pieces[1]['actions'] == []
