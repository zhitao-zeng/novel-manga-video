"""Synchronous host media operations called by the episode's sandbox agent."""
from __future__ import annotations


from novel_manga.util import read_json


from .draft_operations import DraftOperations
from .evidence import EpisodeEvidence
from .media_operations import MediaOperations
from .repair_operations import RepairOperations
from .completion import EpisodeCompletion
from .inspection import EpisodeInspection
from .tool_inputs import input_file
from .tool_state import record_state


class EpisodeTools:
    """The eight public commands compose owned episode services."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.drafts = DraftOperations(ctx)
        self.evidence = EpisodeEvidence(ctx)
        self.media = MediaOperations(ctx, self.drafts, self.evidence)
        self.repairs = RepairOperations(ctx, self.drafts, self.evidence)
        self.completion = EpisodeCompletion(ctx, self.media, self.evidence)
        self.inspection = EpisodeInspection(ctx, self.drafts, self.evidence)

    @property
    def begin_generation(self):
        return self.media.begin_generation

    @begin_generation.setter
    def begin_generation(self, callback):
        self.media.begin_generation = callback

    def dispatch(self, action, payload=None):
        try:
            if not isinstance(payload, dict) and payload is not None:
                raise ValueError('tool payload requires an object')
            if action not in {'check', 'adopt', 'render', 'review', 'apply', 'finish', 'status', 'doctor'}:
                raise ValueError('unknown episode tool action')
            result = getattr(self, action)(payload or {})
            if action in {'apply', 'finish'}:
                from . import recovery
                diagnosis = read_json(self.ctx.run_dir/'recovery-diagnosis.json', {})
                if not diagnosis.get('issues'):
                    diagnosis = recovery.diagnose(self.ctx, self.status({}))
                decisions = input_file(self.ctx, payload or {}, 'output/repair.json').get('decisions', []) if action == 'apply' else []
                recovery.remember(self.ctx, diagnosis, {'action': action, **result}, decisions)
            return {'action': action, **result}
        except Exception as error:
            return {'ok': False, 'action': action,
                    'error': f'{type(error).__name__}: {error}'}


    def check(self, payload):
        return self.drafts.check(payload)

    def adopt(self, payload):
        return self.drafts.adopt(payload)

    def render(self, payload):
        return self.media.render(payload, repairs=self.repairs)

    def review(self, payload):
        return self.media.review(payload)

    def apply(self, payload):
        return self.repairs.apply(payload)

    def finish(self, payload):
        return self.completion.finish(payload)

    def status(self, payload):
        return self.inspection.status(payload)

    def doctor(self, payload):
        from .doctor import run_doctor
        report = run_doctor(self.ctx.novel_dir)
        record_state(self.ctx, 'doctor', ok=report['ok'])
        return {'ok': True, 'report': report}
