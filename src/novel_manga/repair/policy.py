"""The existing route priorities; no model calls, files or retry accounting."""
from dataclasses import dataclass

ATTRIBUTION_ERRORS = ('action_by_wrong_person', 'actor_missing', 'species_or_gender_wrong', 'lead_face_swapped')


@dataclass(frozen=True)
class RepairDecision:
    action: str
    diagnosis: dict


def source_decision(problems, precise, verified_source, instruction):
    attribution = any(precise.get(k) for k in ATTRIBUTION_ERRORS)
    if problems or (attribution and not verified_source):
        return RepairDecision('source', {
            'cause': 'request_mismatch' if problems else 'source_attribution',
            'reason': instruction if problems else 'check source attribution before acting on the old verdict'})
    return None


def whole_take_decision(precise, repeated=False):
    """What the whole-take cast check found (review/cast_video.py) is a take unlike its own request - an extra
    person or suit, a wrong look, a face artefact - so the request stands and the take is drawn again, or the
    shot rewritten when the same clip keeps failing.  Sent to the diagnosis instead, all 31 such clips of agent
    ch12 came back "uncertain" (2026-09-26), and uncertain meant a source recheck that rewrote casts rather than
    drawing again."""
    found = precise.get('cast_video') or {}
    if found.get('adjudicated'):
        if precise.get('request_conflict'):
            return RepairDecision('reframe', {'cause': 'request_mismatch',
                                  'reason': precise.get('evidence', 'actual references conflict with the request')})
        if not (precise.get('adjudication') or {}).get('confirmed'):
            return None
        # A visual confirmation does not establish whether a wrong actor/action came from the
        # script or the generator. Keep those cases on the existing diagnosis path; otherwise
        # moving frame flags into candidate would turn every attribution problem into a retake.
        kinds = set(precise.get('error_kinds') or [])
        if not kinds:
            return None
        if kinds & set(ATTRIBUTION_ERRORS):
            return None
        checked_input = precise.get('request_check') or {}
        if (kinds & {'extra_person', 'extra_object'} and 'same_person_twice' not in kinds
                and not (checked_input.get('consistent') is True and not checked_input.get('problems'))):
            return None  # without the current input evidence, do not assume the generator caused the extra entity
        return RepairDecision('reframe' if repeated else 'retake',
                              {'cause': 'generation_mismatch', 'reason': 'visible error confirmed against this shot',
                               'evidence': str(precise['adjudication'].get('checks') or [])})
    wrong = any(found.get(k) for k in ('extra_person', 'extra_object', 'face_artifact')) or any(
        check.get('costume_wrong') or check.get('color_wrong') or check.get('state_wrong') for check in found.get('looks') or [])
    if not wrong:
        return None
    diagnosis = {'cause': 'generation_mismatch', 'reason': 'the whole take differs from its own request',
                 'evidence': str(found.get('note') or '')}
    return RepairDecision('reframe' if repeated else 'retake', diagnosis)


def diagnosed_decision(diagnosis, repeated=False):
    action = 'retake' if diagnosis.get('cause') == 'generation_mismatch' else 'source'
    if diagnosis.get('cause') in {'script_mismatch', 'request_mismatch'}:
        action = 'reframe'
    if action == 'retake' and repeated:
        action = 'reframe'
    return RepairDecision(action, diagnosis)
