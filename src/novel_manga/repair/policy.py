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


def diagnosed_decision(diagnosis, repeated=False):
    action = 'retake' if diagnosis.get('cause') == 'generation_mismatch' else 'source'
    if action == 'retake' and repeated:
        action = 'reframe'
    return RepairDecision(action, diagnosis)
