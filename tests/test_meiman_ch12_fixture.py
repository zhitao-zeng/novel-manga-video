"""The frozen chapter-12 fixture: what the shelf held when PR3-PR5 started changing it.

The fixture is a copy of a real episode's evidence (原文 → 规划 → 打包 → 请求), taken before any of
the presence/wearing/continuity work landed.  These tests read the fixture only - no model, no
generation - and pin down three things the review found wrong and must not silently come back:

    mention ≠ presence    佩珀/贾维斯 appear in lines and event text as things talked ABOUT;
                          the cast of every shot is who is IN it
    worn, not split       托尼 arrives in the armour; the armour is one worn appearance of one
                          person, not a second actor facing him
    places stay places    the bus stop's binding text bakes in a parked bus the script then
                          drives in; a place describes what stays, not what happens

Later PRs add their regressions here against the same frozen inputs.
"""
import json
from pathlib import Path

FIXTURE = Path(__file__).parent / 'fixtures' / 'meiman_ch12'


def _load(name):
    return json.loads((FIXTURE / name).read_text(encoding='utf-8'))


def test_fixture_is_complete_and_digested():
    manifest = _load('manifest.json')
    import hashlib
    for name, digest in manifest['digests'].items():
        assert (FIXTURE / name).is_file(), name
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest()[:16] == digest, name


def test_the_people_talked_about_are_not_in_the_cast():
    """佩珀 and 贾维斯 are mentioned (shot 4's threat, shot 11's phone call); neither is on camera.

    The frozen script already keeps them out of `characters`.  This is the line PR3 must hold:
    a name in a line, an end_state or an event description is a candidate for presence, never
    presence itself - the defect the review described (complete_characters adding 佩珀 because a
    description names her) must not reappear through any of the layers that read this fixture.
    """
    script = _load('chapter_script.json')
    cast = {name for shot in script['shots'] for name in (shot.get('characters') or [])}
    assert '席勒' in cast and '托尼·斯塔克' in cast
    assert '佩珀' not in cast and '贾维斯' not in cast
    mentioned = ' '.join(str(shot.get(k) or '') for shot in script['shots']
                         for k in ('visual_prompt', 'motion_prompt', 'end_state')
                         ) + ' '.join(str(t.get('text') or '') for shot in script['shots'] for t in shot.get('turns', []))
    assert '佩珀' in mentioned or '贾维斯' in mentioned      # the fixture does talk about them


def test_the_armour_is_one_worn_appearance_not_a_second_actor():
    """托尼's arrival is in the 马克2号; the script must never cast the armour as a character.

    The frozen script keeps `characters` to the two people.  PR4's shot-level wearing state will
    express "托尼 in the armour" as one person's appearance; these assertions are what "not split"
    means, and they hold for the frozen data as it stands.
    """
    script = _load('chapter_script.json')
    cast = {name for shot in script['shots'] for name in (shot.get('characters') or [])}
    assert cast == {'席勒', '托尼·斯塔克'}, sorted(cast)
    # the armour is described in the picture text, as a thing its wearer acts through
    picture = ' '.join(str(shot.get('visual_prompt') or '') + str(shot.get('motion_prompt') or '')
                       for shot in script['shots'])
    assert '机甲' in picture


def test_the_place_binding_promised_a_parked_bus_the_script_drives_in():
    """The known defect, frozen on purpose: the bus stop's binding says a bus stands there, and
    the same shot has it arrive.  PR5 will let the stage carry its own place and strip happening
    state out of place text; this test names the defect so the fix can flip the assertion."""
    plan = _load('clip_plan.json')
    stop = next(c for c in plan['clips'] if c['location'] == '地狱厨房公交站牌')
    scene = stop['prompt'].split('【场景】', 1)[1].split('。', 1)[0]
    assert '巴士' in scene                                   # the place text bakes in the bus
    shot = next(s for s in _load('chapter_script.json')['shots'] if s['origin_index'] == 5)
    assert '巴士' in (shot.get('visual_prompt') or '')       # and the same shot drives it in


def test_every_shot_keeps_a_location_and_the_key_lines_survive():
    """Structure the later PRs may not lose: 12 shots, three places, the pivot lines intact."""
    script = _load('chapter_script.json')
    shots = script['shots']
    assert len(shots) == 12
    assert {s['location'] for s in shots} == {'地狱厨房第九尾巷心理诊所', '地狱厨房公交站牌', '斯塔克大厦实验室'}
    lines = [str(t.get('text') or '') for s in shots for t in s.get('turns', [])]
    joined = ' '.join(lines)
    for needle in ('这次免费', '钢铁侠扛着巴士飞', '你把他弄坏的就得修好'):
        assert needle in joined, needle


def test_the_reference_shelf_agrees_with_the_plan():
    """The shelf fixture mirrors the plan's references; the cards it names were on disk."""
    plan, shelf = _load('clip_plan.json'), _load('reference_shelf.json')
    for clip in plan['clips']:
        frozen = shelf['clips'][clip['clip_id']]
        live = [{'role': r.get('role'), 'name': r.get('name'), 'asset_id': r.get('asset_id'),
                 'path': r.get('path')} for r in clip.get('references', [])]
        assert frozen == live, clip['clip_id']
        for ref in live:
            if ref.get('asset_id'):
                assert ref['asset_id'] in shelf['cards_on_disk'], ref['asset_id']
