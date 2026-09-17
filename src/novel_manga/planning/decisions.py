"""Existing patch, retry and waiver decisions; no files, model calls or message parsing."""
from __future__ import annotations
from dataclasses import dataclass
from .issues import PlanningCode, PlanningIssue, ValidationResult
from . import constants


def patch_targets(issues: list[PlanningIssue]) -> tuple[list[str], dict[str, list[str]]] | None:
    missing, faulty = [], {}
    for issue in issues:
        if issue.code == PlanningCode.UNCITED_SEGMENT and issue.segment_id:
            missing.append(issue.segment_id)
        elif issue.code.scope == 'stage' and issue.stage:
            faulty.setdefault(issue.stage, []).append(issue.detail)
        else:
            return None
    return (list(dict.fromkeys(missing)), faulty) if missing or faulty else None


@dataclass
class RevisionDecision:
    action: str
    issues: list[PlanningIssue]
    warnings: list[str]
    targets: tuple[list[str], dict[str, list[str]]] | None = None
    floor_waived: bool = False
    strict_waived: list[str] | None = None

    @property
    def errors(self):
        return [issue.message for issue in self.issues]


def decide_validation(result: ValidationResult, *, final_attempt: bool, patch_rounds: int,
                      patch_seconds_left: float, allow_floor_waiver: bool = False) -> RevisionDecision:
    issues, warnings = list(result.issues), list(result.warnings)
    floor_waived = bool(allow_floor_waiver and final_attempt and issues
                       and all(i.code == PlanningCode.DURATION_BELOW_MINIMUM for i in issues))
    if floor_waived:
        warnings.extend('report only: ' + i.message for i in issues)
        issues = []
    targets = patch_targets(issues)
    can_patch = targets is not None and patch_rounds < constants.PATCH_ROUNDS and patch_seconds_left > 0
    return RevisionDecision('accept' if not issues else 'patch' if can_patch else 'rewrite', issues, warnings,
                            targets if can_patch else None, floor_waived)


def decide_strict(issues: list[PlanningIssue], *, final_attempt: bool) -> RevisionDecision:
    if issues and final_attempt:
        messages = [i.message for i in issues]
        return RevisionDecision('accept', [], ['report only (strict gate waived on the last attempt): ' + m for m in messages],
                                strict_waived=messages)
    return RevisionDecision('rewrite' if issues else 'accept', list(issues), [])


def invalid_response_feedback(errors: list[str], finish_reason: str | None) -> dict:
    repair = {'validation_errors': errors}
    if finish_reason == 'length':
        repair['instruction'] = ('上一稿超过输出长度上限被截断。本次压缩篇幅：每个字段只写必要内容，camera 和 light 在机位或光源不变时写'
                                 '"同上"，avoid 每段不超过 3 项，台词句子不加长；不得减少区段覆盖。')
    return repair


def revision_feedback(errors: list[str], raw: dict, *, resent: bool) -> dict:
    if resent:
        return {'instruction': '上一稿被原样重发，未做任何修改。本次不提供上一稿，请按 validation_errors 里的数字要求从头重写一份符合规模的剧本。',
                'validation_errors': errors}
    return {'instruction': '上一稿未通过硬门检查。逐条修复 validation_errors，其余内容尽量保持不变；source_quote 必须从对应区段逐字复制。',
            'validation_errors': errors, 'previous_response': raw}
