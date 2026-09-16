"""Remove old expression-sheet references without changing a clip's story or cuts."""
import re
import copy
import time
from pathlib import Path


def single_card_plan(plan: dict, clip_ids: set[str] | None = None) -> list[str]:
    changed = []
    for clip in plan.get("clips", []):
        if clip_ids is not None and clip['clip_id'] not in clip_ids:
            continue
        refs = clip.get("references", [])
        removed = [r for r in refs if r.get("role") == "character" and Path(r.get("path", "")).name == "expressions.jpeg"]
        if not removed:
            continue
        retained = [r for r in refs if r not in removed]
        primary = {(r.get("name"), r.get("asset_id")): r for r in retained
                   if r.get("role") == "character" and Path(r.get("path", "")).name == "turnaround.jpeg"}
        # Resolve mappings before mutating tags: picture 10 must not be rewritten
        # as picture 1 followed by a zero, or remapped twice after renumbering.
        tags = {}
        index = 0
        for ref in retained:
            if ref.get("role") in {"character", "location"}:
                index += 1
                tags[ref["tag"]] = f"@图片{index}"
        for ref in removed:
            owner = primary.get((ref.get("name"), ref.get("asset_id")))
            if owner is None:
                raise ValueError(f"{clip.get('clip_id')}: expression sheet has no main character card: {ref.get('name')}")
            tags[ref["tag"]] = tags[owner["tag"]]
        for ref in retained:
            if ref.get("tag") in tags:
                ref["tag"] = tags[ref["tag"]]
        prompt = re.sub(r"@图片\d+", lambda m: tags.get(m[0], m[0]), clip.get("prompt", ""))
        prompt = re.sub(r"(@图片\d+)和\1(?!\d)", r"\1", prompt)
        lines = []
        for line in prompt.splitlines():
            if line.startswith("【人物】"):
                line = line.replace("这两张图", "该图").replace("两张都", "该图").replace("两张图", "该图")
            lines.append(line)
        clip["references"] = retained
        clip["prompt"] = "\n".join(lines)
        clip["prompt_chars"] = len(clip["prompt"])
        # Reconvert just these clips: old <Picture N> bindings refer to the old
        # reference order and cannot be reused as-is.
        clip.pop("prompt_h3", None)
        clip.pop("prompt_h3_of", None)
        changed.append(clip["clip_id"])
    return changed


def missing_expression_clips(directory: Path, plan: dict) -> set[str]:
    return {clip['clip_id'] for clip in plan.get('clips',[]) if any(
        ref.get('role')=='character' and Path(ref.get('path','')).name=='expressions.jpeg'
        and not (directory.parent/ref['path']).is_file() for ref in clip.get('references',[]))}


def repair_missing_expressions(directory: Path) -> dict:
    """Remove missing secondary-card dependencies; retain currently approved footage.

    Caller owns the episode. Only reference metadata changes: no new source
    verdict is invented, and a known bad or unreviewed video is never accepted.
    """
    from novel_manga.util import read_json as read
    from review_store_thin import current_takes
    from repair_history import accepted_clip_material, begin_trial, load, save, refresh_prepared_plan, ERROR_FIELDS
    from build_h3_prompts import convert
    from thin_profile import h3_prompt_outdated
    from novel_manga.media.common import reference_digests
    from clip_readiness import inspect_episode,save_check
    from novel_manga.util import atomic_write_json
    plan=read(directory/'clip_plan.json',{});targets=missing_expression_clips(directory,plan)
    if not targets:
        return {'changed':[],'retained':[]}
    updated=copy.deepcopy(plan);changed=single_card_plan(updated,targets)
    review=read(directory/'episode_review.json',{});takes=current_takes(directory,plan,review)
    notes=read(directory/'review_feedback.json',{});acceptances=read(directory/'source_acceptances.json',{})
    retained=[];english_pending=[]
    for clip in updated['clips']:
        cid=clip['clip_id']
        if cid not in changed:
            continue
        convert(clip,note=str(notes.get(cid,'')))
        if h3_prompt_outdated(clip,str(notes.get(cid,''))):
            english_pending.append(cid);continue
        row=review.get('clips',{}).get(cid,{})
        verdict=row.get('verify') or {};take=takes.get(cid)
        refs=[directory.parent/ref['path'] for ref in clip.get('references',[]) if ref.get('role')!='voice']
        if (take and row.get('video')==take['video'] and row.get('take')==take['take']
                and row.get('story_ok') is True and not row.get('technical') and not row.get('flash_pending')
                and verdict.get('verdict') in {'fine','subtle'} and not any(verdict.get(k) for k in ERROR_FIELDS)
                and all(p.is_file() for p in refs)):
            acceptances[cid]={'clip':accepted_clip_material(clip),'note':str(notes.get(cid,'')),**take,
                              'reference_digests':reference_digests(refs),'accepted_at':time.strftime('%F %T'),
                              'reason':'removed missing expression reference only; current video already passed precise review',
                              'existing_review':verdict}
            retained.append(cid)
    begin_trial(directory,set(changed),'drop_missing_expression_refs',after_plan=updated,after_notes=notes,
                changes={'removed_missing_expression_refs':changed,'retained_current_approved':retained})
    record=load(directory);record['trials'][-1]['managed']=True;save(directory,record)
    refresh_prepared_plan(directory,plan,updated,notes)
    atomic_write_json(directory/'clip_plan.json',updated)
    atomic_write_json(directory/'source_acceptances.json',acceptances)
    _,blocked=inspect_episode(directory,assets=True);save_check(directory,updated,blocked)
    result={'changed':changed,'retained':retained,'english_pending':english_pending}
    atomic_write_json(directory/'missing_expression_repair.json',result)
    return result
