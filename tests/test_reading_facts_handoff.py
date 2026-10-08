import novel_manga.application.review.character_updates as cast_character_updates
"""Real reading failures: source appearances survive attribution and casting handoff."""
import json

from novel_manga.application.planning.initialize import read_cast, read_cast_facts
from novel_manga.application.planning.bible_builder import BibleBuilder
from novel_manga.config import Settings
from novel_manga.source.ingest import read_novel
from novel_manga.story import lean_reading as lean
from novel_manga.story.reading_facts import (
    apply_known_facts, facts_for_name, facts_from_export, merge_character_facts,
)
from novel_manga.application.planning import lean_bible as thin


SCAR = "他的脸一直到胸脯上都是一块斑驳的疤痕，容貌丑陋到了极点。"
GOLD = "金发，金眸，白皙宛如牛奶的皮肤，精致俊美到极致"
CLOUD = "一个身穿淡金色长袍，有着一头瑰丽的白金色长发，眸子黑中带着点蔚蓝的俊美青年"
CHILD = "形如七八岁孩童的云帝"


def person(form, refers_to, kind="专名"):
    return {"form": form, "kind": kind, "refers_to": refers_to, "presence": "在场",
            "speaks": True, "gender": "男", "evidence": form}


def reading(people, looks):
    return {"summary": "", "people": people, "looks": looks, "changes": [], "locations": []}


def entry(name, appearance="", wardrobe="", **extra):
    return {"name": name, "aliases": [], "age": "未写明", "appearance": appearance,
            "wardrobe": wardrobe, "evidence": [], "tier": "重要", "first_chapter": 1,
            "chapters": [1], "unresolved": [], **extra}


def test_known_extraction_and_aliases_keep_permanent_scars_and_gold_face():
    # These are literal extracts from guangming ch13/ch47, not descriptions from the old Bible.
    extracts = {
        13: reading([person("科查", "科查")], [{"form": "科查", "text": SCAR, "lasting": "持久"}]),
        47: reading([person("黑衣青年", "艾尔哈姆", "描述性称谓")],
                    [{"form": "黑衣青年", "text": GOLD, "lasting": "持久"}]),
    }
    candidates = lean.aggregate(extracts, {13: "科查的面孔整个被毁掉了。" + SCAR,
                                          47: GOLD + "。黑衣青年缓缓点头。"})
    export = {"characters": [entry("科查", wardrobe="破洞药渍黑袍", aliases=["科查大师"]),
                              entry("艾尔哈姆", wardrobe="简单黑袍")], "look_bindings": []}
    assert lean.missing_look_bindings(export, candidates) == []
    bound = lean.bind_export_looks(export, candidates)
    facts = facts_from_export(bound)
    assert facts_for_name(facts, "科查大师")["appearance"] == SCAR
    assert facts["艾尔哈姆"]["appearance"] == GOLD
    assert {r["name"] for r in bound["characters"]} == {"科查", "艾尔哈姆"}
    assert facts["科查"]["evidence"] == [{"chapter": 13, "quote": SCAR}]


def test_yunsen_descriptor_requires_merge_attribution_and_keeps_the_whole_paragraph():
    text = CLOUD + "犹如一阵清风一样掠进了大殿。云氏一族长老团监察长老之一的云森走到林齐面前。"
    candidates = lean.aggregate({456: reading([person("云森", "云森")],
        [{"form": CLOUD, "text": CLOUD, "lasting": "持久"}])}, {456: text})
    export = {"characters": [entry("云森")], "look_bindings": []}
    assert lean.missing_look_bindings(export, candidates)[0]["context"] == text
    assert text in lean.candidate_table("光明纪元", candidates, 456)
    unknown = lean.bind_export_looks(export, candidates)
    assert unknown["characters"][0]["appearance"] == ""
    assert len(unknown["unresolved_looks"]) == 1  # nearby 云森 is deliberately not an automatic binding
    export["look_bindings"] = [{"chapter": 456, "form": CLOUD, "text": CLOUD, "name": "云森"}]
    bound = lean.bind_export_looks(export, candidates)
    assert bound["characters"][0]["appearance"] == CLOUD
    assert bound["characters"][0]["looks"] == [{"chapter": 456, "text": CLOUD, "lasting": "持久"}]


