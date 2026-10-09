"""An explicit individual speaker must retain its existing portrait channel."""
import pytest
from novel_manga.story import dialogue,h3


@pytest.mark.parametrize('count,spoken,individual',[(0,True,True),(0,False,False),(2,True,False)])
def test_zero_count_metadata_does_not_hide_a_current_individual_speaker(count,spoken,individual):
    clip={'references':[{'role':'character','name':'甲','asset_id':'a','path':'a.jpeg'},
                        {'role':'location','name':'房间','asset_id':'room','path':'room.jpeg'}],
          'crowd_roles':{'甲':{'count':count,'source_quote':'existing source'}},
          'dialogue_bindings':[{'speaker_name':'甲','delivery_mode':'visible_dialogue'}]if spoken else []}
    assert ('甲'in dialogue.character_pictures(clip))is individual
    assert any(r.get('name')=='甲'for _,_,r in h3.asset_subjects(clip))is(not individual)
    definitions,mapping=h3.subject_lines(clip)
    assert ('甲'in mapping)is individual
    assert any('shared clothing design'in d for d in definitions)is(not individual)


def test_compile_stamp_changes_only_for_zero_count_current_speakers():
    from novel_manga.application.profiles import h3_compile_inputs
    clip={'references':[],'crowd_roles':{'甲':{'count':0}},
          'dialogue_bindings':[{'stage':1,'speaker_name':'甲','text':'hello'}]}
    assert h3_compile_inputs(clip)['individual_zero_crowd_speakers']==['甲']
    clip['crowd_roles']['甲']['count']=2
    assert 'individual_zero_crowd_speakers'not in h3_compile_inputs(clip)
