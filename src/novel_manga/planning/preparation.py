"""Existing pre-render audit contract, grounding and rewrite scope rules."""
from __future__ import annotations

from ..runtime_backends import normalize_text

POLICY = 'h3-book-preparation-v4-targeted-retry'
TERMINAL = {'ready', 'needs_source', 'needs_replan', 'needs_repair', 'error', 'existing_video', 'production_owned'}


def source_segment_text(segment_id, segments):
    """The program, rather than the reviewer, supplies literal source evidence."""
    return next((s['text'] for s in segments if s.get('segment_id') == segment_id), '')

def needs_full_replan(issues):
    return any(i.get('stage') == 0 for i in issues)


def audit_schema():
    from novel_manga.llm.client import obj
    return obj({'source_readable': {'type': 'boolean'}, 'source_problem': {'type': 'string'},
                'issues': {'type': 'array', 'maxItems': 12, 'items': obj({
                    'kind': {'type': 'string', 'enum': ['speaker', 'action', 'missing_event', 'location', 'identity']},
                    'stage': {'type': 'integer', 'minimum': 0}, 'source_quote': {'type': 'string', 'enum': ['']},
                    'source_segment': {'type': 'string'},
                    'problem': {'type': 'string'}, 'correction': {'type': 'string'}})}})


def grounded_issues(answer, script, segments):
    """An invented citation or stage cannot authorize a rewrite or a clean pass."""
    passage = normalize_text('\n'.join(s['text'] for s in segments))
    indexes = {s.get('index', i) for i, s in enumerate(script.get('shots', []), 1)}
    for issue in answer['issues']:
        if not issue['source_quote'].strip():
            issue['source_quote'] = source_segment_text(issue.get('source_segment'), segments)
        quote = normalize_text(issue['source_quote'])
        if len(quote) < 6 or quote not in passage:
            raise ValueError('text audit supplied an ungrounded source quotation')
        if issue['stage'] not in indexes and not (issue['stage'] == 0 and issue['kind'] == 'missing_event'):
            raise ValueError('text audit supplied an unknown stage')
    return answer['issues']