def test_ambiguous_descriptive_alias_is_not_assigned_to_a_nearby_person():
    facts = facts_from_export({"characters": [entry("甲", aliases=["青年"]), entry("乙", aliases=["青年"])]})
    assert facts_for_name(facts, "青年") == {}
    assert facts_for_name(facts, "甲")["name"] == "甲"
    candidates = lean.aggregate({1: reading([person("青年", "甲", "描述性称谓"),
                                             person("青年", "乙", "描述性称谓")],
        [{"form": "青年", "text": "一个黑发青年", "lasting": "持久"}])}, {1: "一个黑发青年走到甲乙面前。"})
    assert candidates["looks"]["青年"][0]["refers_to"] == ""
    export = {"characters": [entry("甲"), entry("乙")],
              "look_bindings": [{"chapter": 1, "form": "青年", "text": "一个黑发青年", "name": "未定"}]}
    assert all(not row["appearance"] for row in lean.bind_export_looks(export, candidates)["characters"])


def test_read_cast_keeps_its_interface_and_passes_full_facts_and_existing_phases(tmp_path):
    path = tmp_path / "story_bible.json"
    export = {"characters": [entry("科查", SCAR, "破洞黑袍", aliases=["科查大师"]),
                              entry("云帝", CHILD)]}
    path.write_text(json.dumps(export, ensure_ascii=False))
    phase = {"from": 257, "to": 258, "label": "众神之启法袍", "appearance": SCAR,
             "wardrobe": "古老复杂的众神之启法袍", "evidence": [{"chapter": 257, "quote": "一套款式极其古老"}]}
    (tmp_path / "phases.json").write_text(json.dumps({"policy": "phase-cards-v1", "characters": {"科查": [phase]}}))
    cast, aliases = read_cast(path)
    facts = read_cast_facts(path)
    assert cast == ["科查", "云帝"] and aliases == {"科查大师": "科查"}
    assert facts["科查"]["phases"] == [phase]
    assert facts_from_export(export, {"科查": [phase]})["科查"]["phases"] == [phase]
    assert facts_for_name(facts, "科查大师", chapter=257)["wardrobe"] == phase["wardrobe"]
    assert facts_for_name(facts, "科查", chapter=13)["wardrobe"] == "破洞黑袍"
    assert facts["云帝"]["appearance"] == CHILD
    assert facts["云帝"]["age"] == ""  # an unknown chronological age does not prevent a design
    design = apply_known_facts({"name": "云帝", "age": "孩童外观，实际年龄未知", "hair": "黑发"}, facts["云帝"])
    assert design["age"] == "孩童外观，实际年龄未知" and design["hair"] == "黑发"


def test_gender_comes_only_from_explicit_export_or_an_existing_phase():
    facts = facts_from_export({"characters": [entry("科查", gender="男"), entry("云帝", gender="未写明")]}, {
        "policy": "phase-cards-v1", "characters": {"科查": [
            {"from": 20, "to": 21, "label": "已有明确性别阶段", "gender": "女"}]}})
    assert facts["科查"]["gender"] == "男" and facts["云帝"]["gender"] == ""
    assert facts_for_name(facts, "科查", chapter=13)["gender"] == "男"
    assert facts_for_name(facts, "科查", chapter=20)["gender"] == "女"
    assert apply_known_facts({"gender": "女"}, facts["科查"])["gender"] == "男"
    assert apply_known_facts({"gender": "女"}, facts["云帝"])["gender"] == "女"
    later = facts_from_export({"characters": [entry("科查", gender="未写明")]})
    assert merge_character_facts(facts, later)["科查"]["gender"] == "男"


