import novel_manga.application.packing.assets as packing_assets
import novel_manga.application.packing.context as packing_context
"""Legacy clips use one card per actor with correct picture tags and fresh H3 bindings."""
import copy
import json
import sys
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import novel_manga.application.rendering.flow as rc
from novel_manga.application.packing.single_card import single_card_plan
from novel_manga.config import Settings
from novel_manga.models.bible import Character, StoryBible
from novel_manga.providers.base import ImageResult


def legacy_clip():
    refs = []
    for i, (name, asset, file) in enumerate([
        ("甲", "character_001", "turnaround.jpeg"), ("甲", "character_001", "expressions.jpeg"),
        ("乙", "character_002", "turnaround.jpeg"), ("乙", "character_002", "expressions.jpeg"),
    ], 1):
        refs.append({"tag": f"@图片{i}", "role": "character", "name": name, "asset_id": asset,
                     "path": f"series_assets/characters/{asset}/{file}"})
    refs += [{"tag": "@图片5", "role": "location", "asset_id": "location_001", "path": "location.jpeg"},
             {"tag": "@音频1", "role": "voice", "name": "乙", "path": "voice.wav"}]
    return {"clip_id": "clip_01", "kind": "video", "references": refs,
            "prompt": "【人物】<甲>对应@图片1和@图片2：两张都不采用背景；其他人不得使用这两张图的相貌。<乙>对应@图片3和@图片4。\n"
                      "【场景】@图片5用于房间。\n【阶段1】甲走向乙。\n【声音】@音频1",
            "prompt_h3": "old English pictures", "prompt_h3_of": "old digest", "request_seconds": 15,
            "segment_ids": ["seg_1"], "shot_parts": [{"index": 2, "part": [1, 2]}],
            "lines": [{"speaker_name": "乙", "text": "你好"}]}


def test_removes_expression_refs_and_renumbers_without_changing_story():
    clip = legacy_clip()
    untouched = {"clip_id": "clip_02", "references": [], "prompt": "unchanged", "prompt_h3": "cached"}
    before = copy.deepcopy(clip)
    plan = {"clips": [clip, untouched]}
    assert single_card_plan(plan) == ["clip_01"]
    assert [r["tag"] for r in clip["references"]] == ["@图片1", "@图片2", "@图片3", "@音频1"]
    assert "<甲>对应@图片1：该图不采用背景" in clip["prompt"]
    assert "<乙>对应@图片2。" in clip["prompt"]
    assert "【场景】@图片3用于房间" in clip["prompt"]
    assert "【阶段1】甲走向乙。\n【声音】@音频1" in clip["prompt"]
    assert "prompt_h3" not in clip and "prompt_h3_of" not in clip
    for key in ("request_seconds", "segment_ids", "shot_parts", "lines"):
        assert clip[key] == before[key]
    assert plan["clips"][1] == untouched
    assert single_card_plan(plan) == []


def test_picture_ten_is_remapped_once_not_as_picture_one():
    clip = legacy_clip()
    clip["references"][4]["tag"] = "@图片10"
    clip["prompt"] = clip["prompt"].replace("@图片5", "@图片10")
    single_card_plan({"clips": [clip]})
    assert "【场景】@图片3用于房间" in clip["prompt"]
    assert "@图片10" not in clip["prompt"]


def test_fast_factory_does_not_build_or_reuse_expression_sheet(tmp_path, monkeypatch):
    factory = rc.FramedAssetFactory(Settings(reuse_existing_assets=True), object())
    bible = StoryBible(novel_title="测试", genre="奇幻", visual_style="2D", palette="蓝",
                       style_fingerprint="test", locations=[], characters=[Character(name="甲", appearance="黑发", wardrobe="白衣")])
    root = tmp_path / "series_assets"
    old = root / "characters/character_001/expressions.jpeg"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"unused old expression sheet")
    built = []

    def ensure_card(self, prompt, output, reference=None):
        built.append(output.name)
        Image.new("RGB", (30, 60), "white").save(output)
        return ImageResult(path=output)

    monkeypatch.setattr(rc.FramedAssetFactory, "ensure_card", ensure_card)
    manifest = factory.build_selected(root, bible, {"character_001"}, set(), expressions=False)
    assert built == ["turnaround.jpeg"]
    assert manifest.characters[0].secondary_image is None
    assert old.read_bytes() == b"unused old expression sheet"


