"""Existing host acceptance follows the selected takes, including review before encoding."""
import os
import sys
import time

import pytest

from novel_manga.application.production import delivery
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.review.contracts import REVIEW_POLICY
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json, read_json


@pytest.mark.parametrize('case,expected', [
    ('review_before_encoding', True), ('changed_take', False),
    ('recorded_acceptance', True), ('old_unaccepted', False),
    ('missing_take_proof', False), ('pending_publication', False),
])
def test_delivery_uses_current_take_review_and_existing_acceptance(tmp_path, monkeypatch, case, expected):
    book = tmp_path/'book';ep=book/'book_1';ep.mkdir(parents=True)
    atomic_write_json(book/'profile.json', {'frame':'16:9','style':'2d','tier':'fast'})
    clip = {'clip_id':'clip_01','kind':'video','prompt':'Existing request','references':[]}
    plan = {'clips':[clip]};atomic_write_json(ep/'clip_plan.json', plan)
    video=ep/'work/clips/clip_01/attempt_01/clip.mp4';video.parent.mkdir(parents=True)
    video.write_bytes(b'original selected take')
    final=ep/'book_1.mp4';final.write_bytes(b'encoded from the reviewed take')
    media={'clip_plan_fingerprint':plan_fingerprint(plan),'review_feedback':{},
           'clips':[{'clip_id':'clip_01','selected':{'video':str(video),'passed':True,'chunks':[]}}],
           'failed_clips':[],'gate_failed_clips':[],
           'assembly':{'thin_passed':True,'pending_publish':False},
           'quality_review':{'passed':True}}
    review={'policy':REVIEW_POLICY,'review_mode':'verify','feedback':{},
            'clips':{'clip_01':{'video':str(video),'take':take_identity(video),
                                'severity':'pass','verify':{'verdict':'fine'}}}}
    if case in {'recorded_acceptance','old_unaccepted','missing_take_proof'}:
        review['policy']='thin-review-v1.22'
    if case != 'old_unaccepted':atomic_write_json(ep/'episode_execution.json',{'passed':True})
    if case=='missing_take_proof':review['clips']['clip_01'].pop('take')
    if case=='pending_publication':media['assembly']['pending_publish']=True
    atomic_write_json(ep/'thin_media_report.json', media)
    atomic_write_json(ep/'episode_review.json', review)
    stamp=time.time();os.utime(ep/'episode_review.json',(stamp-20,stamp-20))
    os.utime(final,(stamp-10,stamp-10))
    if case=='changed_take':video.write_bytes(b'unreviewed replacement take')
    monkeypatch.setattr(delivery,'lane_is_h3',lambda novel:True)
    monkeypatch.setattr(sys,'argv',['delivery_gate','--novel-dir',str(book),'--quiet'])
    assert delivery.main()==0
    assert read_json(book/'delivery.json')['episodes'][0]['deliverable'] is expected
