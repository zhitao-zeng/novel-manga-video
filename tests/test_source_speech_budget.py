import copy
import pytest
from novel_manga.application.agents.episode import speech_budget


def test_capacity_uses_actual_words_and_existing_duration():
    assert speech_budget.capacity_issue({'spoken_text':'说'*267, 'request_seconds':15})
    assert speech_budget.capacity_issue({'spoken_text':'说'*60, 'request_seconds':15}) is None


def test_source_compression_preserves_owner_order_and_speech_mode():
    shot={'turns':[{'speaker_name':'甲','text':'长'*100,'delivery_mode':'visible_dialogue'},
                   {'speaker_name':'乙','text':'答'*50,'delivery_mode':'offscreen_dialogue'}]}
    saved=copy.deepcopy(shot)
    clips=[{'spoken_text':'长'*100+'答'*50,'request_seconds':15}]
    result=speech_budget.compress_turns('source',shot,['关键决定。','保留回答。'],clips)
    assert [t['speaker_name'] for t in result]==['甲','乙']
    assert result[1]['delivery_mode']=='offscreen_dialogue' and shot==saved
    with pytest.raises(ValueError):speech_budget.compress_turns('script',shot,['短','短'],clips)
    with pytest.raises(ValueError):speech_budget.compress_turns('source',shot,['短'],clips)
    with pytest.raises(ValueError):speech_budget.compress_turns('source',shot,['短',''],clips)


def test_adoption_rejects_overfull_source_before_rendering():
    from support.episode_agent import episode, CATALOG, SOURCES
    from novel_manga.application.agents.episode.contracts import adopt
    draft=episode(turns=[{'speaker_name':'甲','text':'长'*150,'delivery_mode':'visible_dialogue'}],voice=False)
    with pytest.raises(ValueError,match='speech capacity'):
        adopt(draft,catalog=CATALOG,source_segments=SOURCES,profile={})
