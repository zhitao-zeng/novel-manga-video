"""Current movie and approved input identity shared by both movie reviewers."""
from pathlib import Path
import json
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json

POLICY='final-movie-v2-approved-postproduction-caption-origin'


def context(directory, media, movie):
    directory=Path(directory)
    plan=read_json(directory/'clip_plan.json',{})
    native=read_json(directory/'episode_review.json',{}).get('clips',{})
    script=read_json(directory/'chapter_script.json',{})
    names=reference_names(plan)
    delivery=read_json(directory/'delivery_policy.json',{})
    audio=delivery.get('interaction_ambience')or{}
    return {'movie':take_identity(Path(movie)), 'plan':plan_fingerprint(plan),
            'delivery_policy':delivery,
            'ambience_asset':take_identity(Path(audio['path'])) if audio.get('path') else None,
            'clip_ambience_assets':{key:take_identity(Path(value['path']))
                                    for key,value in (delivery.get('clip_ambience')or{}).items()},
            'h3':h3_prompt_fingerprint(plan),
            'script':script,
            'bible':scoped_bible(read_json(directory.parent/'story_bible.json',{}), script, names),
            'reference_names':names,
            'layout':read_json(directory.parent/'layout.json',{}),
            'notes':read_json(directory/'review_feedback.json',{}),
            'references':{ref['path']:take_identity(directory.parent/ref['path'])
                          for clip in plan.get('clips',[]) for ref in clip.get('references',[])},
            'native_judgments':{cid:{k:row.get(k) for k in ('verify','policy','review_mode','scripted','story_ok','story_issue','feedback')}
                                for cid,row in native.items()},
            'takes':{r['clip_id']:(take_identity(Path(r['selected'].get('postmix_video') or r['selected']['video']))
                                  if (r.get('selected') or {}).get('video') else None)
                     for r in media.get('clips',[])},
            'speech_chunks':{r['clip_id']:(r.get('selected') or {}).get('chunks',[]) for r in media.get('clips',[])}}



def actual_speech(media):
    """Expose observed audio analysis, never substitute the planned reference text."""
    result = {}
    for row in media.get('clips', []):
        selected = row.get('selected') or {}
        if not selected:
            continue
        text = selected.get('speech_hypothesis')
        if not isinstance(text, str):
            text = selected.get('hypothesis')
        result[row['clip_id']] = {
            'recognized_text': text if isinstance(text, str) else '',
            'speech_gate_passed': selected.get('passed'),
            'issues': list(selected.get('issues') or []),
            'clip_relative_chunks': [{key: chunk[key] for key in ('start', 'end', 'hypothesis', 'raw_hypothesis')
                                     if key in chunk} for chunk in selected.get('chunks') or []],
        }
    return result

def assembly_inputs(plan, media):
    """Existing take identities in the actual story order, including postmixed takes."""
    selected = {row['clip_id']: row.get('selected') or {} for row in media.get('clips', [])}
    takes = []
    for clip in plan.get('clips', []):
        if clip.get('kind') != 'video':
            continue
        row = selected.get(clip['clip_id'], {})
        video = row.get('postmix_video') or row.get('video')
        takes.append([clip['clip_id'], take_identity(Path(video)) if video else None])
    return {'plan': plan_fingerprint(plan), 'h3': h3_prompt_fingerprint(plan), 'takes': takes}

def assembly_current(directory, media):
    """An old movie cannot stand in for missing or replaced current selected takes."""
    expected = assembly_inputs(read_json(Path(directory)/'clip_plan.json', {}), media)
    return bool(expected['takes'] and all(take for cid, take in expected['takes'])
                and (media.get('assembly') or {}).get('source_inputs') == expected)

def scoped_bible(bible, script, reference_names=()):
    """Retrieve episode material, without treating a textual mention as identity binding."""
    if not isinstance(bible, dict):
        return bible
    text = json.dumps([script, list(reference_names)], ensure_ascii=False)
    characters = [row for row in bible.get('characters', []) if row.get('name') and row['name'] in text]
    prop_text = text + json.dumps([r.get('signature_prop', '') for r in characters], ensure_ascii=False)
    return {**bible, 'characters': characters,
            'props': [row for row in bible.get('props', [])
                      if any(name and name in prop_text for name in [row.get('name', ''), *row.get('aliases', [])])],
            'locations': [row for row in bible.get('locations', []) if row.split('：', 1)[0] in text]}

def reference_names(plan):
    return sorted({r['name'] for c in plan.get('clips', []) for r in c.get('references', []) if r.get('name')})

def approved_source(script, plan):
    """Source shot addresses are current indices, not pre-packing clip label hints."""
    shots=[{**{k:v for k,v in row.items() if k not in {'label','clip_hint','origin_index'}},
            'index':row.get('index', index)}
           for index,row in enumerate(script.get('shots', []),1)]
    return {'script':{**script,'shots':shots},
            'clip_shots':{c['clip_id']:list(c.get('shot_indexes', []))
                          for c in plan.get('clips', []) if c.get('kind')=='video'}}

def same_evidence(left, right):
    """Native verdicts are checked separately; they are not new movie pictures."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    names = set(left.get('reference_names', [])) | set(right.get('reference_names', []))
    def relevant(value):
        result = {k: v for k, v in value.items() if k not in {'native_judgments', 'reference_names'}}
        if 'bible' in result:
            result['bible'] = scoped_bible(result['bible'], result.get('script', {}), names)
        return result
    return relevant(left) == relevant(right)
