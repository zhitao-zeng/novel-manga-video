import copy
import json
from pathlib import Path

from novel_manga.llm import client as model_client
from novel_manga.story.catalog import IdentityCatalog
from novel_manga.util import atomic_write_json
from identity_store_thin import load_chapter
from identity_context_thin import prompt_context, reading_segments
from identity_flow_thin import resolve_chapter
from scene_context_thin import load_scene_context
from planner_context_thin import load_entity_index
from novel_manga.planning.context import PlannerContext
from service_contract_cases import service_contracts


def test_source_and_speaker_requests_evidence_and_cache_match_frozen_contracts():
    expected = json.loads((Path(__file__).parent / 'fixtures/services_before.json').read_text())
    assert service_contracts() == expected


def test_chapter_snapshot_is_reused_by_planning_scene_and_review(tmp_path, monkeypatch):
    import identity_store_thin as store
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    atomic_write_json(novel / 'story_bible.json', {'characters': [{'name': '小甲', 'role': '人物'}]})
    atomic_write_json(novel / 'entity_index.json', {'characters': [{'name': '小甲', 'forms': {'甲兄': 2}}]})
    atomic_write_json(directory / 'segments.json', [{'text': '甲兄开门。'}])
    counts = {}; original = store.read
    def read(path, default=None):
        counts[str(path)] = counts.get(str(path), 0) + 1
        return original(path, default)
    monkeypatch.setattr(store, 'read', read)
    monkeypatch.setattr(model_client, 'ask_json', lambda *a, **kw: {'actors': [{
        'source_id': 1, 'name': '小甲', 'forms': [{'form': '甲兄', 'kind': 'proper', 'paragraphs': [1]}],
        'kind': 'individual', 'presence': 'on_stage', 'appearance': '', 'paragraphs': [1]}]})
    data = load_chapter(directory)
    resolve_chapter(directory, data=data)
    assert data.context['entities']['e001'] == '小甲'
    before = copy.deepcopy(data)
    prompt = prompt_context(directory, data=data)
    annotated = reading_segments(directory, data=data)
    scene = load_scene_context(novel, 1, identity_data=data)
    planner = PlannerContext.from_env()
    load_entity_index(novel, 1, ctx=planner, identity_data=data)
    assert prompt['resolved_name_aliases']['甲兄'] == scene.aliases['甲兄'] == planner.aliases['甲兄'] == '小甲'
    assert '身份注' in annotated[0]['text']
    assert data.expected == before.expected and data.saved == before.saved
    assert all(counts[str(novel / name)] == 1 for name in ['story_bible.json', 'entity_index.json', 'bible_aliases.json', 'entity/types.json'])
    assert counts[str(directory / 'segments.json')] == 1
    atomic_write_json(directory / 'segments.json', [{'text': '小甲离开。'}])
    assert not load_chapter(directory).context
    assert data.context == before.context  # this operation's snapshot is stable


def test_catalogue_inputs_are_not_modified_or_shared():
    bible = {'characters': [{'name': '甲', 'role': '人物'}]}
    before = copy.deepcopy(bible)
    catalog = IdentityCatalog(bible, [], {}, {}, [], {})
    catalog.entities['e001']['design']['role'] = 'changed in snapshot'
    assert bible == before
