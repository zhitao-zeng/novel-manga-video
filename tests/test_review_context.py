import json
from concurrent.futures import ThreadPoolExecutor
from novel_manga.models.bible import StoryBible, Character
from novel_manga.review.policy import fix_tier, ReviewRules
from novel_manga.application.review.evidence import load_review_rules


def test_book_rules_do_not_leak_when_interleaved(tmp_path):
    fantasy, generic = tmp_path / 'fantasy', tmp_path / 'generic'
    for p in [fantasy, generic]: p.mkdir()
    (fantasy / 'profile.json').write_text('{"genre":"fantasy"}')
    (generic / 'profile.json').write_text('{"genre":"generic"}')
    (fantasy / 'entity_index.json').write_text(json.dumps({'characters':[{'name':'次要甲','tier':'major'}]}))
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='v', palette='p', style_fingerprint='f',
                       characters=[Character(name='次要甲',role='配角',appearance='青年',wardrobe='白衣')],locations=[])
    tail = {'identity_ok':False, 'identity_issue':'角色多出尾巴'}
    swapped = {'identity_ok':False, 'identity_issue':'次要甲被画成别人'}
    first = load_review_rules(fantasy)
    second = load_review_rules(generic)
    assert first is not second and second == ReviewRules()
    assert fix_tier(tail,bible,second) == 'must_fix'
    assert fix_tier(swapped,bible,first) == 'must_fix'
    assert fix_tier(swapped,bible,second) == 'optional'
    def check(book):
        rules = load_review_rules(book)
        return fix_tier(tail,bible,rules),fix_tier(swapped,bible,rules)
    expected = [check(fantasy),check(generic)] * 5
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(check,[fantasy,generic]*5)) == expected
    assert load_review_rules(generic) == second
