"""Pure speech evaluation; model/FFmpeg execution and record writes stay in analysis."""
from __future__ import annotations
import re
from functools import lru_cache
from pypinyin import lazy_pinyin
from novel_manga.media.evidence_backends import correct_protected_lexicon, edit_distance
from .subtitles import match_key, subsequence_overlap, align_chunks
from .issues import QualityIssue, missing_dialogue, replaced_by_speech_recheck

MAX_MISSING = 0.5
MIN_PEAK_DB = -35.0
UNSCRIPTED_MIN_CHARS = 5
UNSCRIPTED_MIN_DB = -40.0
EVALUATION_POLICY = 8
RECHECK_POLICY = 5


@lru_cache(maxsize=1024)
def phonemes(text):
    return tuple(lazy_pinyin(text, errors=lambda value: list(value)))


def tolerance(reference, hypothesis, spoken, mean_db, peak_db):
    expected, heard = match_key(reference), match_key(spoken)
    equivalent = bool(expected and heard and len(expected) == len(heard)
                      and expected != heard and phonemes(expected) == phonemes(heard))
    audible = peak_db is not None and peak_db >= MIN_PEAK_DB and mean_db is not None and mean_db > UNSCRIPTED_MIN_DB
    cry = bool(re.fullmatch(r'[啊呀哎唉哦噢呃]{1,3}', expected)) and audible and not heard
    warnings = []
    if equivalent:
        warnings.append('exact_phonetic_match_raw_spelling_differs')
    if re.search(r'[哈呵嘿嘻]{3,}', hypothesis):
        warnings.append('nonverbal_laughter')
    if cry and not heard:
        warnings.append('audible_nonlexical_exclamation_not_transcribed')
    return equivalent, cry, warnings


def planned_vocalizations(clip):
    """Only explicit authored sound events authorize nonverbal ASR chunks."""
    result, start = [], 0.0
    sounds, timing = clip.get('shot_sound') or [], clip.get('shot_timing') or []
    if len(sounds) != len(timing):
        return result
    for cue, stage in zip(sounds, timing):
        end = start + float(stage['seconds'])
        if re.search(r'笑声|大笑|干笑|狂笑|放声笑|\b(?:laughter|laughing|chuckl\w*)\b', str(cue), re.I):
            result.append({'kind': 'laughter', 'start': start, 'end': end, 'cue': cue})
        start = end
    return result


def speech_rows(rows, vocalizations):
    """Retain mixed words and unplanned sounds; separate only pure planned laughter."""
    words, nonverbal = [], []
    for row in rows:
        key = match_key(str(row.get('hypothesis', '')))
        allowed = re.fullmatch(r'[哈呵嘿嘻]{2,}', key) and any(
            event['kind'] == 'laughter' and 'start' in row and 'end' in row
            and float(row['start']) >= event['start'] - .35
            and float(row['end']) <= event['end'] + .35 for event in vocalizations)
        # Repeated laugh syllables carry emotion rather than new lexical dialogue.
        # Keep the original chunk as evidence, including laughter embedded in speech.
        cleaned = re.sub(r'[哈呵嘿嘻]{3,}', '', str(row.get('hypothesis', '')))
        if allowed or cleaned != row.get('hypothesis', ''):
            nonverbal.append(row)
            if not allowed and match_key(cleaned):
                words.append({**row, 'hypothesis': cleaned})
        else:
            words.append(row)
    return words, nonverbal


def unplanned_segments(reference, rows):
    timed = [r for r in rows if 'start' in r and 'end' in r]
    if not reference or not timed:
        return []
    # Subtitle suppression is not a speech-quality decision. Consecutive unmatched
    # chunks form one extra utterance even when ASR split it into short fragments.
    found, pending = [], []
    def flush():
        if (sum(len(match_key(str(r.get('hypothesis', '')))) for r in pending) >= 6
                and sum(float(r['end'])-float(r['start']) for r in pending) >= 0.8):
            found.extend(pending)
        pending.clear()
    for row, text, score, pieces in align_chunks([reference], timed):
        if text:
            flush()
        elif match_key(str(row.get('hypothesis', ''))):
            pending.append(row)
    flush()
    return found


def corrected_rows(rows, reference, protected_terms, aliases):
    # ASR may split a protected name from the dialogue anchor that verifies it.
    # Use the retained neighboring words, then apply only replacements wholly
    # inside one chunk. Keep timing and raw transcription as independent evidence.
    joined = ''.join(row.get('raw_hypothesis', row['hypothesis']) for row in rows)
    _, contextual = correct_protected_lexicon(joined, reference, protected_terms, aliases)
    contextual = [change for change in contextual if change.get('method') == 'phonetic_with_context'
                  and joined.count(change['source']) == 1]
    result = []
    for row in rows:
        raw = row.get('raw_hypothesis', row['hypothesis'])
        corrected, corrections = correct_protected_lexicon(raw, reference, protected_terms, aliases)
        for change in contextual:
            if change['source'] in corrected:
                corrected = corrected.replace(change['source'], change['canonical'], 1)
                corrections.append(change)
        result.append({**row, 'raw_hypothesis': raw, 'hypothesis': corrected, 'corrections': corrections})
    return result


