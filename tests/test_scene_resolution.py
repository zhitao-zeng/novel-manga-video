import copy

from novel_manga.story.scene import SceneContext, resolve_scene
from novel_manga.util import atomic_write_json
from novel_manga.application.identity.scene import prepare_scene


def test_resolution_is_nonmutating_and_resolved_result_is_idempotent():
    quote = '乙喊道：“快走。”'
    context = SceneContext(
        segments=[{'segment_id': 's1', 'text': quote}],
        aliases={'小乙': '乙'}, types={'木门': {'kind': 'object'}},
        identity={'policy': 'test', 'entities': {'e1': '乙'}, 'mentions': [
            {'kind': 'proper', 'entity_id': 'e1', 'form': '乙'}]},
        speaker_facts=[{'stage': 1, 'turn': 1, 'speaker': '乙', 'source_quote': quote,
                        'source_speaker_phrase': '乙', 'relation': 'verbatim', 'adapted_text': '快走。'}])
    script = {'shots': [{'characters': ['甲', '木门'], 'in_frame': ['甲', '木门'],
                        'camera': '门外平视', 'light': '日光从左侧照入',
                        'actions': [{'actor': '小乙', 'action': '推开', 'target': '木门'}],
                        'turns': [{'speaker_name': '甲', 'delivery_mode': 'visible_dialogue', 'text': '快走。'}]},
                       {'characters': ['小乙'], 'camera': '同上', 'light': '同上', 'turns': []}]}
    before = copy.deepcopy(script); original_context = copy.deepcopy(context)
    result = resolve_scene(script, context)
    assert script == before and context == original_context
    assert result.shots[0]['turns'][0]['speaker_name'] == '乙'
    assert '木门' not in result.shots[0]['characters']
    assert result.shots[0]['actions'][0] == {'actor': '乙', 'action': '推开', 'target': '木门'}
    assert result.shots[1]['camera'] == '门外平视'
    assert resolve_scene(result, context) == result
    result.shots[0]['characters'].append('修改副本')
    assert script == before


def test_explicit_scene_contexts_do_not_share_aliases_or_casts():
    script = {'shots': [{'characters': ['掌柜'], 'turns': [], 'camera': '', 'light': ''}]}
    a = SceneContext(aliases={'掌柜': '甲'})
    b = SceneContext(aliases={'掌柜': '乙'})
    first = resolve_scene(script, a)
    assert resolve_scene(script, b).shots[0]['characters'] == ['乙']
    assert resolve_scene(script, a) == first
    assert first.shots[0]['characters'] == ['甲']


def test_file_adapter_does_not_modify_existing_script_or_production_files(tmp_path):
    novel = tmp_path / 'book'; directory = novel / 'book_1'; directory.mkdir(parents=True)
    atomic_write_json(novel / 'story_bible.json', {'characters': [{'name': '甲'}]})
    atomic_write_json(novel / 'bible_aliases.json', {'小甲': '甲'})
    script = {'shots': [{'characters': ['小甲'], 'turns': [], 'camera': '平视', 'light': '日光'}]}
    before = copy.deepcopy(script)
    files = {p: p.read_bytes() for p in novel.rglob('*') if p.is_file()}
    assert prepare_scene(script, directory).shots[0]['characters'] == ['甲']
    assert script == before
    assert files == {p: p.read_bytes() for p in novel.rglob('*') if p.is_file()}


def test_a_part_of_a_cut_chapter_resolves_against_its_own_directory(tmp_path):
    """prepare_scene took the directory apart into a chapter number with _(\\d+)$ and rebuilt it.
    meiman-daoshi_12-1 has no match, so every part of a cut chapter lost its source text and its
    speaker facts; and a number alone would have rebuilt meiman-daoshi_12 anyway, not the part."""
    novel = tmp_path / 'book'; part = novel / 'book_12-1'; part.mkdir(parents=True)
    atomic_write_json(novel / 'story_bible.json', {'characters': [{'name': '甲'}]})
    atomic_write_json(part / 'segments.json', [{'segment_id': 'seg_1', 'text': '甲说话。'}])
    atomic_write_json(part / 'source_speaker_contract.json', [{'stage': 1, 'turn': 1, 'speaker': '甲'}])
    from novel_manga.application.identity.scene import load_scene_context
    context = load_scene_context(novel, directory=part)
    assert context.source_available is True
    assert context.speaker_facts == [{'stage': 1, 'turn': 1, 'speaker': '甲'}]
    script = {'shots': [{'characters': ['甲'], 'turns': [], 'camera': '平视', 'light': '日光'}]}
    assert prepare_scene(script, part).shots[0]['characters'] == ['甲']
