"""Keep chapter-grounded identities and specific places available to the planner."""
from novel_manga.util import read_json


def speakers(defaults, identity):
    # Catalogue membership governs reference cards, not who spoke in the book.
    names = [row['name'] for row in (identity or {}).get('unmatched_actors', [])
             if row.get('kind') in {'individual', 'group'}
             and row.get('presence') in {'on_stage', 'voice'} and row.get('name')]
    return list(dict.fromkeys([*defaults, *names]))


def locations(novel_dir, chapter, source):
    extracted = read_json(novel_dir/f'lean/extract/ch-{chapter:03d}.json', {})
    rows = extracted.get('answer', {}).get('locations', [])
    compact = lambda text: ''.join(str(text).split())
    passage = compact(source)
    return [{'name': row['name'].strip(), 'description': row.get('sketch', ''),
             'evidence': row['evidence'], 'chapters': [chapter], 'first_chapter': chapter}
            for row in rows if str(row.get('name', '')).strip()
            and compact(row.get('evidence', '')) and compact(row['evidence']) in passage]


def adopt_selected(novel_dir, candidates, shots):
    from novel_manga.application.identity.reading_locations import adopt
    selected = {shot.get('location') for shot in shots}
    known = {name.split('：', 1)[0].strip()
             for name in read_json(novel_dir/'story_bible.json', {}).get('locations', [])}
    rows = [row for row in candidates if row['name'] in selected and row['name'] not in known]
    if rows:
        adopt(novel_dir, {'locations': rows})
