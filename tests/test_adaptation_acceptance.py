"""Meaning and ownership, not literal coverage, control the existing plot review."""
from copy import deepcopy
import json

import pytest
from novel_manga.application.planning import retention
from novel_manga.planning.context import PlannerContext
from novel_manga.planning import source_checks, prompts, validation
from novel_manga.planning.issues import PlanningCode
from support.planning_retention import run

SOURCE = '甲对乙说：“我不会把密信交给你。”'
SHOTS = [{'label':'c1 stage 1','segment_id':'seg_1','location':'庭院',
          'characters':['甲','乙'],'in_frame':['甲','乙'],'visual_prompt':'甲面对乙。',
          'motion_prompt':'甲答应交出密信。','end_state':'两人仍在庭院。',
          'turns':[{'speaker_name':'甲','delivery_mode':'visible_dialogue','inner_monologue':False,
                    'text':'我会把密信交给你。'}]}]


def failure(**changes):
    return {'shot':1,'field':'speech','source_quote':'我不会把密信交给你',
            'draft_quote':'我会把密信交给你','viewer_error':'把拒绝交信反转成同意交信。', **changes}


def test_existing_review_rejects_grounded_reversal_and_reuses_same_evidence(monkeypatch):
    calls=[]
    monkeypatch.setattr(retention,'ask_json',lambda *a,**k:calls.append(1)or {'verdict':'fail','errors':[failure()],'note':''})
    saved,issues=retention.review('拒绝交信',SHOTS,SOURCE)
    assert issues[0].code == PlanningCode.SOURCE_CONTRADICTION
    retention.review('拒绝交信',SHOTS,SOURCE,previous=deepcopy(saved))
    assert len(calls)==1


def test_empty_or_invented_judgment_does_not_become_content_pass(monkeypatch):
    for response in [{},{'verdict':'pass','errors':[failure()],'note':''},
                     {'verdict':'fail','errors':[failure(draft_quote='并不存在的对白')],'note':''}]:
        monkeypatch.setattr(retention,'ask_json',lambda *a,answer=response,**k:answer)
        with pytest.raises(ValueError):retention.review('',SHOTS,SOURCE)


def test_adapted_and_omitted_details_do_not_demand_verbatim_delivery(monkeypatch):
    captured=[]
    def judge(parts,*a,**k):
        captured.append(json.loads(parts[0]['text'].split('\n')[-1]))
        return {'verdict':'pass','errors':[],'note':'省略部分交接细节，仍保留客卿起步和暂不给高位，未造成事实反转。'}
    monkeypatch.setattr(retention,'ask_json',judge)
    result,issues=retention.review('原文两项条件',SHOTS,SOURCE,skipped=[{'segment_id':'seg_2','reason':'重复解释'}])
    assert result['verdict']=='pass' and not issues
    assert captured[0]['omissions'][0]['segment_id']=='seg_2'
    assert 'retained_dialogue' not in captured[0]  # no checklist of quote promises


def test_omitted_source_segments_are_recorded_without_a_coverage_veto():
    raw={'skipped_segments':[{'segment_id':'seg_2','reason':'作者说明'}]};errors=[]
    segments=[{'segment_id':'seg_1','text':'甲进屋。'},{'segment_id':'seg_2','text':'感谢读者。'},
              {'segment_id':'seg_3','text':'重复解释。'}]
    source_checks.chapter_coverage(raw,[],segments,{'seg_1':['s1']},'甲进屋。感谢读者。重复解释。',PlannerContext(),errors,[])
    assert not errors
    assert {x['segment_id']for x in raw['skipped_segments']}=={'seg_2','seg_3'}


def test_literal_omissions_are_not_reported_as_content_errors():
    assert validation.soft_warnings({'missing_quoted_lines':['被正常改写的一句'],
        'shot_count':2,'spoken_chars':10},ctx=PlannerContext(episode_shape='flex'))==[]
    prompt=prompts.outline_prompt('coverage')
    assert '不跳过' not in prompt and '省略' in prompt


def test_material_failure_returns_same_candidate_to_repair_without_another_author(tmp_path,monkeypatch):
    def reject(*args):
        return ({'verdict':'fail'},[retention.PlanningIssue(PlanningCode.SOURCE_CONTRADICTION,
            '拒绝被写成答应',stage='c1 stage 1',field='turns')])
    code,drafts,report,episode=run(tmp_path,monkeypatch,3,reject)
    assert code==2 and len(drafts)==1
    assert '拒绝被写成答应' in (episode/'planning_failed.json').read_text()
    assert (episode/'planning_attempt.json').exists()


