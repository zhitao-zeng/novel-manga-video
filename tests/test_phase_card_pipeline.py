"""Run the actual phase CLI with a simulated image supplier, including expressions."""
import json
from types import SimpleNamespace

from novel_manga.application.assets import phase_cards
from novel_manga.config import Settings
from novel_manga.util import atomic_write_json


def test_shared_missing_worn_prop_is_generated_once_before_character_builds(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    target=tmp_path/'props/prop_001/turnaround.jpeg'
    start=threading.Barrier(2)
    calls=[]
    class Factory:
        def build_selected(self, *args, **kwargs):
            calls.append(kwargs['prop_ids'])
            time.sleep(0.03)
            target.write_bytes(b'shared prop')
    def one():
        start.wait(timeout=2)
        phase_cards.ensure_worn_card(Factory(), tmp_path, None, target)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(one) for _ in range(2)]
        for future in futures:future.result()
    assert calls==[{'prop_001'}]


def test_phase_cli_builds_expressions_and_reuses_completed_identical_cards(tmp_path, monkeypatch):
    atomic_write_json(tmp_path/'story_bible.json', {
        'novel_title': '测试', 'genre': 'fantasy', 'visual_style': '3D', 'palette': '暖色',
        'style_fingerprint': '唯美3D', 'characters': [{'name': '甲', 'appearance': '黑发青年', 'wardrobe': '黑袍'}]})
    phase_path = tmp_path/'series_assets/phases.json'
    phase = {'from': 1, 'to': 1, 'asset_id': 'character_001-p1', 'label': '佩钱袋', 'base_costume': '黑袍与腰间钱袋'}
    atomic_write_json(phase_path, {'characters': {'甲': [phase]}})
    monkeypatch.setattr(phase_cards, 'load_dotenv', lambda *a: None)
    monkeypatch.setattr(phase_cards, 'load_profile', lambda *a, **k: {'frame': '16:9'})
    monkeypatch.setattr(phase_cards, 'load_genre', lambda *a: {})
    monkeypatch.setattr(phase_cards, 'load_style', lambda *a: {})
    monkeypatch.setattr(phase_cards.Settings, 'from_env', lambda **k: Settings())
    monkeypatch.setattr(phase_cards.AssetStyle, 'for_genre', lambda *a, **k: SimpleNamespace(
        render_family='3D', render_direction='唯美3D', prompt_fingerprint=False, tidy_prompts=True, card_brief=''))
    monkeypatch.setattr(phase_cards, 'style_card_suffix', lambda *a: '')
    monkeypatch.setattr(phase_cards, 'wants_3d_card', lambda *a: True)
    monkeypatch.setattr(phase_cards, 'FramedPhanRouter', lambda *a: None)
    calls = []
    class Supplier:
        def __init__(self, *a, **k):
            pass
        def ensure_card(self, prompt, output, **kwargs):
            calls.append((prompt, output.name))
            output.write_bytes(b'simulated image')
    monkeypatch.setattr(phase_cards, 'FramedAssetFactory', Supplier)
    argv = ['phase-cards', '--novel-dir', str(tmp_path), '--only', '甲', '--chapter', '1', '--expressions']
    monkeypatch.setattr(phase_cards.sys, 'argv', argv)
    assert phase_cards.main() == 0
    assert [name for _, name in calls] == ['turnaround.jpeg', 'expressions.jpeg']
    # A later step failed previously: a new phase ID must reuse its completed images.
    phase['asset_id'] = 'character_001-p2'
    atomic_write_json(phase_path, {'characters': {'甲': [phase]}})
    assert phase_cards.main() == 0
    assert len(calls) == 2
    output = tmp_path/'series_assets/characters/character_001-p2'
    assert (output/'expressions.jpeg').read_bytes() == b'simulated image'
    assert json.loads((output/'spec.json').read_text())['reused_from'].endswith('character_001-p1')
