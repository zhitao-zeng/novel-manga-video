"""Minimal media flow for focused tests that intentionally skip provider construction."""
from novel_manga.application.rendering.flow import ThinMediaRunner
from novel_manga.media.context import RenderContext


def uninitialized_runner():
    runner = object.__new__(ThinMediaRunner)
    runner.context = RenderContext()
    return runner


import json
from pathlib import Path
from novel_manga.config import Settings
from novel_manga.models.bible import Character, StoryBible


def media_runner(tmp_path, *, frame='16:9', local='', extra=None):
    novel = tmp_path; directory = novel / f'{novel.name}_1'; directory.mkdir(parents=True)
    profile = {'style': '2d', 'frame': frame, 'tier': 'quality', **(extra or {})}
    (novel / 'profile.json').write_text(json.dumps(profile))
    (directory / 'chapter_script.json').write_text('{"shots": []}')
    (directory / 'clip_plan.json').write_text('{"clips": []}')
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='国漫', palette='蓝',
                       characters=[Character(name='甲', appearance='黑发', wardrobe='青衣')], locations=['庭院'], style_fingerprint='test')
    return ThinMediaRunner(novel_dir=novel, episode_dir=directory, settings=Settings(local_h3_base_url=local),
                                bible=bible, workers=1, max_attempts=2, profile=profile)


def video_clip():
    return {'clip_id': 'a', 'kind': 'video', 'prompt': '庭院空镜。', 'prompt_h3': 'An empty courtyard.',
            'request_seconds': 5, 'references': [], 'repair_take': 2}
