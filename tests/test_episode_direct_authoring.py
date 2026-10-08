from novel_manga.media.context import RenderContext, RenderOptions, RenderState
from types import SimpleNamespace
import json

import pytest
from PIL import Image

from novel_manga.application.agents.episode import authoring
from novel_manga.util import atomic_write_json, read_json
from support.episode_agent import CATALOG, SOURCES, episode


def source_beats():
    return {'title': '相逢', 'adaptation_notes': ['保留问答。'], 'clips': [
        {'reference_ids': ['b', 'room', 'a', 'a_voice'], 'soundscape': 'Wind outside.',
         'shots': [{'segment_ids': ['s1'], 'in_frame': ['甲'], 'location': '房间',
                    'start_state': '甲坐着。', 'event': '甲抬眼发问。', 'end_state': '甲看向门口。',
                    'camera': '近景。', 'beats': [
                        {'direction': '<Subject 2> lifts his eyes toward the door.'},
                        {'speaker_name': '甲', 'text': '你来啦。', 'kind': 'speech'},
                        {'direction': 'His gaze settles on the doorway.'}]}]}
        for _ in range(7)]}


def test_normal_writer_owns_staging_while_host_binds_addresses_timing_and_dialogue_slots():
    from novel_manga.application.agents.episode.contracts import adopt
    import copy
    raw = source_beats(); before = copy.deepcopy(raw)
    draft = authoring.bind_source(raw, [15]*7, {})
    assert raw == before
    script, plan = adopt(draft, catalog=CATALOG, source_segments=SOURCES,
                         profile={}, target_seconds=(90, 120))
    assert plan['totals']['requested_seconds'] == 105
    assert [s['index'] for s in script['shots']] == list(range(1, 8))
    assert all(s['duration_seconds'] == 15 for s in script['shots'])
    assert script['shots'][0]['turns'][0]['text'] == '你来啦。'
    assert script['shots'][0]['turns'][0]['delivery_mode'] == 'visible_dialogue'
    assert draft['clips'][0]['h3']['shots'] == [
        '<Subject 2> lifts his eyes toward the door. <DIALOGUE_1> His gaze settles on the doorway.']


def test_offscreen_owner_and_silent_shot_have_no_contradictory_visibility_or_slots():
    raw = source_beats()
    raw['clips'][0]['shots'][0]['in_frame'] = ['乙']
    raw['clips'][1]['shots'][0]['beats'] = [{'direction': '<Subject 1> waits quietly.'}]
    draft = authoring.bind_source(raw, [15]*7, {})
    first, silent = draft['chapter_script']['shots'][:2]
    assert first['in_frame'] == ['乙'] and first['characters'] == ['乙', '甲']
    assert first['turns'][0]['delivery_mode'] == 'offscreen_dialogue'
    assert silent['turns'] == []
    assert '<DIALOGUE_' not in draft['clips'][1]['h3']['shots'][0]


def test_postmixed_thought_retains_words_but_no_native_dialogue_marker():
    raw = source_beats()
    raw['clips'][0]['shots'][0]['beats'][1]['kind'] = 'inner_voice'
    draft = authoring.bind_source(raw, [15]*7, {'inner_voice_delivery': 'postmix'})
    assert draft['chapter_script']['shots'][0]['turns'][0] == {
        'speaker_name': '甲', 'text': '你来啦。', 'delivery_mode': 'offscreen_dialogue', 'inner_monologue': True}
    assert '<DIALOGUE_' not in draft['clips'][0]['h3']['shots'][0]


def test_multiple_shots_keep_authored_durations_and_restart_native_slots():
    import copy
    raw = source_beats()
    first = raw['clips'][0]['shots'][0]
    second = copy.deepcopy(first)
    first['duration_seconds'], second['duration_seconds'] = 6, 9
    raw['clips'][0]['shots'].append(second)
    draft = authoring.bind_source(raw, [15]*7, {})
    assert draft['clips'][0]['shot_indexes'] == [1, 2]
    assert [s['duration_seconds'] for s in draft['chapter_script']['shots'][:2]] == [6, 9]
    assert all(body.count('<DIALOGUE_1>') == 1 for body in draft['clips'][0]['h3']['shots'])


