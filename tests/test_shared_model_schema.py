import json
from pathlib import Path
from novel_manga.models.source import NovelDocument
from novel_manga.models.bible import StoryBible
from novel_manga.models.episode import EpisodePlan
from novel_manga.models.assets import AssetRecord, SeriesAssetManifest
from novel_manga.models.runtime import ProductionPlan


def test_model_groups_keep_existing_persisted_schema():
    classes = [NovelDocument, StoryBible, EpisodePlan, AssetRecord, SeriesAssetManifest, ProductionPlan]
    before = json.loads((Path(__file__).parent / 'fixtures/shared_models_before.json').read_text())
    assert {c.__name__: c.model_json_schema() for c in classes} == before
