import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from benchmark_repair_cause import controlled_submit, clone_case, evaluation_passed
from novel_manga.application.repair.diagnosis import numbered_evidence, validate_diagnosis


def test_literal_diagnosis_cannot_route_with_invented_source_evidence():
    context={'passage':'医生蹲下开锁。','stages':[{'origin_index':9,'motion_prompt':'医生蹲下开锁。'}],
             'prompt':'医生蹲下开锁。','prompt_h3':''}
    answer={'cause':'generation_mismatch','reason':'test','source_quotes':['主角蹲下开锁。'],
            'script_quote':'医生蹲下开锁。','stage_indexes':[9]}
    assert validate_diagnosis(answer,context)['cause']=='uncertain'


def test_numbered_evidence_keeps_source_and_actual_request_distinct():
    context={'passage':'医生蹲下开锁。主角站在旁边。','stages':[{'origin_index':9,'motion_prompt':'医生蹲下开锁。'}],
             'prompt':'【阶段一】医生开锁','prompt_h3':'<Subject 1> is the protagonist.\n<Subject 1> opens the lock.'}
    source,script=numbered_evidence(context)
    assert source['S1']=='医生蹲下开锁。'
    assert script['D1_motion_prompt']=='医生蹲下开锁。'
    assert script['H2']=='<Subject 1> opens the lock.'


def test_experiment_submits_the_recorded_same_seed_once(tmp_path):
    seen=[]
    def backend(payload,base):
        seen.append((dict(payload),base));return 'task-1'
    receipt=tmp_path/'submission.json'
    call=controlled_submit(backend,20260914,receipt)
    payload={'seed':99,'seconds':15}
    assert call(payload,'http://example')=='task-1'
    assert seen[0][0]['seed']==20260914
    assert json.loads(receipt.read_text())['seed']==20260914
    with pytest.raises(RuntimeError):call(payload,'http://example')
    with pytest.raises(RuntimeError):controlled_submit(backend,20260914,receipt)(payload,'http://example')
    assert len(seen)==1


def test_arm_clones_write_their_own_plan_and_reference_only_the_snapshot(tmp_path):
    frozen=tmp_path/'frozen/wuyue';ep=frozen/'wuyue_1';ep.mkdir(parents=True)
    (frozen/'series_assets').mkdir();(frozen/'entity').mkdir()
    for name in ['clip_plan.json','chapter_script.json','episode_review.json','segments.json']:
        (ep/name).write_text('{}')
    (frozen/'story_bible.json').write_text('{}')
    novel,arm=clone_case(frozen,tmp_path/'arms/A',{'episode':1})
    (arm/'clip_plan.json').write_text('{"changed":true}')
    assert (ep/'clip_plan.json').read_text()=='{}'
    assert (novel/'series_assets').resolve()==(frozen/'series_assets').resolve()


def test_evaluation_uses_the_production_any_obvious_defect_rule():
    assert evaluation_passed({'verdict':'subtle'})
    assert not evaluation_passed({'verdict':'fine','same_person_twice':True})
    assert not evaluation_passed({'verdict':'obvious'})
    assert not evaluation_passed({'error':'judge unavailable','verdict':'fine'})