def test_ambiguous_direction_does_not_silently_drop_spoken_text():
    raw = source_beats()
    raw['clips'][0]['shots'][0]['beats'][0]['text'] = '不能被删除的台词。'
    with pytest.raises(ValueError, match='direction beat'):
        authoring.bind_source(raw, [15]*7, {})


def test_legacy_private_voice_field_is_not_silently_changed_into_visible_speech():
    raw = source_beats()
    speech = raw['clips'][0]['shots'][0]['beats'][1]
    speech.pop('kind'); speech['inner_monologue'] = True
    with pytest.raises(ValueError, match='speech beat'):
        authoring.bind_source(raw, [15]*7, {})


@pytest.mark.parametrize('count', [6, 8])
def test_normal_writer_must_fill_the_allocated_clip_slots_exactly(count):
    import copy
    raw = source_beats()
    raw['clips'] = [copy.deepcopy(raw['clips'][0]) for _ in range(count)]
    with pytest.raises(ValueError, match='allocated clip'):
        authoring.bind_source(raw, [15]*7, {})


def test_unbound_humming_is_not_silently_dropped_from_the_video_request():
    raw = source_beats()
    raw['clips'][0]['shots'][0]['beats'][1].update(kind='singing', text='轻轻哼唱')
    with pytest.raises(ValueError, match='singing'):
        authoring.bind_source(raw, [15]*7, {})


def test_translation_changes_only_unbound_direction_fields_and_keeps_script_words(monkeypatch):
    import copy
    draft = authoring.bind_source(source_beats(), [15]*7, {})
    draft['clips'][0]['h3']['shots'][0] = '<Subject 2> 抬眼。 <DIALOGUE_1> 他看向门口。'
    draft['clips'][0]['h3']['soundscape'] = '窗外风声。'
    original = copy.deepcopy(draft)

    def ask(parts, schema, **options):
        assert not options['retry_truncated'] and options['json_object']
        assert '你来啦' not in parts[0]['text']
        return {'translations': [
            {'id': 'clip_01.soundscape', 'text_en': 'Wind outside.'},
            {'id': 'clip_01.shot_01', 'text_en': '<Subject 2> lifts his eyes. <DIALOGUE_1> He looks toward the door.'}]}

    monkeypatch.setattr(authoring, 'ask_json', ask)
    candidate, report = authoring.translate_directions(draft, timeout=20)
    assert draft == original and report['status'] == 'translated'
    assert candidate['chapter_script'] == original['chapter_script']
    assert candidate['clips'][1:] == original['clips'][1:]
    assert candidate['clips'][0]['reference_ids'] == original['clips'][0]['reference_ids']
    assert candidate['clips'][0]['request_seconds'] == original['clips'][0]['request_seconds']
    assert '抬眼' not in candidate['clips'][0]['h3']['shots'][0]


def test_translation_cannot_rebind_subjects_or_repeat_the_initial_story_call(monkeypatch):
    draft = authoring.bind_source(source_beats(), [15]*7, {})
    draft['clips'][0]['h3']['shots'][0] = '<Subject 2> 抬眼。 <DIALOGUE_1>'
    monkeypatch.setattr(authoring, 'ask_json', lambda *a, **k: {'translations': [
        {'id': 'clip_01.shot_01', 'text_en': '<Subject 1> lifts his eyes. <DIALOGUE_1>'}]})
    candidate, report = authoring.translate_directions(draft, timeout=20)
    assert report['status'] == 'failed'
    assert 'binding' in report['rejected']['clip_01.shot_01']
    assert candidate == draft
    assert '抬眼' in draft['clips'][0]['h3']['shots'][0]


