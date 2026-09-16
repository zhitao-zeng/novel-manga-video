from novel_manga.review import storage as review_storage, contracts as review_contracts
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import novel_manga.review.reconciliation as reconciliation
import review_store_thin as review_store
import repair_review_thin as review


def setup_episode(tmp_path, *, bad=True):
    d = tmp_path / "novel" / "novel_1"
    d.mkdir(parents=True)
    video = d / "clip.mp4"
    video.write_bytes(b"current video")
    current = {"video": str(video), "take": review_storage.take_identity(video)}
    plan = {"clips": [{"kind": "video", "clip_id": "clip_01"}]}
    (d / "clip_plan.json").write_text(json.dumps(plan))
    (d / "thin_media_report.json").write_text(json.dumps({"clips": [{"clip_id": "clip_01", "selected": {"video": str(video)}}]}))
    row = {**current, "severity": "fail" if bad else "pass", "tier": "must_fix" if bad else "optional", "feedback": "old complaint" if bad else ""}
    (d / "episode_review.json").write_text(json.dumps({"policy": review_contracts.POLICY, "clips": {"clip_01": row}, "feedback": {"clip_01": "old complaint"} if bad else {}}))
    return d, current


def test_new_joint_recheck_can_add_a_real_error_to_an_old_pass(tmp_path):
    d, current = setup_episode(tmp_path, bad=False)
    review_store.reconcile(d, evidence(current), {})
    updated, _ = review_store.reconcile(d, evidence(current, verdict='obvious', mode='joint'), {})
    assert updated['clips']['clip_01']['joint_checked']
    assert updated['clips']['clip_01']['story_ok'] is False
    assert 'clip_01' in updated['feedback']


def evidence(current, *, verdict="fine", mode="all"):
    record = {**current, "ep": 1, "clip": "clip_01", "verdict": verdict, "people": ["甲(男) 站立"],
              "evidence": "visible evidence", "instruction": "甲只出现一次" if verdict == "obvious" else "", "mode": mode}
    return {reconciliation.evidence_key(1, "clip_01", current["video"], current["take"]): record}


def test_imported_precise_pass_clears_false_candidate_without_a_model_call(tmp_path, monkeypatch):
    d, current = setup_episode(tmp_path)
    result, takes = review_store.reconcile(d, evidence(current), {})
    assert result["feedback"] == {}
    assert result["clips"]["clip_01"]["verify"]["verdict"] == "fine"
    assert reconciliation.missing_reviews(result, takes, "all") == []
    state, legacy = tmp_path / "state", tmp_path / "legacy"
    state.mkdir(); legacy.mkdir()
    monkeypatch.setattr(review, "CurrentVerifier", lambda *a: (_ for _ in ()).throw(AssertionError("must reuse the result")))
    assert review.review_batch(d.parent, [1], "all", state, legacy)["judged"] == 0


def test_a_result_for_a_previous_take_cannot_clear_a_new_video(tmp_path):
    d, current = setup_episode(tmp_path)
    records = evidence(current)
    new = d / "new_clip.mp4"
    new.write_bytes(b"new take")
    (d / "thin_media_report.json").write_text(json.dumps({"clips": [{"clip_id": "clip_01", "selected": {"video": str(new)}}]}))
    result, takes = review_store.reconcile(d, records, {})
    assert "verify" not in result["clips"]["clip_01"]
    assert takes["clip_01"]["video"] == str(new)
    assert reconciliation.missing_reviews(result, takes, "changed") == ["clip_01"]


def test_flash_is_a_candidate_until_locally_confirmed(tmp_path):
    d, current = setup_episode(tmp_path)
    review_store.reconcile(d, evidence(current), {})
    flash = evidence(current, verdict="obvious")
    result, takes = review_store.reconcile(d, evidence(current), flash)
    assert result["feedback"] == {}
    assert reconciliation.missing_reviews(result, takes, "flash") == ["clip_01"]
    assert json.loads((d / "episode_review.json").read_text())["clips"]["clip_01"]["flash_pending"]
    result, takes = review_store.reconcile(d, evidence(current, verdict="obvious", mode="confirm"), flash)
    assert result["feedback"] == {"clip_01": "甲只出现一次"}
    assert result["clips"]["clip_01"]["flash_checked"]
    assert reconciliation.missing_reviews(result, takes, "flash") == []


def test_repeated_local_scan_does_not_overwrite_current_precise_verdict(tmp_path):
    d, current = setup_episode(tmp_path)
    review_store.reconcile(d, evidence(current, verdict="obvious"), {})
    result, _ = review_store.reconcile(d, evidence(current), {})
    assert result["feedback"] == {"clip_01": "甲只出现一次"}


