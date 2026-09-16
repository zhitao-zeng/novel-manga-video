"""Correct source attribution, then review existing footage before ordering a retake."""
from __future__ import annotations

import copy
import fcntl
import json
from pathlib import Path
import time

from novel_manga.repair.proposal import RepairProposal
from repair_publication_thin import publish_candidate, publish_source_review
from novel_manga.util import atomic_write_json
from novel_manga.runtime_backends import normalize_text
from repair_review_thin import CurrentVerifier, current_takes, read
from repair_history import accepted_clip_material, begin_trial


class SourceVerifier(CurrentVerifier):
    def __init__(self, *args, proposed_plan: dict, current_videos: dict, **kwargs):
        self.proposed_plan = proposed_plan
        self.current_videos = current_videos
        super().__init__(*args, repair_advice=False, **kwargs)

    def load(self, path: Path):
        if Path(path).name == 'clip_plan.json':
            return copy.deepcopy(self.proposed_plan)
        return super().load(path)

    def video_of(self, ep_dir, cid, review_clip):
        current = self.current_videos.get(cid)
        return Path(current['video']) if current else None

    def prompt_for(self, clip, ep_dir, chapter, claim):
        cards, text = super().prompt_for(clip, ep_dir, chapter, '')
        text += ('\n本次按原文重新核验当前视频。上面的台词说话者已核验，台词可以是原文的压缩或改写，'
                 '也可以是在场人物对原文明写事实的简短口头表达；后者不等于原著逐字发言。'
                 '原文是事实依据；旧剧本或旧生成请求若与原文相反，不得要求画面遵从旧错误。'
                 '只看当前画面是否违背原文事实，正常的镜头角度、措辞压缩不算错误。')
        return cards, text