def test_one_bad_translation_does_not_discard_other_valid_direction_fields(monkeypatch):
    draft = authoring.bind_source(source_beats(), [15]*7, {})
    draft['clips'][0]['h3']['shots'][0] = '<Subject 2> 抬眼。 <DIALOGUE_1>'
    draft['clips'][0]['h3']['soundscape'] = '窗外风声。'
    monkeypatch.setattr(authoring, 'ask_json', lambda *a, **k: {'translations': [
        {'id': 'clip_01.shot_01', 'text_en': '<Subject 2> 抬眼。 <DIALOGUE_1>'},
        {'id': 'clip_01.soundscape', 'text_en': 'Wind outside.'}]})
    candidate, report = authoring.translate_directions(draft, timeout=20)
    assert candidate['chapter_script'] == draft['chapter_script']
    assert candidate['clips'][0]['h3']['shots'] == draft['clips'][0]['h3']['shots']
    assert candidate['clips'][0]['h3']['soundscape'] == 'Wind outside.'
    assert draft['clips'][0]['h3']['soundscape'] == '窗外风声。'
    assert report['status'] == 'partial' and list(report['rejected']) == ['clip_01.shot_01']
    assert report['proposed']['clip_01.shot_01'].endswith('<DIALOGUE_1>')


def test_already_english_directions_need_no_translation_call(monkeypatch):
    draft = authoring.bind_source(source_beats(), [15]*7, {})
    monkeypatch.setattr(authoring, 'ask_json', lambda *a, **k: pytest.fail('English fields should be reused'))
    candidate, result = authoring.translate_directions(draft, timeout=20)
    assert candidate == draft and result['status'] == 'skipped'


def test_host_allocates_duration_inside_the_existing_episode_and_clip_limits():
    assert authoring.clip_budget([90, 120], 15) == [15]*7
    for target, cap in (([120, 150], 15), ([90, 120], 12)):
        budget = authoring.clip_budget(target, cap)
        assert target[0] <= sum(budget) <= target[1]
        assert all(4 <= value <= cap for value in budget)


@pytest.fixture
def context(tmp_path):
    ctx = SimpleNamespace(input_mode='source', run_dir=tmp_path, work_dir=tmp_path / 'sandbox', novel_dir=tmp_path / 'book', episode_dir=tmp_path / 'book/book_1')
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'ready': True, 'title': '相逢',
                     'target_seconds': [90, 120], 'clip_seconds_max': 15})
    (ctx.work_dir/'input/source.txt').write_text('完整原文头。'+'甲乙交谈。'*2000+'完整原文尾。')
    atomic_write_json(ctx.novel_dir/'profile.json', {'style': 'weimei', 'frame': '16:9'})
    atomic_write_json(ctx.novel_dir/'story_bible.json', {'characters': [{'name': '甲', 'appearance': '黑发'}]})
    atomic_write_json(ctx.episode_dir/'segments.json', [{'segment_id': 's1', 'text': '原文'}])
    assets = [{'id': 'a', 'name': '甲', 'role': 'character', 'image': 'input/a.jpeg'},
              {'id': 'a_expressions', 'name': '甲', 'role': 'character', 'image': 'input/other.jpeg'},
              {'id': 'voice', 'name': '甲', 'role': 'voice', 'image': 'input/voice.wav'}]
    atomic_write_json(ctx.run_dir/'catalog.json', assets)
    Image.new('RGB', (20, 20), (0, 10, 20)).save(ctx.work_dir/'input/a.jpeg')
    return ctx


def test_one_complete_source_call_sends_actual_primary_image_and_leaves_adoption_to_host(context, monkeypatch):
    calls = []
    atomic_write_json(context.novel_dir/'style.json', {'render_direction': 'specific book CG style'})
    atomic_write_json(context.work_dir/'input/reading/chapter-extract.json',
                      {'answer': {'people': [{'form': '甲', 'evidence': '独特的章级身份依据'}]}})

    def ask(parts, schema, **options):
        calls.append(options)
        text = parts[0]['text']
        assert '完整原文头。' in text and '完整原文尾。' in text
        assert 'specific book CG style' in text
        assert '独特的章级身份依据' in text
        images = [p for p in parts if p['type'] == 'image_url']
        assert len(images) == 1 and images[0]['image_url']['url'].startswith('data:image/jpeg;base64,')
        assert not options['retry_truncated'] and options['json_object']
        return source_beats()

    monkeypatch.setattr(authoring, 'ask_json', ask)
    result = authoring.propose(context, timeout=40)
    assert result['status'] == 'candidate' and result['assets'] == ['a', 'voice']
    assert read_json(context.work_dir/'output/episode.json') == authoring.bind_source(source_beats(), [15]*7, {})
    assert read_json(context.work_dir/'input/source-draft-raw.json') == source_beats()
    assert not (context.run_dir/'prepared.json').exists()
    assert not (context.episode_dir/'clip_plan.json').exists()
    assert authoring.propose(context)['skipped']
    assert len(calls) == 1


