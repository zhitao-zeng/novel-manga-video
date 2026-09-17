"""Shared split episode regression fixtures."""
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
from novel_manga.media import asset_inspection
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service
import novel_manga.application.repair.context as repair_context
import novel_manga.application.repair.judges as repair_judges
import novel_manga.application.production.render as production_render
from support.render_context import uninitialized_runner
import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from PIL import Image
import novel_manga.application.packing.context as packer
import novel_manga.application.rendering.flow as renderer
import novel_manga.application.packing.ranges as ranges
from novel_manga.models.bible import Character, StoryBible
from novel_manga.config import Settings


@pytest.fixture
def split_episode(tmp_path, monkeypatch):
    for key in ("MAX_CLIP_SECONDS", "MAX_STAGES", "SOFT_CUT_SECONDS", "TWO_VIEW_CAST_LIMIT"):
        monkeypatch.setattr(packer, key, getattr(packer, key))
    episode = tmp_path / "book" / "book_1"
    episode.mkdir(parents=True)
    bible = StoryBible(novel_title="测试", genre="generic", visual_style="2d", palette="蓝", style_fingerprint="test",
                       characters=[Character(name="林凡", appearance="黑发", wardrobe="白衣")], locations=["大厅：木桌"])
    (episode.parent / "story_bible.json").write_text(bible.model_dump_json())
    shot = {"index": 1, "origin_index": 9, "segment_id": "seg_1", "location": "大厅", "characters": ["林凡"],
            "visual_prompt": "林凡站在窗边", "motion_prompt": "林凡说话", "end_state": "林凡停下",
            "shot_scale": "中景", "camera": "固定中景", "light": "窗外日光",
            "turns": [{"speaker_name": "林凡", "delivery_mode": "visible_dialogue", "text": char * 48}
                      for char in "甲乙丙"]}
    script = {"shots": [shot]}
    plan = {"policy": "thin-15s", "limits": {"max_clip_seconds": 15, "max_stages": 3},
            "totals": {"profile": {"tier": "fast", "frame": "16:9", "style": "2d"}}}
    ctx = packing_context.context_for_plan(episode, episode.parent / "story_bible.json", plan)
    plan["clips"] = [packing_service.clip_entry(c, f"clip_{i:02d}", ctx)
                     for i, c in enumerate(ClipCompiler(ctx['compiler_options'] or compiler_options()).pack(packing_service.prepared_shots(copy.deepcopy(script), episode)), 1)]
    return episode, script, plan

