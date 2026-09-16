from __future__ import annotations
from dataclasses import dataclass
import copy


@dataclass
class RepairProposal:
    """A candidate with the existing result/plan fields, never a new on-disk format."""
    result: dict
    script: dict | None = None
    plan: dict | None = None
    notes: dict | None = None
    changes: dict | None = None
    structural_repair: dict | None = None

    @classmethod
    def from_result(cls, value):
        value = copy.deepcopy(value)
        payload = value.pop('proposal', None)
        return cls(value, **payload) if payload is not None else cls(value)

    @property
    def changed(self):
        return self.result.get('changed', [])

    @property
    def reason(self):
        return self.result.get('why', '')

    @property
    def available(self):
        return self.script is not None and self.plan is not None

    @property
    def payload(self):
        return {'script': self.script, 'plan': self.plan, 'notes': self.notes,
                'changes': self.changes, 'structural_repair': self.structural_repair or {}}

    def as_result(self, include_proposal=False):
        result = copy.deepcopy(self.result)
        if include_proposal and self.available:
            result['proposal'] = copy.deepcopy(self.payload)
        return result
