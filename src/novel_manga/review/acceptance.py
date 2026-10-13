"""Content and technical outcomes, independent of files, models and scheduling."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeAcceptance:
    content_passed: bool
    technical_passed: bool
    missing_reviews: list[str]
    review_errors: list[str]
    remaining: list[str]
    stale: bool

    @property
    def passed(self) -> bool:
        return self.content_passed and self.technical_passed

    def quality_report(self, *, rounds=(), corrections=(), blocked=None, generated_counts=None):
        result = {'passed':self.passed, 'content_passed':self.content_passed,
                  'repair_rounds':list(rounds), 'automatic_corrections':sorted(set(corrections)),
                  'blocked':blocked or {}, 'missing_reviews':self.missing_reviews,
                  'review_errors':self.review_errors, 'remaining':self.remaining,
                  'generated_counts':generated_counts}
        if self.stale:
            result['reason']='media predates the current plan, H3 request or corrections'
        return result


def evaluate_acceptance(planned, media, review, *, stale=False) -> EpisodeAcceptance:
    rows=review.get('clips') or {}
    missing=sorted(set(planned)-rows.keys())
    errors=sorted(cid for cid,row in rows.items() if row.get('severity')=='review_error')
    content=bool(planned) and not media.get('failed_clips') and not media.get('gate_failed_clips') \
        and not review.get('feedback') and not missing and not errors and not stale
    technical=bool((media.get('assembly') or {}).get('thin_passed'))
    return EpisodeAcceptance(bool(content),technical,missing,errors,
                             sorted(set(review.get('feedback') or {}) | set(media.get('failed_clips') or [])
                                    | set(media.get('gate_failed_clips') or [])),bool(stale))