def test_fast_lead_does_not_get_an_extra_sheet_from_old_environment(tmp_path, monkeypatch):
    pass
    monkeypatch.setattr(packing_context, 'TWO_VIEW_CAST_LIMIT', 0)
    monkeypatch.setenv("NOVEL_TWO_VIEWS", "1")
    bible = StoryBible(novel_title="测试", genre="奇幻", visual_style="2D", palette="蓝",
                       style_fingerprint="test", locations=["房间：木桌"],
                       characters=[Character(name="甲", role="主角", appearance="黑发", wardrobe="白衣")])
    old = tmp_path / "series_assets/characters/character_001/expressions.jpeg"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old sheet")
    refs, _, _ = packing_assets.build_references(["甲"], "房间", bible, {"房间": "房间：木桌"}, novel_dir=tmp_path)
    assert [r["path"] for r in refs if r["role"] == "character"] == ["series_assets/characters/character_001/turnaround.jpeg"]


def test_chapter_preview_does_not_stack_the_whole_long_book(tmp_path):
    root = tmp_path / "series_assets"
    for n in range(1, 221):
        card = root / "characters" / f"character_{n:03d}" / "turnaround.jpeg"
        card.parent.mkdir(parents=True)
        Image.new("RGB", (30, 60), "white").save(card)
    output = rc.cards_sheet(tmp_path, root / "cards_sheet.jpg", asset_ids={"character_001"})
    with Image.open(output) as preview:
        preview.load()
        assert preview.height == 300 + 16


@pytest.mark.parametrize('passed',[True,False])
def test_missing_expression_cleanup_only_reuses_a_current_approved_video(tmp_path,monkeypatch,passed):
    import novel_manga.application.rendering.h3 as prompts
    import novel_manga.application.repair.history as history
    from novel_manga.application.profiles import h3_source_digest, plan_fingerprint
    from novel_manga.review.storage import take_identity
    from novel_manga.application.packing.single_card import repair_missing_expressions
    d=tmp_path/'book'/'book_1';d.mkdir(parents=True)
    clip=legacy_clip();clip['shot_indexes']=[2];clip['shot_parts']=[{'index':2,'part':[1,1]}]
    keep={'clip_id':'other','kind':'video','references':[],'request_seconds':5,'prompt':'unchanged'}
    plan={'clips':[clip,keep],'limits':{'max_clip_seconds':15}}
    for ref in clip['references']:
        if ref['role']=='voice' or Path(ref['path']).name=='expressions.jpeg':continue
        p=d.parent/ref['path'];p.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(16,16),'white').save(p)
    video=d/'work/clips/clip_01/attempt_01/clip.mp4';video.parent.mkdir(parents=True);video.write_bytes(b'old video')
    row={'video':str(video),'take':take_identity(video),'story_ok':passed,
         'verify':{'verdict':'fine' if passed else 'obvious','same_person_twice':not passed}}
    for name,data in [('clip_plan.json',plan),('chapter_script.json',{'shots':[{'index':2}]}),
                      ('episode_review.json',{'clips':{'clip_01':row}}),
                      ('thin_media_report.json',{'clips':[{'clip_id':'clip_01','selected':{'video':str(video),'passed':True}}]})]:
        (d/name).write_text(json.dumps(data))
    history.begin_trial(d,{'other'},'source_recheck',after_plan=plan)
    h=history.load(d);h['trials'][0]['managed']=True;history.save(d,h)
    def convert(c,note=''):
        c.update(prompt_h3='fresh English',prompt_h3_of=h3_source_digest(c['prompt'],note,c.get('crowd_roles')))
        return True
    monkeypatch.setattr(prompts,'convert',convert)
    result=repair_missing_expressions(d)
    updated=json.loads((d/'clip_plan.json').read_text())
    assert result['changed']==['clip_01'] and result['retained']==(['clip_01'] if passed else [])
    assert updated['clips'][1]==keep
    assert all('expressions.jpeg' not in r['path'] for r in updated['clips'][0]['references'])
    assert history.load(d)['trials'][0]['expected_plan']==plan_fingerprint(updated)
    accepted=history.source_accepted_take(d,updated['clips'][0],'')
    assert accepted==(video if passed else None)
    video.write_bytes(b'replaced video')
    assert history.source_accepted_take(d,updated['clips'][0],'') is None