def test_source_recheck_replaces_old_confirmation_only_for_the_reviewed_take(tmp_path):
    d, current = setup_episode(tmp_path)
    review_store.reconcile(d, evidence(current, verdict='obvious', mode='confirm'), {})
    source = evidence(current, mode='source_confirm')
    next(iter(source.values()))['source_confirmed_at'] = '2026-09-15 14:40:00'
    result, _ = review_store.reconcile(d, source, evidence(current, verdict='obvious'))
    assert result['feedback'] == {}
    assert result['clips']['clip_01']['source_confirmed_at'] == '2026-09-15 14:40:00'
    assert not result['clips']['clip_01'].get('flash_pending')
    (d / 'clip.mp4').write_bytes(b'a newly generated, unreviewed take')
    changed, _ = review_store.reconcile(d, source, {})
    assert not changed['clips']['clip_01'].get('verify')


def test_legacy_final_gate_overrides_its_retained_first_judge_with_matching_evidence(tmp_path):
    d, current = setup_episode(tmp_path)
    review_store.reconcile(d, evidence(current, verdict="obvious"), {})
    path = d / "episode_review.json"
    old = json.loads(path.read_text())
    old["clips"]["clip_01"].update(tier="optional", verified={"verdict": "fine", "evidence": "final gate cleared it"})
    old["feedback"] = {}
    path.write_text(json.dumps(old))
    result, takes = review_store.reconcile(d, evidence(current), {})
    row = result["clips"]["clip_01"]
    assert row["verify"]["verdict"] == "fine" and "verified" not in row
    assert result["feedback"] == {}
    assert reconciliation.inspection_counts(result, takes, ["clip_01"])["failed"] == 0
    assert reconciliation.inspection_counts(result, takes, ["clip_01"])["passed"] == 1


def test_legacy_gate_annotation_alone_does_not_override_without_matching_evidence(tmp_path):
    d, current = setup_episode(tmp_path)
    review_store.reconcile(d, evidence(current, verdict="obvious"), {})
    path = d / "episode_review.json"
    old = json.loads(path.read_text());old["clips"]["clip_01"]["verified"] = {"verdict": "fine"}
    path.write_text(json.dumps(old))
    result, _ = review_store.reconcile(d, {}, {})
    assert result["clips"]["clip_01"]["verify"]["verdict"] == "obvious"


def test_black_clip_stays_required_even_when_semantic_judge_says_fine(tmp_path):
    d, current = setup_episode(tmp_path)
    path = d / "episode_review.json"
    old = json.loads(path.read_text());old["clips"]["clip_01"]["technical"] = True
    path.write_text(json.dumps(old))
    result, _ = review_store.reconcile(d, evidence(current), {})
    assert "clip_01" in result["feedback"] and result["clips"]["clip_01"]["technical"]


def test_changed_review_leaves_unchanged_legacy_passes_for_the_running_full_audit(tmp_path):
    d, _ = setup_episode(tmp_path, bad=False)
    result, takes = review_store.reconcile(d, {}, {})
    assert reconciliation.missing_reviews(result, takes, "changed") == []
    assert reconciliation.missing_reviews(result, takes, "all") == ["clip_01"]


def test_read_only_reconcile_does_not_change_a_claimed_episode(tmp_path):
    d, current = setup_episode(tmp_path)
    path = d / "episode_review.json"
    before = path.read_bytes()
    result, _ = review_store.reconcile(d, evidence(current), {}, write=False)
    assert result["feedback"] == {} and path.read_bytes() == before


def test_inspection_does_not_count_old_candidates_as_confirmed_errors(tmp_path):
    d, _ = setup_episode(tmp_path)
    result, takes = review_store.reconcile(d, {}, {})
    counts = reconciliation.inspection_counts(result, takes, ["clip_01"])
    assert counts == {"total": 1, "passed": 0, "failed": 0, "unchecked": 1,
                      "unconfirmed_candidates": 1, "flash_pending": 0}


def test_inspection_counts_current_pass_failure_and_unknown_once_each(tmp_path):
    d, current = setup_episode(tmp_path)
    good, takes = review_store.reconcile(d, evidence(current), {})
    bad = reconciliation.verdict_from_record(next(iter(evidence(current, verdict="obvious").values())))
    good["clips"]["clip_02"] = bad
    takes["clip_02"] = current
    counts = reconciliation.inspection_counts(good, takes, ["clip_01", "clip_02", "clip_03"])
    assert (counts["passed"], counts["failed"], counts["unchecked"]) == (1, 1, 1)
    assert sum(counts[k] for k in ["passed", "failed", "unchecked"]) == counts["total"]


def test_inspection_requires_a_matching_take_even_if_a_verdict_remains(tmp_path):
    d, current = setup_episode(tmp_path)
    result, takes = review_store.reconcile(d, evidence(current), {})
    takes["clip_01"] = {**current, "take": [0, 0, 0]}
    assert reconciliation.inspection_counts(result, takes, ["clip_01"])["unchecked"] == 1
    assert reconciliation.inspection_counts(result, {}, ["clip_01"])["unchecked"] == 1
