import copy

from novel_manga.story import fields
import novel_manga.planning.contracts as pc_contracts
from novel_manga.planning.context import PlannerContext
import repair_flow_thin as repair


def stage_schemas(names):
    planner_ctx = PlannerContext.from_env()
    full = pc_contracts.build_schema(names, ['庭院'], ['seg_1'], ctx=planner_ctx)['properties']['clips']['items']['properties']['stages']['items']
    patch = repair.schema_for(names, [1])['properties']['stages']['items']
    return full, patch


def test_shared_scene_fields_keep_full_and_patch_requirements_separate():
    for names in (['甲', '乙'], []):
        full, patch = stage_schemas(names)
        for field in ('actions', 'extras', 'in_frame'):
            assert full['properties'][field] == patch['properties'][field]
        assert ('turns' in full['required']) and ('turns' not in patch['required'])
        assert ('origin_index' in patch['required']) and ('origin_index' not in full['required'])
        assert full['properties']['in_frame']['maxItems'] == (6 if names else 0)


def test_one_field_change_reaches_both_requests_without_shared_mutable_schemas(monkeypatch):
    definition = copy.deepcopy(fields.ACTION_FIELD)
    definition['items']['properties']['target']['maxLength'] = 79
    monkeypatch.setattr(fields, 'ACTION_FIELD', definition)
    full, patch = stage_schemas(['甲'])
    assert full['properties']['actions']['items']['properties']['target']['maxLength'] == 79
    assert patch['properties']['actions']['items']['properties']['target']['maxLength'] == 79
    full['properties']['actions']['maxItems'] = 99
    assert stage_schemas(['甲'])[0]['properties']['actions']['maxItems'] == 3
