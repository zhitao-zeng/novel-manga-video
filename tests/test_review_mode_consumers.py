"""Delivery and cached scheduling status honor the selected review method."""
from types import SimpleNamespace
import sys

from novel_manga.application.production import conductor_state, delivery
from novel_manga.review.contracts import POLICY
from novel_manga.util import atomic_write_json, read_json
from support.repair_history import episode


def test_scheduler_rechecks_mode_even_when_the_report_file_is_unchanged(tmp_path,monkeypatch):
    path=tmp_path/'book/book_1/episode_review.json'
    atomic_write_json(path,{'policy':POLICY,'review_mode':'classic','clips':{'a':{'severity':'pass'}}})
    conductor=SimpleNamespace(_summaries={})
    monkeypatch.setenv('NOVEL_REVIEW_MODE','classic')
    assert not conductor_state.review_incomplete(conductor,path)
    monkeypatch.setenv('NOVEL_REVIEW_MODE','verify')
    assert conductor_state.review_incomplete(conductor,path)


def test_delivery_requires_matching_mode_before_reporting_deliverable(episode,monkeypatch):
    directory,_,review,_=episode
    review.update(policy=POLICY,review_mode='classic',feedback={})
    review['clips']['clip_01'].update(severity='pass',story_ok=True,verify={'verdict':'fine'})
    atomic_write_json(directory/'episode_review.json',review)
    monkeypatch.setenv('NOVEL_REVIEW_MODE','verify')
    monkeypatch.setattr(delivery,'episode_status',lambda *a:'done')
    monkeypatch.setattr(delivery,'lane_is_h3',lambda *a:True)
    monkeypatch.setattr(sys,'argv',['delivery','--novel-dir',str(directory.parent),'--quiet'])
    delivery.main()
    report=read_json(directory.parent/'delivery.json')
    assert report['episodes'][0]['deliverable'] is False
    review['review_mode']='verify';atomic_write_json(directory/'episode_review.json',review)
    delivery.main()
    assert read_json(directory.parent/'delivery.json')['episodes'][0]['deliverable'] is True
