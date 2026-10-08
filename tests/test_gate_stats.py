"""Gate firings and completed planner outcomes are separate facts."""
import json
from novel_manga.planning import gate_stats as gates


def fire(path, chapter, code, *, finish=True, passed=False, episode=None):
    run=gates.begin_gate_run(path,chapter,episode=episode)
    gates.record_gate_event(path,chapter,code,run_id=run,attempt=1)
    if finish:gates.finish_gate_run(path,chapter,run,passed=passed,fatal_codes=[] if passed else [code])
    return run


def test_one_firing_and_its_final_failure_are_counted_once(tmp_path):
    fire(tmp_path,1,'quote_not_source')
    assert gates.gate_report(tmp_path)['quote_not_source']=={'blocked':1,'fatal':1,'passed_chapters':0}


def test_successful_retry_replaces_the_old_failed_chapter_outcome(tmp_path):
    fire(tmp_path,1,'quote_not_source')
    run=gates.begin_gate_run(tmp_path,1)
    gates.finish_gate_run(tmp_path,1,run,passed=True)
    assert gates.gate_report(tmp_path)['quote_not_source']=={'blocked':1,'fatal':1,'passed_chapters':1}


def test_interrupted_run_is_not_inferred_passed(tmp_path):
    fire(tmp_path,1,'quote_not_source',finish=False)
    assert gates.gate_report(tmp_path)['quote_not_source']['passed_chapters']==0


def test_a_current_incomplete_retry_does_not_inherit_old_success(tmp_path):
    fire(tmp_path,1,'quote_not_source',passed=True)
    gates.begin_gate_run(tmp_path,1)
    assert gates.gate_report(tmp_path)['quote_not_source']['passed_chapters']==0


def test_late_finish_of_older_run_does_not_replace_current_run(tmp_path):
    old=fire(tmp_path,1,'quote_not_source',finish=False)
    new=gates.begin_gate_run(tmp_path,1)
    gates.finish_gate_run(tmp_path,1,new,passed=True)
    gates.finish_gate_run(tmp_path,1,old,passed=False,fatal_codes=['quote_not_source'])
    assert gates.gate_report(tmp_path)['quote_not_source']['passed_chapters']==1


def test_another_failed_part_prevents_claiming_the_whole_chapter_passed(tmp_path):
    fire(tmp_path,1,'quote_not_source',episode='book_1_a')
    fire(tmp_path,1,'retained_line_lost',episode='book_1_b',passed=True)
    assert gates.gate_report(tmp_path)['retained_line_lost']['passed_chapters']==0


def test_failure_on_another_gate_is_not_a_pass(tmp_path):
    run=gates.begin_gate_run(tmp_path,4)
    gates.record_gate_event(tmp_path,4,'retained_line_lost',run_id=run,attempt=1)
    gates.record_gate_event(tmp_path,4,'quote_not_source',run_id=run,attempt=1)
    gates.finish_gate_run(tmp_path,4,run,passed=False,fatal_codes=['quote_not_source'])
    fire(tmp_path,5,'retained_line_lost',passed=True)
    report=gates.gate_report(tmp_path)
    assert report['retained_line_lost']=={'blocked':2,'fatal':0,'passed_chapters':1}
    assert report['quote_not_source']=={'blocked':1,'fatal':1,'passed_chapters':0}


def test_empty_or_old_unqualified_events_cannot_establish_success(tmp_path):
    assert gates.gate_report(tmp_path)=={}
    (tmp_path/gates.EVENTS_NAME).write_text(json.dumps({'chapter':1,'code':'old','fatal':False})+'\n')
    assert gates.gate_report(tmp_path)=={}


def test_real_planner_records_passed_report_only_gate(tmp_path,monkeypatch):
    from support.planning_retention import run,missing_line
    code,_,_,episode=run(tmp_path,monkeypatch,0,missing_line)
    assert code==0
    rows=[json.loads(x) for x in (episode.parent/gates.EVENTS_NAME).read_text().splitlines()]
    assert rows[-1]['kind']=='outcome' and rows[-1]['passed'] is True
    assert gates.gate_report(episode.parent)['retained_line_lost']=={'blocked':1,'fatal':0,'passed_chapters':1}


def test_real_planner_failure_then_success_has_one_trigger_and_current_success(tmp_path,monkeypatch):
    from support.planning_retention import run
    from novel_manga.planning.issues import PlanningCode,PlanningIssue
    issue=PlanningIssue(PlanningCode.QUOTE_NOT_SOURCE,'引文不在原文中')
    review=lambda *a:({},[])
    code,_,_,episode=run(tmp_path,monkeypatch,0,review,validation_issues=[issue])
    assert code==2
    assert gates.gate_report(episode.parent)['quote_not_source']=={'blocked':1,'fatal':1,'passed_chapters':0}
    code,_,_,_=run(tmp_path,monkeypatch,0,review)
    assert code==0
    assert gates.gate_report(episode.parent)['quote_not_source']=={'blocked':1,'fatal':1,'passed_chapters':1}


def test_unwaived_presence_and_source_errors_both_remain_in_the_final_verdict(tmp_path,monkeypatch):
    from support.planning_retention import run
    from novel_manga.planning.issues import PlanningCode,PlanningIssue
    issues=[PlanningIssue(PlanningCode.QUOTE_NOT_SOURCE,'引文不在原文中'),
            PlanningIssue(PlanningCode.PRESENCE_DISAGREEMENT,'在场分歧',stage='c1 stage 1')]
    code,_,_,episode=run(tmp_path,monkeypatch,0,lambda *a:({},[]),validation_issues=issues)
    assert code==2
    report=gates.gate_report(episode.parent)
    assert report['quote_not_source']['fatal']==1
    assert report['presence_disagreement']['blocked']>=1
    assert report['presence_disagreement']['fatal']==1
