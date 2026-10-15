"""Current movie and approved input identity shared by both movie reviewers."""
from pathlib import Path
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json

POLICY='final-movie-v2-approved-postproduction-caption-origin'


def context(directory, media, movie):
    directory=Path(directory)
    plan=read_json(directory/'clip_plan.json',{})
    native=read_json(directory/'episode_review.json',{}).get('clips',{})
    return {'movie':take_identity(Path(movie)), 'plan':plan_fingerprint(plan),
            'h3':h3_prompt_fingerprint(plan),
            'script':read_json(directory/'chapter_script.json',{}),
            'bible':read_json(directory.parent/'story_bible.json',{}),
            'layout':read_json(directory.parent/'layout.json',{}),
            'notes':read_json(directory/'review_feedback.json',{}),
            'references':{ref['path']:take_identity(directory.parent/ref['path'])
                          for clip in plan.get('clips',[]) for ref in clip.get('references',[])},
            'native_judgments':{cid:{k:row.get(k) for k in ('verify','policy','review_mode','scripted','story_ok','story_issue','feedback')}
                                for cid,row in native.items()},
            'takes':{r['clip_id']:(take_identity(Path(r['selected']['video']))
                                  if (r.get('selected') or {}).get('video') else None)
                     for r in media.get('clips',[])},
            'speech_chunks':{r['clip_id']:(r.get('selected') or {}).get('chunks',[]) for r in media.get('clips',[])}}

