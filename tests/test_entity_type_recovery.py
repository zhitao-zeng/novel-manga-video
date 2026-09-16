import time
from pathlib import Path

import pytest

from novel_manga.util import atomic_write_json
from clip_readiness import reference_issues
from h3_request_checks import source_crowds


def test_source_confirmed_type_blocks_but_old_design_judgments_are_not_source_truth(tmp_path):
    card = tmp_path / 'series_assets/characters/character_001/turnaround.jpeg'
    card.parent.mkdir(parents=True)
    card.write_bytes(b'card')
    clip = {'references':[{'role':'character','name':'道具','asset_id':'character_001','path':str(card.relative_to(tmp_path))}]}
    atomic_write_json(tmp_path/'series_assets/cards_review.json', {'characters':{
        'character_001':{'actions':['regenerate'],'judged_at':time.time()+1,'mismatch':'object rendered as person'}}})
    assert reference_issues(clip,tmp_path) == []
    atomic_write_json(tmp_path/'entity/types.json', {'道具':{'kind':'object'}})
    assert any(s.startswith('entity:') for s in reference_issues(clip,tmp_path))


def test_semantic_group_does_not_need_a_literal_number_or_occupational_suffix():
    clip={'cast':['某境界的修士'],'references':[{'name':'某境界的修士','role':'character'}]}
    ctx={'entities':{'e001':'某境界的修士'},'mentions':[{
        'form':'这些强者','entity_kind':'group','entity_id':'e001','count':0,'source_quote':'这些强者围在门外。'}]}
    assert source_crowds(clip,{},'这些强者围在门外。',context=ctx)['某境界的修士']['count']==0
    ctx['mentions'][0]['entity_kind']='individual'
    assert source_crowds(clip,{},'这些强者围在门外。',context=ctx)=={}


def test_partial_entity_repair_cannot_publish_an_incomplete_plan(tmp_path,monkeypatch):
    import prepare_recovery_thin as recovery
    import story_identity
    import repair_flow_thin
    directory=tmp_path/'book'/'book_1'
    plan={'clips':[{'clip_id':'clip_01','cast':['道具']} ]}
    atomic_write_json(directory/'clip_plan.json',plan)
    atomic_write_json(directory.parent/'entity/types.json',{'道具':{'kind':'object'}})
    monkeypatch.setattr(story_identity,'resolve_chapter',lambda _: {})
    monkeypatch.setattr(repair_flow_thin,'repair_episode',lambda *a,**k:{'changed':[],'why':'speaker unresolved'})
    with pytest.raises(ValueError,match='incomplete'):
        recovery.prepare(directory,'entities')
    assert recovery.read(directory/'clip_plan.json')==plan
    assert not (directory/'repair_history/history.json').exists()