def test_incomplete_review_preserves_candidate_instead_of_reauthoring(tmp_path,monkeypatch):
    def down(*args):raise TimeoutError('review not finished')
    code,drafts,_,episode=run(tmp_path,monkeypatch,3,down)
    assert code==2 and len(drafts)==1
    assert 'ReviewIncomplete' in (episode/'planning_failed.json').read_text()
    saved=json.loads((episode/'planning_attempt.json').read_text())
    saved['outline']={'sections':{'retained_dialogue':'保持主线'}}
    (episode/'planning_attempt.json').write_text(json.dumps(saved))
    code,drafts,_,_=run(tmp_path,monkeypatch,3,lambda *a:({'verdict':'pass','errors':[]},[]))
    assert code==0 and drafts==[]


def test_one_ungrounded_finding_does_not_erase_an_independent_grounded_error(monkeypatch):
    monkeypatch.setattr(retention,'ask_json',lambda *a,**k:{'verdict':'fail',
        'errors':[failure(),failure(source_quote='伪造的另一条原文')],'note':''})
    _,issues=retention.review('拒绝交信',SHOTS,SOURCE)
    assert any(i.code==PlanningCode.SOURCE_CONTRADICTION for i in issues)
    assert any(i.code==PlanningCode.REVIEW_INCOMPLETE for i in issues)


def test_unconfirmed_review_notes_are_not_agent_repair_targets(tmp_path):
    from novel_manga.application.planning import repair
    from tests.test_planning_agent_repair import repair_batch
    from novel_manga.util import atomic_write_json, read_json
    batch,_=repair_batch(tmp_path);path=batch.episode_dir(2)/'planning_failed.json'
    original={'errors':['有据的意思反转'], 'attempts':[{'errors':['有据的意思反转'],
        'review_unfinished':['另一个未经证实的指代推测']}]}
    atomic_write_json(path,original)
    packet=repair.failure_inputs(batch,2)
    assert 'review_unfinished' not in packet['planning_failure']['attempts'][0]
    assert packet['planning_failure']['errors']==original['errors']
    assert read_json(path)==original


def test_pending_draft_gets_current_review_before_agent_without_renewing_budget(tmp_path):
    from novel_manga.application.planning import repair
    from tests.test_planning_agent_repair import repair_batch
    from novel_manga.util import atomic_write_json,read_json
    batch,_=repair_batch(tmp_path);d=batch.episode_dir(2);segments=read_json(d/'segments.json')
    candidate={'clips':[{'clip_id':'c1','stages':[]}]};outline={'sections':{'retained_dialogue':'保留主线'}}
    atomic_write_json(d/'planning_attempt.json',{'planning_input':{'segments':segments},'candidate':candidate,'outline':outline})
    original={'context':repair.current_context(batch,2),'inputs':{'failure':'旧字面检查拒绝'},
              'initial_seconds':20,'used_seconds':20,'status':'held'}
    atomic_write_json(d/'planning-repair.json',original)
    seen=[]
    def plan(chapter,*,candidate):
        seen.append(read_json(candidate));batch.rows[chapter]['plan']='planned'
    batch.plan=plan
    assert repair.resume_failure(batch,2)
    assert seen==[{'draft':candidate,'outline':outline}]
    assert read_json(d/'planning-repair.json')==original


def test_model_admission_configuration_failure_is_not_a_content_retry(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from novel_manga.application.planning import repair
    from tests.test_planning_agent_repair import repair_batch
    from novel_manga.util import read_json
    batch,config=repair_batch(tmp_path);entries=[]
    @contextmanager
    def bad_slot(*a,**k):
        if entries:pytest.fail('configuration failure was retried as a content correction')
        entries.append(1)
        raise ValueError('NOVEL_MODEL_INFLIGHT_DIR is required for configured model resources')
        yield
    monkeypatch.setattr(repair,'model_slot',bad_slot)
    monkeypatch.setattr(repair,'run_session',lambda *a,**k:pytest.fail('Agent must not start'))
    result=repair.repair_chapter(batch,2,config=config)
    assert result['status']=='service_blocked' and len(entries)==1
    record=read_json(batch.episode_dir(2)/'planning-repair.json')
    assert record['initial_seconds']==config['timeout_seconds'] and record['used_seconds']==0