def evaluate(reference, rows, mean_db, peak_db, *, vocalizations=()):
    hypothesis = "".join(row["hypothesis"] for row in rows)
    word_rows, nonverbal = speech_rows(rows, vocalizations)
    spoken = ''.join(row['hypothesis'] for row in word_rows)
    reference_key = match_key(reference)    # numbers in spoken form: 50万 and 五十万 agree
    hypothesis_key = match_key(hypothesis)
    cer = round(edit_distance(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
    # CER punishes what the model ADDED (an ad-lib, a chuckle, a stage direction
    # it read out) as much as what it dropped, and 12 of 14 gate failures in the
    # first 46 episodes were of that kind - the lines were spoken.  The gate
    # judges the share of the script that was never heard, in order.
    missing = round(1.0 - subsequence_overlap(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
    equivalent, cry, warnings = tolerance(reference, hypothesis, spoken, mean_db, peak_db)
    if equivalent:
        missing = 0.0
    issues = []
    if reference_key:
        if (not hypothesis_key or peak_db is None or peak_db < MIN_PEAK_DB) and not cry:
            issues.append(QualityIssue.VOICE_ENERGY_MISSING.code)
        if missing > MAX_MISSING and not cry:
            issues.append(missing_dialogue(missing, MAX_MISSING))
        if not equivalent and unplanned_segments(reference, word_rows):
            issues.append(QualityIssue.EXCESS_UNPLANNED_SPEECH.code)
    elif len(match_key(spoken)) >= UNSCRIPTED_MIN_CHARS and (mean_db or -99) > UNSCRIPTED_MIN_DB:
        issues.append(QualityIssue.UNSCRIPTED_SPEECH.code)
    result = {
        "reference": reference, "hypothesis": hypothesis, "cer": cer, "missing": missing, "mean_volume_db": mean_db, "max_volume_db": peak_db,
        "chunks": rows, "issues": issues, "passed": not issues, "speech_evaluation_policy": EVALUATION_POLICY,
        "planned_vocalizations": list(vocalizations), "nonverbal_chunks": nonverbal,
        "speech_hypothesis": spoken,
        'phonetic_equivalent': equivalent, 'warnings': warnings,
    }
    return result


def recheck(reference, analysis, protected_terms, aliases, *, vocalizations=()):
    chunks = corrected_rows(analysis.get('chunks') or [], reference, protected_terms, aliases)
    raw = ''.join(row['raw_hypothesis'] for row in chunks) if chunks else str(analysis.get('hypothesis') or '')
    hypothesis = ''.join(row['hypothesis'] for row in chunks) if chunks else correct_protected_lexicon(raw, reference, protected_terms, aliases)[0]
    word_rows, nonverbal = speech_rows(chunks, vocalizations)
    spoken = ''.join(row['hypothesis'] for row in word_rows) if chunks else hypothesis
    expected, heard = match_key(reference), match_key(hypothesis)
    missing = round(1 - subsequence_overlap(expected, heard) / max(1, len(expected)), 4) if expected else 0
    equivalent, cry, warnings = tolerance(reference, hypothesis, spoken,
                                        analysis.get('mean_volume_db'), analysis.get('max_volume_db'))
    if equivalent:
        missing = 0.0
    issues = [i for i in analysis.get('issues') or [] if not replaced_by_speech_recheck(i)]
    if expected:
        if (not heard or analysis.get('max_volume_db') is None or analysis['max_volume_db'] < MIN_PEAK_DB) and not cry:
            issues.append(QualityIssue.VOICE_ENERGY_MISSING.code)
        if missing > MAX_MISSING and not cry:
            issues.append(missing_dialogue(missing, MAX_MISSING))
        if len(match_key(spoken)) - len(expected) > max(12, len(expected) * 2):
            issues.append(QualityIssue.EXCESS_UNPLANNED_SPEECH.code)
        if not equivalent and unplanned_segments(reference, word_rows):
            issues.append(QualityIssue.EXCESS_UNPLANNED_SPEECH.code)
    if re.search(r'keep\s*everything\s*above|this\s*is\s*take|spoken\s*clearly\s*and\s*completely', raw, re.I):
        issues.append(QualityIssue.DIRECTOR_INSTRUCTION_SPOKEN.code)
    result = {**analysis, 'reference': reference, 'hypothesis': hypothesis, 'chunks': chunks or analysis.get('chunks', []),
              'missing': missing, 'issues': list(dict.fromkeys(issues)), 'passed': not issues,
              'speech_recheck_policy': RECHECK_POLICY, 'planned_vocalizations': list(vocalizations),
              'nonverbal_chunks': nonverbal, 'speech_hypothesis': spoken,
              'phonetic_equivalent': equivalent, 'warnings': warnings}
    return result


def refresh(reference, analysis, protected_terms, aliases, *, vocalizations=()):
    """Re-evaluate retained ASR/levels under current rules, without a new recording."""
    from .issues import is_speech_issue
    rows = corrected_rows(analysis.get('chunks') or [], reference, protected_terms, aliases)
    if not rows and analysis.get('hypothesis'):
        rows = [{'hypothesis': analysis['hypothesis']}]
    checked = evaluate(reference, rows, analysis.get('mean_volume_db'), analysis.get('max_volume_db'),
                       vocalizations=vocalizations)
    retained = [i for i in analysis.get('issues') or [] if not is_speech_issue(i)]
    checked['issues'] = list(dict.fromkeys([*retained, *checked['issues']]))
    checked['passed'] = not checked['issues']
    merged = recheck(reference, {**analysis, **checked}, protected_terms, aliases, vocalizations=vocalizations)
    if 'speech_issues' in analysis:
        merged['speech_issues'] = [i for i in merged['issues'] if is_speech_issue(i)]
    return merged
