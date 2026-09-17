"""Quality issue identities and categories; reports retain their existing string codes."""
from __future__ import annotations
from enum import Enum


class QualityIssue(Enum):
    # code, category, scope, prefix match, replaced by a speech recheck
    VOICE_ENERGY_MISSING = ('voice_energy_missing', 'speech', 'clip', False, True)
    MISSING_DIALOGUE = ('missing_', 'speech', 'clip', True, True)
    EXCESS_UNPLANNED_SPEECH = ('excess_unplanned_speech', 'speech', 'clip', False, False)
    DIRECTOR_INSTRUCTION_SPOKEN = ('director_instruction_spoken', 'speech', 'clip', False, False)
    UNSCRIPTED_SPEECH = ('unscripted_speech', 'speech', 'clip', False, False)
    BLACK_FRAMES = ('black_frames', 'picture', 'clip', False, False)
    SILENCE_RATIO = ('silence_ratio', 'speech', 'assembly', False, False)
    LONG_SILENCE = ('long_silence', 'speech', 'assembly', False, False)

    def __init__(self, code, category, scope, prefix, speech_recheck):
        self.code, self.category, self.scope = code, category, scope
        self.prefix, self.speech_recheck = prefix, speech_recheck

    def matches(self, code):
        return code.startswith(self.code) if self.prefix else code == self.code


def is_speech_issue(code: str) -> bool:
    return any(issue.scope == 'clip' and issue.category == 'speech' and issue.matches(code) for issue in QualityIssue)


def replaced_by_speech_recheck(code: str) -> bool:
    # The existing recheck clears the entire missing/voice-energy prefix family.
    return any(issue.speech_recheck and code.startswith(issue.code) for issue in QualityIssue)


def missing_dialogue(missing: float, limit: float) -> str:
    return f'{QualityIssue.MISSING_DIALOGUE.code}{missing}_over_{limit}'


def speech_qc_ignores() -> list[str]:
    return [issue.code for issue in QualityIssue if issue.scope == 'assembly' and issue.category == 'speech']


def apply_speech_policy(analysis: dict, *, observe: bool) -> dict:
    issues = list(dict.fromkeys([*analysis.get('issues', []), *analysis.get('speech_issues', [])]))
    speech = [issue for issue in issues if is_speech_issue(issue)]
    if not observe and not analysis.get('speech_issues'):
        return analysis
    blocking = [issue for issue in issues if issue not in speech] if observe else issues
    return {**analysis, 'issues': blocking, 'speech_issues': speech, 'speech_gate': 'observe' if observe else 'enforce',
            'passed': not blocking if issues else analysis.get('passed', False)}