def prepare_source_recheck(directory: Path, targets: list[str] | None = None, *, instructions: dict | None = None) -> dict:
    from plan_chapter_thin import load_entity_index, mentioned_characters, ledger_cast
    from repair_flow_thin import speaker_contract, repair_episode, source_identities, source_passage
    from build_h3_prompts import convert
    from thin_profile import h3_prompt_outdated
    from clip_readiness import plan_issues
    from novel_manga.media.common import reference_digests
    from thin_review import verify_to_verdict
    from h3_request_checks import request_issues,source_crowds

    novel = directory.parent
    instructions = {**read(directory/'source_recheck_instructions.json',{}),
                    **{key:value for key,value in (instructions or {}).items() if value}}
    episode = int(directory.name.rsplit('_',1)[1])
    wanted = set(targets if targets is not None else read(directory / 'source_recheck_targets.json', []))
    before = read(directory / 'clip_plan.json', {})
    script = read(directory / 'chapter_script.json', {})
    bible = read(novel / 'story_bible.json', {})
    by_index = {s.get('index', i):s for i,s in enumerate(script.get('shots', []),1)}
    segments = {str(s['segment_id']):s['text'] for s in read(directory / 'segments.json', [])}
    names_all = [c['name'] for c in bible.get('characters', [])]
    from story_identity import IdentityCatalog, resolve_chapter
    identity_reading = resolve_chapter(directory)
    from dialogue_binding import apply_confirmed_speakers
    protected_bindings = apply_confirmed_speakers(directory, script['shots'])
    catalog = IdentityCatalog(novel)
    load_entity_index(novel, episode)
    present = ledger_cast(novel, episode)
    contracts = read(directory / 'source_speaker_contract.json', [])
    merged = {**{(r['stage'],r['turn']):r for r in contracts}, **protected_bindings}
    source_issues, resolved = {}, {}
    for clip in before.get('clips', []):
        cid = clip['clip_id']
        if cid not in wanted:
            continue
        # Speaker identity may be established well before a late split part.
        # Keep the full chapter for attribution; picture rewriting stays local.
        passage = '\n'.join(segments.values())
        resolved_names = [identity_reading['entities'].get(m['entity_id']) for m in identity_reading['mentions']
                          if m.get('presence') in {'on_stage','voice'} and m['entity_id'] != 'UNKNOWN']
        names = list(dict.fromkeys([*(n for n in resolved_names if n in names_all), *mentioned_characters(passage, names_all),
                                    *(n for n,p in present.items() if p in {'on_stage','voice'} and n in names_all),
                                    *(n for n in clip.get('cast',[]) if n in names_all)]))
        if any(t.get('speaker_name')=='无名群声' and t.get('delivery_mode')=='offscreen_dialogue' for t in clip.get('lines',[])):
            names = list(dict.fromkeys([*names,'无名群声']))
        identities = source_identities(names,bible,passage,context=identity_reading,catalog=catalog)
        shots = [{**by_index[i], 'origin_index':i} for i in dict.fromkeys(clip.get('shot_indexes',[])) if i in by_index]
        evidence = []
        speakers = speaker_contract(passage,shots,names,identities,list(merged.values()),evidence,identity_context=identity_reading)
        required = {(s['origin_index'],i) for s in shots for i,t in enumerate(s.get('turns',[]),1)
                    if t.get('delivery_mode') in {'visible_dialogue','offscreen_dialogue'} and t.get('text')}
        if required - speakers.keys():
            raise ValueError(f'{cid}: source attribution unresolved for turns {sorted(required - speakers.keys())}')
        merged.update({(r['stage'],r['turn']):r for r in evidence})
        resolved[cid] = evidence
        source_issues[cid] = ('已核验原文归属或有事实依据的对白改编（看 relation，改编不冒充原文发言）：' + json.dumps(evidence,ensure_ascii=False)
                             + '。按这些事实纠正说话者、对应动作和画面。旧判官可能是以错误剧本为标准，不要照抄旧的换人要求。'
                             + str((instructions or {}).get(cid,'')))
        print(f'{cid}: source attribution verified for {len(speakers)} dialogue turns',flush=True)
    if not source_issues:
        return {'changed': [], 'accepted': [], 'why':'no current source targets'}
    atomic_write_json(directory / 'source_speaker_contract.json',list(merged.values()))
    result = repair_episode(novel,episode,False,reframe=True,source_issues=source_issues,return_proposal=True)
    candidate = RepairProposal.from_result(result)
    proposal = candidate.payload if candidate.available else None
    if not proposal:
        raise ValueError(f'source plan proposal incomplete: {result.get("why")}')
    plan, notes = proposal['plan'], proposal['notes']
    from single_card_plan import single_card_plan
    single_card_plan(plan,wanted)
    # An already correct request still needs source-based review: a no-op
    # picture rewrite is not a failed preparation or evidence the video is bad.
    checked = sorted(wanted)
    structural = proposal.get('structural_repair') or {}
    if structural or set(plan_issues(plan,proposal['script'])) & set(result['changed']):
        from repair_blocked_plan import repack
        if not structural:
            plan, structural = repack(directory, plan, proposal['script'])
        changed = list(dict.fromkeys([*result['changed'], *(cid for g in structural['groups'] for cid in [*g['old'], *g['new']])]))
        notes = {cid:note for cid,note in notes.items() if cid not in changed}
        # A changed cut cannot reuse a source verdict about the old clip ID.
        # Its dialogue is preserved by repack; its new footage must be reviewed.
        candidate.plan, candidate.notes = plan, notes
        publish_candidate(directory, candidate, 'source_repack', changed, changes=structural)
        report={'changed':changed,'accepted':[],'needs_render':changed,'source_attribution':resolved,'structural_repair':structural}
        atomic_write_json(directory/'source_recheck_report.json',report)
        return report
    print(f'source plan prepared: {result["changed"]}',flush=True)
    clips = {c['clip_id']:c for c in plan['clips']}
    old_clips = {c['clip_id']:c for c in before['clips']}
    for cid in checked:
        clip = clips[cid]
        passage='\n'.join(segments.get(str(s),'') for s in clip.get('segment_ids',[]))
        crowds=source_crowds(clip,bible,passage)
        if crowds:
            clip['crowd_roles']=crowds
        from build_clip_plan_thin import nonverbal_sound
        old_words = ''.join(t.get('text','') for t in old_clips[cid].get('lines',[]) if not nonverbal_sound(t))
        new_words = ''.join(t.get('text','') for t in clip.get('lines',[]) if not nonverbal_sound(t))
        if normalize_text(new_words) != normalize_text(old_words):
            raise ValueError(f'{cid}: source repair changed the adapted dialogue wording')
        convert(clip,note=str(notes.get(cid,'')))
        if h3_prompt_outdated(clip,str(notes.get(cid,''))) or request_issues(clip):
            raise ValueError(f'{cid}: corrected English request not ready')
        print(f'{cid}: English request prepared; reviewing existing video',flush=True)
    state = novel / 'repair_manager'
    media = read(directory / 'thin_media_report.json', {})
    selected = {c['clip_id']:c.get('selected') or {} for c in media.get('clips', [])}
    actual = current_takes(directory,before,read(directory/'episode_review.json',{}))
    verifier = SourceVerifier(novel,state/'source_recheck'/'records.jsonl','source_recheck',1,
                              proposed_plan=plan,current_videos=actual,max_tokens=2200)
    acceptances = read(directory / 'source_acceptances.json', {})
    records, accepted = [], []
    for cid in checked:
        acceptances.pop(cid,None)
        record = verifier.verify((episode,cid,'','source_confirm'))
        now = current_takes(directory,before,read(directory/'episode_review.json',{})).get(cid)
        if ('error' in record or now != actual.get(cid) or not now
                or record.get('video') != now['video'] or record.get('take') != now['take']):
            raise ValueError(f'{cid}: source recheck failed or video changed: {record.get("error", "changed take")}')
        record['source_confirmed_at'] = time.strftime('%F %T')
        record['source_attribution'] = resolved.get(cid, [])
        records.append(record)
        print(f'{cid}: existing video source verdict={record.get("verdict")}',flush=True)
        answer = {**record,'people':[p if isinstance(p,dict) else {'who':p} for p in record.get('people',[])]}
        from thin_profile import speech_gate_result
        if verify_to_verdict(answer)['story_ok'] and speech_gate_result(novel,selected.get(cid,{}),directory).get('passed'):
            references = tuple(novel / r['path'] for r in clips[cid].get('references',[]) if r.get('role') != 'voice')
            if not all(p.is_file() for p in references):
                raise ValueError(f'{cid}: reference asset missing during source acceptance')
            acceptances[cid] = {'clip':accepted_clip_material(clips[cid]),'note':str(notes.get(cid,'')),
                                **now,'reference_digests':reference_digests(references),
                                'source_confirmed_at':record['source_confirmed_at']}
            accepted.append(cid)
        else:
            clips[cid]['repair_take']=int(old_clips[cid].get('repair_take',0))+1
    candidate.plan, candidate.notes = plan, notes
    report={'changed':checked,'accepted':accepted,'needs_render':[cid for cid in checked if cid not in accepted],
            'source_attribution':resolved,'reviews':records}
    publish_source_review(directory, candidate, checked, acceptances, records, report)
    return report
