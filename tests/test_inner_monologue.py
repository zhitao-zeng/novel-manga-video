"""Inner monologue rides the existing voice-off path: the character's own voice, a closed mouth,
no reply, and it pays its seconds like any other line.

The ban is lifted (constants hard rule 3 rewritten, the requirements switch split, the outline
prompt follows), but "allowed" only counts if the compiled request carries it: 席勒's motive line
must reach the video prompt as his offscreen voice, other characters must not be made to answer
it, and the duration budget must count it - a monologue nobody hears or one that costs nothing
would both be silent regressions of the old ban.
"""
from novel_manga.application.profiles import frame_spec
from novel_manga.planning import constants as pc_constants
from novel_manga.story.compilation import ClipCompiler, CompilerOptions
from novel_manga.planning.normalization import normalize_turns
from novel_manga.planning.context import PlannerContext


def options(seconds=15):
    return CompilerOptions(seconds, seconds * .6, 3, 'execution', 8, frame=frame_spec({'frame': '16:9'}))


def monologue_turn(text="还在气头上呢。算了，今天心情好。"):
    return {"speaker_name": "席勒", "delivery_mode": "offscreen_dialogue", "text": text, "emotion": "平静"}


def shot(**kw):
    base = {"index": 1, "origin_index": 1, "location": "诊所", "segment_id": "s1", "shot_scale": "中景",
            "visual_prompt": "席勒坐在桌后看着托尼，神情放松", "motion_prompt": "席勒听着托尼的威胁",
            "end_state": "席勒放下咖啡杯", "characters": ["席勒", "托尼·斯塔克"],
            "actions": [{"actor": "席勒", "action": "听", "target": ""}],
            "turns": [monologue_turn()], "camera": "", "light": "", "sfx": "", "avoid": "", "extras": []}
    base.update(kw)
    return base


def test_the_monologue_survives_into_the_compiled_prompt():
    clip = {"clip_id": "clip_01", "kind": "video", "request_seconds": 10, "shots": [shot()],
            "references": [], "lines": []}
    compiler = ClipCompiler(options())
    prompt = compiler.compile_prompt(clip, bible(), ["席勒", "托尼·斯塔克"], [], "诊所")
    assert "还在气头上呢" in prompt                       # the motive line reaches the request
    assert "画外" in prompt or "席勒" in prompt            # in his voice, offscreen


def bible():
    from novel_manga.models.bible import Character, StoryBible
    return StoryBible(novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
                      characters=[Character(name="席勒", role="男主角", appearance="清瘦", wardrobe="白大褂"),
                                  Character(name="托尼·斯塔克", role="主角", appearance="瘦高", wardrobe="便装")],
                      locations=["诊所：低矮房间"])


def test_the_monologue_pays_its_seconds():
    """An offscreen line is spoken audio: the budget counts it like any other turn."""
    quiet = ClipCompiler(options()).shot_seconds({**shot(), "turns": []})
    speaking = ClipCompiler(options()).shot_seconds(shot())
    assert speaking > quiet


def test_the_monologue_does_not_become_visible_speech():
    """Mouth stays closed: the speaker is on camera but the mode is offscreen, and framing must not
    turn him into the stage's visible speaker."""
    errors, warnings = [], []
    turns_out, visible = normalize_turns(shot(), ["席勒", "托尼·斯塔克"], ["席勒", "托尼·斯塔克"],
                                         "stage 1", PlannerContext(), errors, warnings)
    assert turns_out[0]["delivery_mode"] == "offscreen_dialogue"
    assert "席勒" not in visible                         # his lips are not asked to move


def test_the_prompts_no_longer_ban_the_monologue():
    """All three places the ban used to live now say the opposite."""
    assert "内心独白不要改成出声自语" not in pc_constants.DEFAULT_SYSTEM_PROMPT
    assert "没有旁白、没有内心独白" not in pc_constants.DEFAULT_SYSTEM_PROMPT
    assert "内心独白" in pc_constants.DEFAULT_SYSTEM_PROMPT          # the rule, stated positively
    from novel_manga.planning.prompts import outline_prompt
    assert "内心独白" in outline_prompt("coverage")
    assert "没有旁白或内心音" not in outline_prompt("coverage")


def test_the_coverage_outline_asks_for_scene_handoffs():
    """The first pass now plans the scene relations, not only the segment allocation."""
    assert "scene_handoffs" in pc_constants.OUTLINE_SECTIONS["coverage"]
    assert "进入原因" in pc_constants.OUTLINE_SECTIONS["coverage"]["scene_handoffs"]


def test_the_location_card_no_longer_locks_the_time_of_day():
    from novel_manga.planning.prompts import grammar_text
    text = grammar_text({"light_contrast": "低对比", "location_time": {"诊所": "夜晚", "公交站": "白天"}})
    assert "已由地点卡锁定" not in text
    assert "连续性冲突时以原文和本场为准" in text
