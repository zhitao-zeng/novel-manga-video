import copy,json
from pathlib import Path
from novel_manga.application.review import confirmed
from novel_manga.application.review.store import import_confirmed_findings
from novel_manga.application.repair.managed import candidates
from support.managed_episode import fixture_episode


def test_confirmed_failure_enters_repair_and_cannot_be_cleared_by_broad_pass(tmp_path):
    d,clips,rows=fixture_episode(tmp_path)
    report=json.loads((d/'episode_review.json').read_text());report['feedback']={}
    for row in report['clips'].values():row.update(severity='pass',tier='ignore',verify={'verdict':'fine'})
    (d/'episode_review.json').write_text(json.dumps(report))
    finding={**{k:rows['a'][k] for k in ['video','take']},'issue':'前景和背景各有一个身体','instruction':'同框一个人'}
    old=import_confirmed_findings(d,{'a':finding})
    assert candidates(d)[0]==['a']
    fresh=copy.deepcopy(report)
    enforced=confirmed.enforce(d,fresh,old)
    assert enforced['feedback']['a']=='同框一个人'
    assert enforced['clips']['a']['severity']=='fail'


def test_replacement_needs_specific_confirmation_tied_to_its_take(tmp_path,monkeypatch):
    d,clips,rows=fixture_episode(tmp_path)
    claim={**{k:rows['a'][k] for k in ['video','take']},'issue':'重复','instruction':'一个人'}
    previous=import_confirmed_findings(d,{'a':claim})
    fresh=copy.deepcopy(previous);fresh['feedback']={}
    fresh['clips']['a'].update(take=[1,2,999],severity='pass',verify={'verdict':'fine'})
    assert 'a' in confirmed.enforce(d,fresh,previous)['feedback']
    monkeypatch.setattr(confirmed,'judge_finding',lambda *a:{'result':'resolved','observations':['一人'], 'evidence':'只有一个身体'})
    checked=confirmed.enforce(d,fresh,previous,verify=True)
    assert not checked['feedback'] and checked['clips']['a']['confirmed']['check']['take']==[1,2,999]
    fresh['clips']['a']['take']=[4,5,6]
    assert 'a' in confirmed.enforce(d,fresh,checked)['feedback']


def test_review_uses_postmixed_take_for_binding(tmp_path):
    from novel_manga.application.review.store import current_takes
    d,clips,rows=fixture_episode(tmp_path)
    mixed=d/'mixed.mp4';mixed.write_bytes(b'mixed')
    media=json.loads((d/'thin_media_report.json').read_text());media['clips'][0]['selected']['postmix_video']=str(mixed)
    (d/'thin_media_report.json').write_text(json.dumps(media))
    assert current_takes(d,{'clips':clips},{})['a']['video']==str(mixed)


def test_positive_checks_have_one_unambiguous_aggregate_result():
    from novel_manga.application.review.confirmed import finding_result
    good={'observations':['两人正常站立，没有穿插'], 'checks':[
        {'criterion':'正常成年人体型','met':True,'evidence':'两人正常比例'},
        {'criterion':'独立空间','met':True,'evidence':'没有身体穿插'}]}
    assert finding_result(good)['result']=='resolved'
    assert finding_result({'checks':[{'met':False,'evidence':'开场面罩打开'}]})['result']=='unresolved'
    assert finding_result({'checks':[{'met':None,'evidence':'看不清'}]})['result']=='uncertain'


def test_visual_review_does_not_invent_a_speech_failure_from_silent_frames():
    from novel_manga.application.review.confirmed import finding_result
    answer=finding_result({'checks':[{'criterion':'两人各占独立空间','scope':'visual','met':True,'evidence':'画面符合'},
                                    {'criterion':'台词保持','scope':'audio','met':None,'evidence':'帧没有音轨'}]})
    assert answer['result']=='resolved' and answer['checked_scope']=='visual'
    assert answer['checks'][1]['met'] is None


def test_targeted_check_receives_named_current_character_references(tmp_path, monkeypatch):
    seen={}
    monkeypatch.setattr(confirmed,'clip_frames',lambda *a:[tmp_path/f'frame{i}.jpg' for i in range(6)])
    monkeypatch.setattr(confirmed,'image_part',lambda path,width:{'type':'image','file':str(path)})
    def ask(parts,*a,**kw):
        seen['parts']=parts
        return {'observations':['两人'],'checks':[{'criterion':'头盔闭合','scope':'visual','met':True,'evidence':'闭合'}]}
    monkeypatch.setattr(confirmed,'ask_json',ask)
    clip={'references':[{'role':'character','name':'医生','path':'doctor.jpg'},
                        {'role':'character','name':'穿甲人','path':'closed.jpg','view':'closed'}]}
    answer=confirmed.judge_finding(clip,tmp_path/'video.mp4',{'instruction':'面罩保持闭合'},tmp_path/'frames',tmp_path)
    assert seen['parts'][0]['text'].endswith('医生')
    assert seen['parts'][1]['file']==str(tmp_path/'doctor.jpg')
    assert '穿甲人' in seen['parts'][2]['text'] and '不是无人空甲' in seen['parts'][2]['text']
    assert answer['result']=='resolved'


def test_a_finding_is_not_rejudged_on_the_take_it_was_confirmed_on(tmp_path, monkeypatch):
    """ch12-1 clip_19/20 (2026-09-26): re-judging the confirmed frames cleared two human-confirmed findings."""
    d,clips,rows=fixture_episode(tmp_path)
    claim={**{k:rows['a'][k] for k in ['video','take']},'issue':'悬空机械臂','instruction':'没有分离的装甲部件'}
    previous=import_confirmed_findings(d,{'a':claim})
    fresh=copy.deepcopy(previous);fresh['feedback']={}
    fresh['clips']['a'].update(severity='pass',verify={'verdict':'fine'})
    monkeypatch.setattr(confirmed,'judge_finding',lambda *a,**k:(_ for _ in ()).throw(AssertionError('re-judged')))
    enforced=confirmed.enforce(d,fresh,previous,verify=True)
    assert enforced['feedback']['a']=='没有分离的装甲部件' and enforced['clips']['a']['severity']=='fail'
    # a check an earlier run wrote on that same take does not clear it either
    previous['clips']['a']['confirmed']['check']={'result':'resolved','video':rows['a']['video'],'take':rows['a']['take']}
    assert 'a' in confirmed.enforce(d,copy.deepcopy(fresh),previous,verify=True)['feedback']
