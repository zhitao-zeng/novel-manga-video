"""Shared managed episode regression fixtures."""
from novel_manga.media import cache
import novel_manga.story.source_identity as source_identity_rules
import novel_manga.application.repair.judges as repair_judges
import novel_manga.application.repair.manager_dispatch as repair_manager_dispatch
import novel_manga.application.repair.manager_workers as repair_manager_workers
from support.render_context import uninitialized_runner
import copy
import hashlib
import json
from pathlib import Path
import pytest
import novel_manga.application.repair.managed as managed
import novel_manga.application.repair.history as history
import novel_manga.application.repair.flow as repair
import novel_manga.repair.scheduling as schedule_rules
import novel_manga.application.repair.manager_flow as repair_manager_flow
from novel_manga.story.h3 import request_issues
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.review.storage import take_identity


def fixture_episode(tmp_path):
    d=tmp_path/'book'/'book_1';d.mkdir(parents=True)
    clips=[];reviews={};media=[]
    for cid in ['a','b']:
        video=d/f'{cid}.mp4';video.write_bytes(cid.encode())
        clips.append({'clip_id':cid,'kind':'video','prompt':'original','request_seconds':5,'references':[]})
        reviews[cid]={'video':str(video),'take':take_identity(video),'story_ok':False,'tier':'must_fix',
                      'verify':{'verdict':'obvious','same_person_twice':True,'evidence':'two identical people'},'feedback':'distinct people'}
        media.append({'clip_id':cid,'selected':{'video':str(video),'passed':True}})
    for filename,data in [('clip_plan.json',{'clips':clips}),('chapter_script.json',{'shots':[]}),('segments.json',[]),
                          ('review_feedback.json',{}),('episode_review.json',{'clips':reviews,'feedback':{'a':'wrong','b':'wrong'}}),
                          ('thin_media_report.json',{'clips':media})]:
        (d/filename).write_text(json.dumps(data))
    return d,clips,reviews

