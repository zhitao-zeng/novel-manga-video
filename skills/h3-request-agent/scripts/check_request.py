"""Stdlib-only H3 request contract feedback for an isolated agent workspace."""
import json,re,sys
from pathlib import Path

HEADS=('subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music')

def normalized(text):
    return ''.join(c for c in text.casefold() if c.isalnum() or '\u3400'<=c<='\u9fff')

def check(case,answer):
    errors=[]
    if answer.get('case_id')!=case['case_id']:errors.append('case_id differs from the supplied case')
    if answer.get('status') not in {'ready','blocked'}:errors.append('status must be ready or blocked')
    if answer.get('status')=='blocked':
        if not answer.get('problems'):errors.append('blocked needs specific problems')
        if answer.get('prompt'):errors.append('blocked must not present a final prompt')
        return errors
    if not isinstance(answer.get('review'),dict):errors.append('review must record source and actual-reference observations')
    text=answer.get('prompt') or ''
    matches=list(re.finditer(r'(?m)^('+'|'.join(HEADS)+r'):\s*$',text))
    if tuple(m[1] for m in matches)!=HEADS:
        return errors+['six section headings must occur exactly once, in the supplied order']
    sections={m[1]:text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip() for i,m in enumerate(matches)}
    if re.search(r'[\u4e00-\u9fff]',re.sub(r'<d>.*?</d>','',text,flags=re.S)):
        errors.append('Chinese outside protected dialogue')
    for kind,valid in [('Picture',case['picture_ids']),('Audio',case['audio_ids']),('Subject',case['subject_ids'])]:
        actual={int(n) for n in re.findall(r'<'+kind+r' (\d+)>',text)}
        if actual-set(valid):errors.append(kind+' tags refer to unavailable identifiers: '+str(sorted(actual-set(valid))))
    declarations={int(n) for n in re.findall(r'(?m)^<Subject (\d+)>',sections['subject_definitions'])}
    if declarations!=set(case['subject_ids']):errors.append('subject definitions do not preserve the supplied subject set')
    for binding in case['reference_bindings']:
        pattern=r'(?m)^<Subject '+str(binding['subject'])+r'>[^\n]*'
        row=re.search(pattern,sections['subject_definitions'])
        if not row or any(f'<Picture {n}>' not in row[0] for n in binding['pictures']):
            errors.append('subject/picture association missing for '+str(binding['subject']))
    blocks=list(re.finditer(r'\[Shot (\d+)\](.*?)(?=\[Shot \d+\]|\Z)',sections['detailed_description'],re.S))
    if [int(b[1]) for b in blocks]!=list(range(1,len(case['shots'])+1)):
        errors.append('shot count/order differs from the approved shots')
    actual=[]
    for b in blocks:
        speeches=re.finditer(r'(?:(?:<Subject (\d+)>)|(?:An off-screen voice))\s+\(S(\d+)\)\s+says'
            r'( in an off-screen voiceover)?\s*<d>\[Chinese\]\s*(.*?)</d>',b[2],re.S)
        actual += [(int(b[1]),int(m[1]) if m[1] else None,int(m[2]),bool(m[3]) or m[1] is None,normalized(m[4])) for m in speeches]
    expected=[(d['stage'],d['subject'],d['speaker_id'],d['offscreen'],normalized(d['text'])) for d in case['dialogue']]
    if actual!=expected:errors.append('utterance order, words, speaker, visibility or shot ownership changed')
    if text.count('<d>')!=len(actual):errors.append('unbound speech outside the identified dialogue clauses')
    for b,shot in zip(blocks,case['shots']):
        required=shot.get('timing_clause')
        if required and required not in b[2]:errors.append('protected timing missing from shot '+b[1])
        if not required and re.search(r'Planned duration:|\bAt\s+\d',b[2]):
            errors.append('shot '+b[1]+' invents a numeric schedule absent from the approved shot')
    return errors

def main():
    manifest=Path(sys.argv[1]);target=Path(sys.argv[2]);data=json.loads(manifest.read_text())
    answer=json.loads(target.read_text());entry=next(c for c in data['cases'] if c['case_id']==answer.get('case_id'))
    case=json.loads(Path(entry['path']).read_text());errors=check(case,answer)
    report={'case_id':case['case_id'],'status':answer.get('status'),'passed':not errors,'errors':errors}
    print(json.dumps(report,ensure_ascii=False));return 1 if errors else 0

if __name__=='__main__':raise SystemExit(main())