def test_initial_builder_receives_source_facts_and_preserves_them_in_new_profiles(tmp_path):
    source = tmp_path / "seed.md"
    source.write_text("第一章 夜行\n林齐走进庭院。")
    novel = read_novel(source, novel_id="test", title="光明纪元")
    facts = facts_from_export({"characters": [
        entry("科查", SCAR, "宽大破洞黑袍", gender="男", age="实际一百多岁，外貌年龄未明确"),
        entry("艾尔哈姆", GOLD, "简单黑色长袍", gender="男", age="青年"),
        entry("云帝", CHILD, age="孩童外观"),
    ]})
    requests = []
    builder = BibleBuilder(Settings(planner_max_revisions=0))
    builder.client.close()

    def reply(system, user, *_args, **_kwargs):
        requests.append((system, user))
        return {"novel_title": "光明纪元", "genre": "玄幻", "visual_style": "国漫", "palette": "黑金", "style_fingerprint": "",
                "characters": [
                    {"name": "科查", "gender": "女", "age": facts["科查"]["age"], "appearance": SCAR,
                     "wardrobe": "宽大破洞黑袍", "hair": "稀疏暗色短发", "base_costume": "宽大破洞黑袍"},
                    {"name": "艾尔哈姆", "age": "青年", "appearance": GOLD,
                     "wardrobe": "简单黑色长袍", "hair": "金发", "palette": "黑金白"},
                    {"name": "云帝", "age": "孩童外观", "appearance": CHILD,
                     "wardrobe": "深色童子长袍", "hair": "黑发"}],
                "locations": ["庭院：白天、石砖铺地，三面回廊，日光从头顶照入。"]}

    builder._json = reply
    result = builder.build_bible(novel, list(facts), character_facts=facts)
    system, request = requests[0]
    assert SCAR in request and GOLD in request and CHILD in request
    schema = json.loads(request.split("JSON Schema：", 1)[1].split("\n", 1)[0])
    fixed = schema["properties"]["characters"]["items"]["oneOf"][1]["allOf"][1]["properties"]
    assert fixed["name"]["const"] == "艾尔哈姆" and fixed["appearance"]["const"] == GOLD
    assert fixed["age"]["const"] == "青年" and fixed["base_costume"]["const"] == "简单黑色长袍"
    assert fixed["gender"]["const"] == "男"
    assert "永久毁容" in system and "所有角色必须是健康" not in system
    assert result.characters[0].appearance == SCAR
    assert result.characters[0].gender == "男"
    assert result.characters[0].wardrobe == result.characters[0].base_costume == "宽大破洞黑袍"
    assert result.characters[1].hair == "金发" and result.characters[1].age == "青年"
    assert result.characters[2].age == "孩童外观" and result.characters[2].wardrobe == "深色童子长袍"



def test_later_partial_reading_keeps_old_facts_and_existing_bible_designs(tmp_path):
    old_facts = facts_from_export({"characters": [entry("艾尔哈姆", GOLD, "简单黑袍")]})
    later = facts_from_export({"characters": [entry("艾尔哈姆")]})
    assert merge_character_facts(old_facts, later)["艾尔哈姆"]["appearance"] == GOLD
    original = {"characters": [{"name": "艾尔哈姆", "appearance": "人工定稿金发金眸", "wardrobe": "黑袍", "asset_id": "character_018"}], "locations": []}
    (tmp_path / "story_bible.json").write_text(json.dumps(original, ensure_ascii=False))
    (tmp_path / "reading_cast.json").write_text(json.dumps({"characters": ["艾尔哈姆"], "aliases": {}, "character_facts": old_facts}))
    thin.fold_into_bible(tmp_path, {"characters": [entry("艾尔哈姆")], "locations": []})
    assert json.loads((tmp_path / "story_bible.json").read_text()) == original
    assert json.loads((tmp_path / "reading_cast.json").read_text())["character_facts"]["艾尔哈姆"]["appearance"] == GOLD


def test_explicit_lean_character_update_passes_facts_to_existing_reconciler(tmp_path, monkeypatch):
    import sys
    import novel_manga.application.review.bible as review
    source = tmp_path / "source.md"
    source.write_text("第一章 夜行\n甲站在门前。")
    (tmp_path / "novel.json").write_text(json.dumps({"source": str(source), "title": "测试", "chapters": [
        {"index": 1, "title": "第一章 夜行", "start": 0, "end": len(source.read_text())}]}))
    (tmp_path / "story_bible.json").write_text(json.dumps({"characters": [entry("甲", "人工旧设计", "青袍")], "locations": []}))
    (tmp_path / "lean").mkdir()
    export = {"characters": [entry("甲", "黑发青年", "青袍", age="青年")], "look_bindings": []}
    monkeypatch.setattr(thin, "read_chapters", lambda *_a, **_k: {1: reading([], [])})
    monkeypatch.setattr(thin, "judge_settings", lambda *_a: None)
    monkeypatch.setattr(thin, "ask_json", lambda *_a, **k: export if k["name"] == "lean_merge" else {"locations": []})
    calls = []
    monkeypatch.setattr(cast_character_updates, "reconcile_characters", lambda *a, **k: calls.append((a, k)) or {"updated": ["甲"]}, raising=False)
    monkeypatch.setattr(sys, "argv", ["lean_bible_thin.py", "--novel-dir", str(tmp_path), "--chapters", "1",
                                    "--endpoints", "http://unused.invalid", "--update-characters", "甲"])
    assert thin.main() == 0
    assert calls[0][0] == (tmp_path, ["甲"])
    assert calls[0][1]["character_facts"]["甲"]["appearance"] == "黑发青年"
    assert json.loads((tmp_path / "story_bible.json").read_text())["characters"][0]["appearance"] == "人工旧设计"
