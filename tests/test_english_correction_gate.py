
from render_context_support import uninitialized_runner
from types import SimpleNamespace

import pytest
import render_flow_thin as render
from thin_profile import h3_source_digest, h3_prompt_outdated


def runner():
    r=uninitialized_runner()
    r.context.settings=SimpleNamespace(local_h3_base_url='pool')
    r.context.feedback={'c':'女作家说话，男子倾听'}
    r.save_clip_plan=lambda:None
    return r


def test_corrected_new_take_leaves_old_chinese_mode_only_with_matching_english():
    r=runner()
    c={'clip_id':'c','prompt':'原分镜','prompt_h3_skip':True,'prompt_h3':'English picture and direction',
       'prompt_h3_of':h3_source_digest('原分镜',r.context.feedback['c'])}
    assert r.english_correction_for_new_take(c)
    assert r.uses_h3_prompt(c) and 'prompt_h3_skip' not in c
    assert not r.english_correction_for_new_take(c)


@pytest.mark.parametrize('stamp',[None,'stale'])
def test_missing_or_outdated_english_does_not_fall_back_to_chinese_correction(stamp):
    r=runner();c={'clip_id':'c','prompt':'原分镜','prompt_h3_skip':True,'prompt_h3':'old English'}
    if stamp:c['prompt_h3_of']=stamp
    with pytest.raises(RuntimeError,match='current English'):
        r.english_correction_for_new_take(c)
    assert c['prompt_h3_skip']
    assert h3_prompt_outdated(c,r.context.feedback['c'])


def test_plain_old_chinese_request_without_correction_keeps_its_cache_mode():
    r=runner();r.context.feedback={};c={'clip_id':'c','prompt_h3_skip':True,'prompt_h3':'English'}
    assert not r.english_correction_for_new_take(c) and c['prompt_h3_skip']