def test_failed_call_is_retained_for_agent_without_repeating_it(context, monkeypatch):
    calls = []

    def failed(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('draft call exhausted its time')

    monkeypatch.setattr(authoring, 'ask_json', failed)
    assert authoring.propose(context)['status'] == 'failed'
    assert authoring.propose(context)['skipped']
    assert calls == [1]
    assert not (context.work_dir/'output/episode.json').exists()


def test_source_writer_receives_effective_phase_design_including_age_and_hair(context, monkeypatch):
    atomic_write_json(context.novel_dir/'series_assets/phases.json', {'characters': {'甲': [
        {'from':1,'to':1,'asset_id':'character_001-p2','appearance':'全身黑毛的熊人',
         'base_costume':'黑色石片铠甲','hair':'黑色熊毛','age':'成年'}]}})
    seen = []
    def ask(parts, *args, **kwargs):
        data = json.loads(parts[0]['text'].split('Actual chapter and inputs:\n',1)[1])
        seen.extend(data['character_designs'])
        return source_beats()
    monkeypatch.setattr(authoring,'ask_json',ask)
    assert authoring.propose(context)['status']=='candidate'
    assert seen[0]['appearance']=='全身黑毛的熊人'
    assert seen[0]['wardrobe']=='黑色石片铠甲'
    assert seen[0]['hair']=='黑色熊毛' and seen[0]['age']=='成年'


def test_translation_failure_keeps_the_usable_story_draft_and_never_repeats_authoring(context, monkeypatch):
    calls = []
    raw = source_beats()
    raw['clips'][0]['soundscape'] = '窗外风声。'

    def ask(*args, **options):
        calls.append(options)
        if options['name'] == 'episode_source_draft':
            return raw
        raise TimeoutError('language conversion timed out')

    monkeypatch.setattr(authoring, 'ask_json', ask)
    result = authoring.propose(context, timeout=40)
    assert result['status'] == 'candidate' and result['translation']['status'] == 'failed'
    assert read_json(context.work_dir/'output/episode.json')['chapter_script']['shots'][0]['turns'][0]['text'] == '你来啦。'
    assert calls[1]['timeout'] <= calls[0]['timeout'] == 40
    assert authoring.propose(context)['skipped']
    assert len(calls) == 2


def test_initial_call_exhausting_budget_keeps_candidate_without_starting_translation(context, monkeypatch):
    now, calls = [0.0], []
    raw = source_beats(); raw['clips'][0]['soundscape'] = '窗外风声。'
    monkeypatch.setattr(authoring.time, 'monotonic', lambda: now[0])

    def ask(*args, **kwargs):
        calls.append(kwargs['name']); now[0] = 5
        return raw

    monkeypatch.setattr(authoring, 'ask_json', ask)
    result = authoring.propose(context, timeout=4)
    assert result['status'] == 'candidate' and result['translation']['status'] == 'failed'
    assert 'budget exhausted' in result['translation']['error']
    assert calls == ['episode_source_draft']
    assert (context.work_dir/'output/episode.json').is_file()


@pytest.mark.parametrize('existing', ['output/episode.json', 'episode-session.json'])
def test_existing_agent_work_is_never_overwritten(context, monkeypatch, existing):
    path = context.work_dir/existing
    atomic_write_json(path, {'owned': 'existing'})
    monkeypatch.setattr(authoring, 'ask_json', lambda *a, **k: pytest.fail('existing work must be retained'))
    assert authoring.propose(context)['skipped']
    assert read_json(path) == {'owned': 'existing'}
