"""Only current, positive evidence from the actual image clears a clothing blocker."""
import json

from PIL import Image

from novel_manga.application.preparation import readiness, wearer_cards


def setup(tmp_path):
    image = tmp_path / 'character.jpeg'
    Image.new('RGB', (32, 32), 'black').save(image)
    ref = {'name': '江永', 'role': 'character', 'asset_id': 'character_001', 'path': image.name}
    clip = {'clip_id': 'c1', 'kind': 'video', 'prompt': '【阶段一】穿戴状态：江永穿着黑色披风；',
            'references': [ref], 'request_seconds': 10, 'lines': []}
    return image, ref, clip


def test_already_worn_regular_clothing_clears_only_after_seeing_the_image(tmp_path, monkeypatch):
    image, ref, clip = setup(tmp_path)
    assert readiness.render_risks(clip, tmp_path)[0]
    seen = []
    def reply(parts, schema, **kwargs):
        seen.extend(parts)
        return {'wearing': 'matches', 'evidence': '图中人物身上可见黑色披风'}
    monkeypatch.setattr(wearer_cards, 'ask_json', reply)
    wearer_cards.check_references(tmp_path, readiness.worn_reference_pairs({'clips': [clip, clip]}))
    assert any(part.get('type') == 'image_url' for part in seen)
    assert readiness.render_risks(clip, tmp_path)[0] == []
    # A changed image cannot borrow the former card's positive result.
    Image.new('RGB', (64, 64), 'white').save(image)
    assert readiness.render_risks(clip, tmp_path)[0]


def test_unclear_image_and_different_prop_keep_the_blocker(tmp_path, monkeypatch):
    _, ref, clip = setup(tmp_path)
    monkeypatch.setattr(wearer_cards, 'ask_json', lambda *a, **k: {'wearing': 'unclear', 'evidence': '背部被裁掉'})
    wearer_cards.check(tmp_path, ref, '黑色披风')
    assert readiness.render_risks(clip, tmp_path)[0]
    assert not wearer_cards.verified(tmp_path, ref, '铁拳套')
