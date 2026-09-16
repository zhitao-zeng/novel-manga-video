import repair_judges_thin as repair_judges

from render_context_support import uninitialized_runner
import copy
from novel_manga.review import storage as review_storage, contracts as review_contracts
import json
from pathlib import Path

import clip_readiness as readiness
import repair_flow_thin as repair
import repair_history as history
import render_flow_thin as render
import novel_manga.review.reconciliation as reconciliation


def test_restore_both_existing_source_parts_instead_of_repeating_the_reply():
    script={'shots':[{'index':5,'origin_index':5},{'index':6,'origin_index':6},{'index':7,'origin_index':6},{'index':8,'origin_index':8}]}
    plan={'clips':[{'clip_id':'a','kind':'video','shot_indexes':[5,7]}, {'clip_id':'b','kind':'video','shot_indexes':[7,8]}]}
    assert readiness.collapsed_source_addresses(plan,script)=={'a':[5,6]}
    plan['clips'][0]['shot_indexes']=[5,6]
    assert not readiness.collapsed_source_addresses(plan,script)


def test_three_source_parts_restore_the_missing_first_part_only():
    script={'shots':[{'index':i,'origin_index':19} for i in (19,20,21)]}
    plan={'clips':[{'clip_id':'a','kind':'video','shot_indexes':[18,21]}, {'clip_id':'b','kind':'video','shot_indexes':[20,21]}]}
    assert readiness.collapsed_source_addresses(plan,script)=={'a':[18,19]}


def test_explicit_cut_metadata_is_not_guessed_from_legacy_occurrences():
    script={'shots':[{'index':1,'origin_index':1},{'index':2,'origin_index':1}]}
    plan={'clips':[{'clip_id':'a','kind':'video','shot_indexes':[2],'shot_parts':[{'index':2,'part':[1,2]}]},
                   {'clip_id':'b','kind':'video','shot_indexes':[2],'shot_parts':[{'index':2,'part':[2,2]}]}]}
    assert not readiness.collapsed_source_addresses(plan,script)


def test_recovery_restores_source_parts_before_resuming_failed_identity_repair(tmp_path,monkeypatch):
    import prepare_recovery_thin as recovery
    import repair_blocked_plan as blocked
    d=tmp_path/'book_1';d.mkdir()
    (d/'chapter_script.json').write_text(json.dumps({'shots':[{'index':1,'origin_index':1},{'index':2,'origin_index':1}]}))
    (d/'clip_plan.json').write_text(json.dumps({'clips':[{'clip_id':'a','kind':'video','shot_indexes':[2]}, {'clip_id':'b','kind':'video','shot_indexes':[2]}]}))
    calls=[]
    monkeypatch.setattr(blocked,'repair_episode',lambda *a,**k:calls.append('repack') or {'changed':['a']})
    monkeypatch.setattr(repair,'repair_episode',lambda *a,**k:calls.append('identity') or {'changed':['a','b']})
    result=recovery.prepare(d,'identity')
    assert calls==['repack','identity']
    assert result['changed']==['a','b'] and result['structural_repair']['changed']==['a']


def test_resumed_residual_with_no_remaining_error_is_not_a_failed_preparation(tmp_path,monkeypatch):
    import prepare_recovery_thin as recovery
    monkeypatch.setattr(repair,'repair_episode',lambda *a,**k:{'clips':0,'why':'nothing to repair'})
    assert recovery.prepare(tmp_path/'book_1','residual')['skip_render']


def test_adapted_dialogue_uses_real_source_evidence_without_exact_line_match(monkeypatch):
    source='贝纳妮丝说：“今晚。伯爵宴请了王子，私人的宴请不合规矩，所以举办成了宴会。”'
    line='今晚伯爵宴请王子，举办宴会。'
    shots=[{'origin_index':3,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'秘女','text':line}]}]
    answer={'speakers':[{'stage':3,'turn':1,'speaker':'贝纳妮丝','source_quote':source,'relation':'condensed'}]}
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:answer)
    evidence=[]
    assert repair_judges.speaker_contract(source,shots,['贝纳妮丝','秘女'],[{'name':'贝纳妮丝','source_names':['贝纳妮丝']}],evidence_out=evidence)=={(3,1):'贝纳妮丝'}
    assert evidence[0]['adapted_text']==line
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k: (_ for _ in ()).throw(AssertionError('should reuse verified fact')))
    assert repair_judges.speaker_contract(source,shots,['贝纳妮丝'],[{'name':'贝纳妮丝','source_names':['贝纳妮丝']}],fixed=evidence)=={(3,1):'贝纳妮丝'}


def test_paraphrase_still_rejects_fabricated_source_quotes(monkeypatch):
    source='乙说：“走吧。”';shots=[{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','text':'快走。'}]}]
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':'乙说：“我们快走。”','relation':'paraphrased'}]})
    assert not repair_judges.speaker_contract(source,shots,['乙'],[])


def test_source_confirmation_can_replace_old_script_based_confirmation():
    base={'ep':1,'clip':'c','video':'v','take':[1,2,3]};records={}
    reconciliation.merge_evidence(records,{**base,'mode':'confirm','verdict':'obvious'})
    reconciliation.merge_evidence(records,{**base,'mode':'source_confirm','verdict':'fine'})
    reconciliation.merge_evidence(records,{**base,'mode':'joint','verdict':'obvious'})
    assert next(iter(records.values()))['verdict']=='fine'


def test_approved_existing_video_does_not_require_its_old_wrong_request(tmp_path):
    d=tmp_path/'book_1';d.mkdir();video=d/'clip.mp4';video.write_bytes(b'actual footage')
    clip={'clip_id':'c','kind':'video','prompt':'corrected intent','references':[]}
    record={'clip':history.accepted_clip_material(clip),'note':'','video':str(video),
            'take':review_storage.take_identity(video),'reference_digests':[]}
    (d/'source_acceptances.json').write_text(json.dumps({'c':record}))
    assert history.source_accepted_take(d,clip,'')==video
    assert history.source_accepted_take(d,{**clip,'prompt':'new scene'},'') is None
    assert history.source_accepted_take(d,clip,'new correction') is None
    r=uninitialized_runner();r.context.work=d/'work';r.context.feedback={};r.context.cache_only=False;r.context.max_attempts=2
    r.analyse_clip=lambda *a:{'passed':True,'video':str(video)}
    r.generate_clip=lambda *a:(_ for _ in ()).throw(AssertionError('must not regenerate an approved take'))
    assert r.process_clip(clip)['selected']['generated_this_run'] is False
    video.write_bytes(b'changed footage')
    assert history.source_accepted_take(d,clip,'') is None
