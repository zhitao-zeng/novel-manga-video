"""Pure speech evaluation; model/FFmpeg execution and record writes stay in analysis."""
from __future__ import annotations
import re
from ..runtime_backends import correct_protected_lexicon, edit_distance
from .subtitles import match_key, subsequence_overlap, align_chunks, classify_unmatched
from .issues import QualityIssue, missing_dialogue, replaced_by_speech_recheck

MAX_MISSING = 0.5
MIN_PEAK_DB = -35.0
UNSCRIPTED_MIN_CHARS = 5
UNSCRIPTED_MIN_DB = -40.0
EVALUATION_POLICY = 2


def unplanned_segments(reference, rows):
    timed = [r for r in rows if 'start' in r and 'end' in r]
    if not reference or not timed:
        return []
    return [row for row, text, score, pieces in align_chunks([reference], timed)
            if not text and classify_unmatched(str(row.get('hypothesis', '')),
                                               float(row['end']) - float(row['start'])) == 'asr_text']


def corrected_rows(rows, reference, protected_terms, aliases):
    result = []
    for row in rows:
        raw = row.get('raw_hypothesis', row['hypothesis'])
        corrected, corrections = correct_protected_lexicon(raw, reference, protected_terms, aliases)
        result.append({**row, 'raw_hypothesis': raw, 'hypothesis': corrected, 'corrections': corrections})
    return result


def evaluate(reference, rows, mean_db, peak_db):
    hypothesis = "".join(row["hypothesis"] for row in rows)
    reference_key = match_key(reference)    # numbers in spoken form: 50万 and 五十万 agree
    hypothesis_key = match_key(hypothesis)
    cer = round(edit_distance(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
    # CER punishes what the model ADDED (an ad-lib, a chuckle, a stage direction
    # it read out) as much as what it dropped, and 12 of 14 gate failures in the
    # first 46 episodes were of that kind - the lines were spoken.  The gate
    # judges the share of the script that was never heard, in order.
    missing = round(1.0 - subsequence_overlap(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
    issues = []
    if reference_key:
        if not hypothesis_key or peak_db is None or peak_db < MIN_PEAK_DB:
            issues.append(QualityIssue.VOICE_ENERGY_MISSING.code)
        if missing > MAX_MISSING:
            issues.append(missing_dialogue(missing, MAX_MISSING))
        if unplanned_segments(reference, rows):
            issues.append(QualityIssue.EXCESS_UNPLANNED_SPEECH.code)
    elif len(hypothesis_key) >= UNSCRIPTED_MIN_CHARS and (mean_db or -99) > UNSCRIPTED_MIN_DB:
        issues.append(QualityIssue.UNSCRIPTED_SPEECH.code)
    result = {
        "reference": reference, "hypothesis": hypothesis, "cer": cer, "missing": missing, "mean_volume_db": mean_db, "max_volume_db": peak_db,
        "chunks": rows, "issues": issues, "passed": not issues, "speech_evaluation_policy": EVALUATION_POLICY,
    }
    return result


def recheck(reference, analysis, protected_terms, aliases):
    chunks = []
    for row in analysis.get('chunks') or []:
        raw = row.get('raw_hypothesis', row.get('hypothesis', ''))
        corrected, corrections = correct_protected_lexicon(raw, reference, protected_terms, aliases)
        chunks.append({**row, 'raw_hypothesis': raw, 'hypothesis': corrected, 'corrections': corrections})
    raw = ''.join(row['raw_hypothesis'] for row in chunks) if chunks else str(analysis.get('hypothesis') or '')
    hypothesis = ''.join(row['hypothesis'] for row in chunks) if chunks else correct_protected_lexicon(raw, reference, protected_terms, aliases)[0]
    expected, heard = match_key(reference), match_key(hypothesis)
    missing = round(1 - subsequence_overlap(expected, heard) / max(1, len(expected)), 4) if expected else 0
    issues = [i for i in analysis.get('issues') or [] if not replaced_by_speech_recheck(i)]
    if expected:
        if not heard or analysis.get('max_volume_db') is None or analysis['max_volume_db'] < MIN_PEAK_DB:
            issues.append(QualityIssue.VOICE_ENERGY_MISSING.code)
        if missing > MAX_MISSING:
            issues.append(missing_dialogue(missing, MAX_MISSING))
        if len(heard) - len(expected) > max(12, len(expected) * 2):
            issues.append(QualityIssue.EXCESS_UNPLANNED_SPEECH.code)
        if unplanned_segments(reference, chunks):
            issues.append(QualityIssue.EXCESS_UNPLANNED_SPEECH.code)
    if re.search(r'keep\s*everything\s*above|this\s*is\s*take|spoken\s*clearly\s*and\s*completely', raw, re.I):
        issues.append(QualityIssue.DIRECTOR_INSTRUCTION_SPOKEN.code)
    result = {**analysis, 'reference': reference, 'hypothesis': hypothesis, 'chunks': chunks or analysis.get('chunks', []),
              'missing': missing, 'issues': list(dict.fromkeys(issues)), 'passed': not issues, 'speech_recheck_policy': 1}
    return result
