"""In-memory catalogue; exact names and existing aliases remain retrieval candidates."""
from __future__ import annotations

import copy
from novel_manga.runtime_backends import normalize_text

class IdentityCatalog:
    def __init__(self, bible: dict, ledger: list, aliases: dict, index: dict, claims: list, phases: dict):
        self.bible = copy.deepcopy(bible)
        bible = self.bible
        by_name = {r['canonical']: r for r in ledger}
        by_id = {r['id']: r for r in ledger}
        original_names = {}
        self.entities = {}
        for i, character in enumerate(self.bible.get('characters', []), 1):
            name = character['name']
            record = by_name.get(name, {})
            seen = set()
            while record.get('merged_into') in by_id and record['id'] not in seen:
                seen.add(record['id']); record = by_id[record['merged_into']]
            eid = record.get('id', f'e{i:03d}')
            original_names[name] = eid
            if eid not in self.entities or record.get('canonical') == name:
                forms = self.entities.get(eid, {}).get('forms', set()) | {name}
                self.entities[eid] = {'id': eid, 'name': name, 'asset_id': record.get('asset_id') or f'character_{i:03d}',
                                      'design': character, 'forms': forms}
            else:
                self.entities[eid]['forms'].add(name)
        self.entities['voice:collective'] = {'id': 'voice:collective', 'name': '无名群声', 'asset_id': None,
            'design': {'name': '无名群声', 'role': '原文群体共同发言的画外声，不绑定单个人物外貌'}, 'forms': {'无名群声'}}
        self.by_name = {r['name']: r for r in self.entities.values()}
        self.by_name.update({name: self.entities[eid] for name, eid in original_names.items()})
        self.aliases = {str(a): str(b) for a, b in aliases.items()}
        for row in index.get('characters', []):
            if row['name'] in self.by_name:
                self.by_name[row['name']]['forms'].update(row.get('forms') or {})
        # The index is not a replacement for the alias file. Both are retained
        # as candidates, including aliases added after the index was built.
        for alias, name in self.aliases.items():
            if name in self.by_name:
                self.by_name[name]['forms'].add(alias)
        self.claims = copy.deepcopy(claims)
        self.phases = copy.deepcopy(phases.get('characters', {}))

    def candidates(self, passage: str, names=()):
        text = normalize_text(passage)
        wanted = set(names)
        wanted.update(r['name'] for r in self.entities.values() if r['design'].get('role') in {'主角','男主角','女主角'})
        rows = [r for r in self.entities.values() if r['name'] in wanted
                or any(normalize_text(form) and normalize_text(form) in text for form in r['forms'])]
        rows.sort(key=lambda r: (r['name'] not in wanted,
                  -max((len(normalize_text(f)) for f in r['forms'] if normalize_text(f) in text), default=0)))
        return rows[:80]  # retrieval budget, not a rule that a shorter name cannot be a person

