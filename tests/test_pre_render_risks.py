"""The pre-render check reads the request for the ways ch12 part one's video went wrong (2026-09-25).

Nothing was checked before that episode rendered; three clips of twenty drew the suit apart from its wearer,
whose card showed street clothes, and the visor, inner-voice lips and exhaust problems were all visible in
the requests beforehand.  One risk blocks - the wearer's card - and the rest are reported."""
import json

from novel_manga.application.identity.phases import POLICY
from novel_manga.application.preparation import readiness


def clip(**overrides):
    stage = ('【阶段一·中景】中景开场。开始时：席勒张嘴准备说话，斯塔克站在机甲内。机位：平视。'
             '光源：室内暖黄灯光，机甲喷口火光。主要事件：斯塔克威胁席勒。穿戴状态：托尼·斯塔克穿着马克2机甲；'
             '声音：…。结束时：席勒张嘴准备说话，斯塔克站在机甲内。')
    base = {'clip_id': 'clip_13', 'kind': 'video', 'request_seconds': 14, 'prompt': stage,
            'references': [{'role': 'character', 'name': '席勒', 'path': 'c1.jpeg'},
                           {'role': 'character', 'name': '托尼·斯塔克', 'path': 'c2.jpeg'},
                           {'role': 'prop', 'name': '马克2机甲', 'path': 'p1.jpeg'}],
            'lines': [{'speaker_name': '席勒', 'delivery_mode': 'offscreen_dialogue', 'text': '他心情好。', 'inner_monologue': True}]}
    return {**base, **overrides}


def test_a_wearer_whose_card_shows_other_clothes_blocks_until_a_worn_card_or_an_acceptance(tmp_path):
    block, report = readiness.render_risks(clip(), tmp_path)
    assert len(block) == 1 and '托尼·斯塔克' in block[0] and block[0].startswith('risk:')
    assert readiness.render_risks(clip(), tmp_path, {'wearer_card': ['托尼·斯塔克']})[0] == []


def phase_card(tmp_path, wears):
    (tmp_path / 'series_assets').mkdir(exist_ok=True)
    (tmp_path / 'series_assets' / 'phases.json').write_text(json.dumps({'policy': POLICY, 'characters': {
        '托尼·斯塔克': [{'from': 12, 'to': 12, 'asset_id': 'character_002-p1', 'label': '穿马克2机甲', **wears}]}}))
    return clip(references=[{'role': 'character', 'name': '席勒', 'path': 'c1.jpeg'},
                            {'role': 'character', 'name': '托尼·斯塔克', 'asset_id': 'character_002-p1',
                             'path': 'series_assets/characters/character_002-p1/turnaround.jpeg', 'phase': '穿马克2机甲'},
                            {'role': 'prop', 'name': '马克2机甲', 'path': 'p1.jpeg'}])


def test_a_phase_card_drawn_wearing_the_prop_clears_it(tmp_path):
    assert readiness.render_risks(phase_card(tmp_path, {'wears': '马克2机甲'}), tmp_path)[0] == []


def test_a_phase_card_of_another_look_does_not(tmp_path):
    block, _ = readiness.render_risks(phase_card(tmp_path, {'hair': '白发'}), tmp_path)
    assert len(block) == 1 and 'phases.json' in block[0]


def test_the_rest_is_reported_not_blocked(tmp_path):
    block, report = readiness.render_risks(clip(), tmp_path)
    text = ' '.join(report)
    for expected in ('面罩状态', '心声', '喷气/火光', '一瞬间动作', '预计静音'):
        assert expected in text, expected


def test_the_report_is_written_and_only_blockers_are_returned(tmp_path):
    episode = tmp_path / 'book_1'
    episode.mkdir()
    blocking = readiness.render_risk_report(episode, {'clips': [clip(), {'clip_id': 'card', 'kind': 'title_card'}]})
    assert list(blocking) == ['clip_13']
    saved = json.loads((episode / readiness.RISK_REPORT).read_text())
    assert saved['clips']['clip_13']['report'] and saved['clips']['clip_13']['block'] == blocking['clip_13']


def test_a_clip_without_worn_props_or_voices_passes_quietly(tmp_path):
    plain = clip(prompt='【阶段一·中景】中景开场。开始时：席勒坐着。光源：台灯。主要事件：席勒说话。声音：…。结束时：席勒坐着。',
                 references=[{'role': 'character', 'name': '席勒', 'path': 'c1.jpeg'}],
                 lines=[{'speaker_name': '席勒', 'delivery_mode': 'visible_dialogue', 'text': '你好' * 20}], request_seconds=12)
    assert readiness.render_risks(plain, tmp_path) == ([], [])


def test_a_voice_reference_with_the_same_name_does_not_hide_the_card(tmp_path):
    voiced = clip(references=[{'role': 'character', 'name': '席勒', 'path': 'c1.jpeg'},
                              {'role': 'character', 'name': '托尼·斯塔克', 'path': 'c2.jpeg'},
                              {'role': 'prop', 'name': '马克2机甲', 'path': 'p1.jpeg'},
                              {'role': 'voice', 'name': '托尼·斯塔克'},
                              {'role': 'voice', 'name': '席勒'}])
    block, report = readiness.render_risks(voiced, tmp_path)
    assert block and '心声' in ' '.join(report)


def speaking(sentence, speaker=1):
    return {'clip_id': 'clip_47', 'kind': 'video', 'request_seconds': 10, 'prompt': '', 'references': [],
            'prompt_h3': ('subject_definitions:\n<Subject 1> is the person shown in <Picture 1>.\n'
                          'detailed_description:\n[Shot 1] Planned duration: 10 seconds. ' + sentence +
                          f' <Subject {speaker}> (S1) says <d>[Chinese] 你好。</d>\noverall_soundscape:\nRoom tone.')}


def at_the_back(sentence, speaker=1, tmp_path=None):
    return any('后景/背对' in r for r in readiness.render_risks(speaking(sentence, speaker), tmp_path)[1])


def test_a_speaker_placed_at_the_back_is_reported(tmp_path):
    assert at_the_back('<Subject 1> stands in the background near the door.', 1, tmp_path)
    assert at_the_back('<Subject 1> sits with their back to the camera.', 1, tmp_path)
    assert at_the_back('<Subject 1> looks at <Subject 2>, who is positioned in the background.', 2, tmp_path)


def test_place_words_about_someone_or_something_else_are_not_the_speakers(tmp_path):
    """Agent ch12 clips 47 and 50 (2026-09-26): Schiller spoke from the foreground both times."""
    assert not at_the_back('<Subject 1> stands in the foreground, looking directly at <Subject 2>, '
                           'who is positioned in the background wearing the Mark 2 armor.', 1, tmp_path)
    assert not at_the_back('A fixed, eye-level close-up frames <Subject 1> standing in the foreground, illuminated '
                           'by cold blue light spilling from a sliding metal door in the background.', 1, tmp_path)
