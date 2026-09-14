"""The v2 ledger: quotations are located in the chapter, mentions stay where they happened, claims carry a
verdict and a status, a supported same_as becomes a reversible merge edge, the bible grows from the ledger's
rows, and a scene snapshot says who is present, through which body, and what must not be revealed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import entity_ledger_thin as el  # noqa: E402

CH7 = "薇奥拉公主走进来。“早安，调查师。”作家小姐笑着说。莱恩点头。原来作家小姐就是薇奥拉。脑内的女声说：别信她。"
CH8 = "艾琳娜的灵魂进入了薇奥拉的身体。莱恩看着她，只有琥珀在窗台。"
RAW7 = {"chapter": 7, "mentions": [
    {"form": "薇奥拉公主", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "薇奥拉公主走进来"},
    {"form": "作家小姐", "entity": "NEW:作家小姐", "kind": "proper", "presence": "on_stage", "evidence": "作家小姐笑着说"},
    {"form": "莱恩", "entity": "e001", "kind": "proper", "presence": "on_stage", "evidence": "莱恩点头"},
    {"form": "女声", "entity": "NEW:脑内女声", "kind": "contextual", "presence": "voice", "evidence": "脑内的女声说"}],
    "new_entities": [{"name": "作家小姐", "kind": "person", "named": True, "description": "", "evidence": "作家小姐笑着说"},
                     {"name": "脑内女声", "kind": "spirit", "named": False, "description": "声音", "evidence": "脑内的女声说：别信她"}],
    "claims": [{"type": "same_as", "subject": "NEW:作家小姐", "object": "e002", "scope": "reality", "hidden_from_reader": False,
                "evidence": "原来作家小姐就是薇奥拉"}],
    "relations": [{"from": "e001", "to": "e002", "base": "unknown", "stance": "romantic_interest", "address": "殿下", "hidden_from_reader": False,
                   "evidence": "莱恩点头"},
                  {"from": "e001", "to": "e002", "base": "kinship_spouse", "stance": "neutral", "address": "", "hidden_from_reader": False,
                   "evidence": "这句话不在原文里"}]}
RAW8 = {"chapter": 8, "mentions": [
    {"form": "艾琳娜", "entity": "e003", "kind": "proper", "presence": "on_stage", "evidence": "艾琳娜的灵魂进入了薇奥拉的身体"},
    {"form": "薇奥拉", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "艾琳娜的灵魂进入了薇奥拉的身体"},
    {"form": "莱恩", "entity": "e001", "kind": "proper", "presence": "on_stage", "evidence": "莱恩看着她"},
    {"form": "琥珀", "entity": "e004", "kind": "proper", "presence": "on_stage", "evidence": "只有琥珀在窗台"}],
    "new_entities": [], "claims": [{"type": "occupies_body", "subject": "e003", "object": "e002", "scope": "reality", "hidden_from_reader": True,
                                    "evidence": "艾琳娜的灵魂进入了薇奥拉的身体"}],
    "relations": [{"from": "e003", "to": "e002", "base": "kinship_sibling", "stance": "unknown", "address": "", "hidden_from_reader": True,
                   "evidence": "只有琥珀在窗台"}]}


def novel(tmp_path: Path) -> Path:
    root = tmp_path / "wuyue"
    for n, text in ((7, CH7), (8, CH8)):
        d = root / f"wuyue_{n}"
        d.mkdir(parents=True)
        (d / "segments.json").write_text(json.dumps([{"segment_id": "seg_1", "text": text}], ensure_ascii=False), encoding="utf-8")
    (root / "story_bible.json").write_text(json.dumps({"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "薇奥拉公主", "role": "公主"}, {"name": "艾琳娜", "role": ""}, {"name": "琥珀·高德", "role": ""}]},
        ensure_ascii=False), encoding="utf-8")
    return root


def test_offering_finds_the_bible_cast_by_name_pieces():
    entities = [{"id": "e001", "canonical": "莱恩·格雷", "status": "active", "role": ""},
                {"id": "e002", "canonical": "薇奥拉公主", "status": "active", "role": ""},
                {"id": "e003", "canonical": "赫尔曼·施密特", "status": "active", "role": ""},
                {"id": "e004", "canonical": "沈玄川", "status": "active", "role": "主角"}]
    assert el.retrieval_keys("薇奥拉公主") == {"薇奥拉", "薇奥", "奥拉"} and "格雷" in el.retrieval_keys("莱恩·格雷")
    forms = {e["id"]: {e["canonical"]} for e in entities}
    rows = {e["id"] for e in el.offered(entities, forms, CH7, set())}
    assert rows == {"e001", "e002", "e004"}   # named by a piece, named by a piece, always (the lead); 赫尔曼 is not in the chapter


def test_locate_finds_quotations_despite_punctuation():
    assert el.locate("原来作家小姐就是薇奥拉", CH7) == (CH7.index("原来"), CH7.index("薇奥拉。") + 3)
    assert el.locate("早安，调查师", CH7) is not None
    assert el.locate("莱恩", CH7) is None and el.locate("这句不在", CH7) is None


def test_ground_keeps_only_backed_records():
    raw = {"mentions": [{"form": "作家小姐", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "作家小姐笑着说"},
                        {"form": "调查师", "entity": "e001", "kind": "contextual", "presence": "on_stage", "evidence": "早安，调查师"},
                        {"form": "女声", "entity": "NEW:脑内女声", "kind": "contextual", "presence": "voice", "evidence": "脑内的女声说"},
                        {"form": "不在文中", "entity": "e001", "kind": "proper", "presence": "on_stage", "evidence": "x"}],
           "new_entities": [{"name": "NEW:脑内女声", "kind": "spirit", "named": False, "description": "声音", "evidence": "脑内的女声说：别信她"},
                            {"name": "幽灵", "kind": "spirit", "named": False, "description": "", "evidence": "这句话不在原文里"}],
           "claims": [{"type": "same_as", "subject": "作家小姐", "object": "e002", "scope": "reality", "hidden_from_reader": False, "evidence": "原来作家小姐就是薇奥拉"},
                      {"type": "death", "subject": "e001", "object": "", "scope": "reality", "hidden_from_reader": False, "evidence": "莱恩死了"}]}
    mentions, news, claims, dropped = el.ground(7, CH7, raw)
    assert [m["form"] for m in mentions] == ["作家小姐", "调查师", "女声"] and mentions[0]["count"] == 2 and mentions[2]["presence"] == "voice"
    assert [n["name"] for n in news] == ["脑内女声"] and claims[0]["type"] == "same_as" and len(claims) == 1
    assert {d["what"] for d in dropped} == {"mention", "new_entity", "claim"}


def test_resolve_merges_reversibly_grows_the_bible_and_snapshots_the_scene(tmp_path, monkeypatch):
    root = novel(tmp_path)
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": "原文直说"})
    ledger = el.Ledger(root)
    summary = ledger.resolve_chapter(7, CH7, RAW7, workers=1)
    # "new" lists the records still standing after the chapter; 作家小姐 was created and merged in the same reading
    assert summary["new"] == ["脑内女声"] and summary["merged"] == ["作家小姐→薇奥拉公主"] and dict(summary["claims"]) == {"accepted": 1}
    ledger.resolve_chapter(8, CH8, RAW8, workers=1)
    base = root / "entity"
    entities = {e["canonical"]: e for e in el.read_json(base / "entities.json", [])}
    assert entities["作家小姐"]["status"] == "merged" and entities["作家小姐"]["merged_into"] == "e002"   # the earlier record survives
    assert entities["脑内女声"]["named"] is False and entities["脑内女声"]["kind"] == "spirit"
    assert el.read_json(base / "merges.json", []) == [{"claim": "c0007-01", "from": "e005", "into": "e002", "chapter": 7}]
    claims = {c["id"]: c for c in el.read_json(base / "claims.json", [])}
    assert claims["c0007-01"]["status"] == "accepted" and claims["c0008-01"]["status"] == "pending"   # kept from the reader: a person decides
    pages = el.pending_pages(root, {7: CH7, 8: CH8})   # the page a person reads before deciding
    assert [p.name for p in pages] == ["pending_ch_0008.html"] and "艾琳娜的灵魂进入了薇奥拉的身体" in pages[0].read_text(encoding="utf-8")
    # grow_bible's rows come from the ledger: counts per chapter, named or not, seen or only spoken of
    rows = {r["name"]: r for r in ledger.scan_rows(7)}
    assert rows["薇奥拉公主"] == {"name": "薇奥拉公主", "kind": "具名角色", "mentions": 3, "speaks_or_close_up": True}
    assert rows["脑内女声"]["kind"] == "称呼或身份" and rows["脑内女声"]["speaks_or_close_up"] is True
    # a fresh load sees the merged forms and resolves the old name to the survivor
    again = el.Ledger(root)
    assert again.forms["e002"] == {"薇奥拉公主", "作家小姐", "薇奥拉"} and again.by_name["作家小姐"]["id"] == "e002"
    index = el.build_index(root)
    row = {r["name"]: r for r in index["characters"]}
    assert "作家小姐" not in row and row["薇奥拉公主"]["forms"] == {"作家小姐": 2, "薇奥拉公主": 1, "薇奥拉": 1} and row["薇奥拉公主"]["merged"] == ["e005"]
    assert row["莱恩·格雷"]["tier"] == "lead"
    shot = el.snapshot(root, 7)
    cast = {r["name"]: r for r in shot["cast"]}
    assert cast["薇奥拉公主"]["presence"] == "on_stage" and cast["薇奥拉公主"]["names_spoken"] == ["作家小姐", "薇奥拉公主"]
    assert cast["脑内女声"]["presence"] == "voice"
    # chapter 8's secrets (a body swap, a hidden kinship) are not yet disclosed at 7; the unbacked spouse row was dropped
    assert [h["type"] for h in shot["must_not_reveal"]] == ["occupies_body", "kinship_sibling"]
    assert shot["relations"] == [{"from": "莱恩·格雷", "to": "薇奥拉公主", "bases": {}, "stance": "romantic_interest", "address": ["殿下"]}]
    shot8 = el.snapshot(root, 8)
    assert shot8["must_not_reveal"] == [] and {(r["from"], r["to"]): r["bases"] for r in shot8["relations"]}[("艾琳娜", "薇奥拉公主")] == {"kinship_sibling": 1}
    assert len(index["relations"]) == 2 and any(d["what"] == "relation" for d in el.read_json(base / "dropped.json", []))
    # accept the body swap by hand: 艾琳娜 then acts through 薇奥拉's body
    claims["c0008-01"]["status"] = "accepted"
    el.write_json(base / "claims.json", list(claims.values()))
    cast8 = {r["name"]: r for r in el.snapshot(root, 8)["cast"]}
    assert cast8["艾琳娜"]["body"] == "e002" and cast8["艾琳娜"]["acts_through_other_body"] and cast8["琥珀·高德"]["body"] == "e004"
    # a person rejects the merge: 作家小姐 is her own record again, with her own forms, the claim is rejected,
    # and the decision is kept apart so that reading chapter 7 again does not merge them back
    assert el.Ledger(root).decide("c0007-01", "rejected", "另一位作家")
    back = el.Ledger(root)
    assert back.by_id["e005"]["status"] == "active" and back.forms["e005"] == {"作家小姐"} and back.forms["e002"] == {"薇奥拉公主", "薇奥拉"}
    assert {c["id"]: c["status"] for c in back.claims}["c0007-01"] == "rejected" and back.merges == []
    assert [c["decision"] for c in el.read_json(base / "corrections.json", [])] == ["rejected"]
    back.resolve_chapter(7, CH7, RAW7, workers=1)
    assert back.by_id["e005"]["status"] == "active" and back.merges == []
    assert {c["id"]: (c["status"], c.get("corrected")) for c in back.claims}["c0007-01"] == ("rejected", "另一位作家")
    # and accepting the hidden body swap by hand merges nothing (occupies_body is not same_as) but binds the body
    assert back.decide("c0008-01", "accepted")
    assert {r["name"]: r["body"] for r in el.snapshot(root, 8)["cast"]}["艾琳娜"] == "e002"


def test_read_ahead_duplicates_are_reconciled_by_asking(tmp_path, monkeypatch):
    """Chapter 20 was extracted before chapter 19's new record existed, so it says NEW:路易斯小姐; the ledger asks
    whether 路易斯小姐 and 多洛茜·路易斯 are one person and merges into the earlier record when told so."""
    root = novel(tmp_path)
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": ""})
    asked = []

    def same(a, b, fa, fb, note=""):
        asked.append((a["canonical"], b["canonical"]))
        return {"same": "same" if {a["canonical"], b["canonical"]} == {"路易斯小姐", "多洛茜·路易斯"} else "unsure", "why": "同一位"}

    monkeypatch.setattr(el, "judge_same", same)
    ch19 = "多洛茜·路易斯推门进来。莱恩看着她。"
    ch20 = "路易斯小姐坐下了。莱恩点头。"
    ledger = el.Ledger(root)
    ledger.resolve_chapter(19, ch19, {"chapter": 19, "mentions": [{"form": "多洛茜·路易斯", "entity": "NEW:多洛茜·路易斯", "kind": "proper", "presence": "on_stage", "evidence": "多洛茜·路易斯推门进来"}],
                                     "new_entities": [{"name": "多洛茜·路易斯", "kind": "person", "named": True, "description": "作家", "evidence": "多洛茜·路易斯推门进来"}], "claims": []}, workers=1)
    summary = ledger.resolve_chapter(20, ch20, {"chapter": 20, "mentions": [{"form": "路易斯小姐", "entity": "NEW:路易斯小姐", "kind": "proper", "presence": "on_stage", "evidence": "路易斯小姐坐下了"}],
                                               "new_entities": [{"name": "路易斯小姐", "kind": "person", "named": True, "description": "", "evidence": "路易斯小姐坐下了"}], "claims": []}, workers=1)
    assert asked == [("路易斯小姐", "多洛茜·路易斯")] and summary["merged"] == ["路易斯小姐→多洛茜·路易斯"] and summary["new"] == []
    assert ledger.by_name["路易斯小姐"]["id"] == "e005" and ledger.forms["e005"] == {"多洛茜·路易斯", "路易斯小姐"}
    claim = [c for c in ledger.claims if c.get("source") == "reconcile"][0]
    assert claim["type"] == "same_as" and claim["status"] == "accepted" and claim["subject"] == "e006" and claim["object"] == "e005"


def test_a_bridging_form_asks_once_and_a_shared_surname_does_not(tmp_path, monkeypatch):
    """艾琳娜·路易斯 carries 艾琳娜 (the bible's) and 路易斯 (多洛茜·路易斯's): the ledger asks whether they are one
    person, remembers the answer, and never asks about 格雷先生 for the two 格雷 who merely share a surname."""
    root = novel(tmp_path)
    (root / "story_bible.json").write_text(json.dumps({"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "薇奥拉公主", "role": ""}, {"name": "艾琳娜", "role": ""}, {"name": "奥斯文·格雷", "role": ""}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": ""})
    asked = []
    monkeypatch.setattr(el, "judge_same", lambda a, b, fa, fb, note="": (asked.append((a["canonical"], b["canonical"], note)) or {"same": "unsure", "why": "笔名？"}))
    ledger = el.Ledger(root)
    ch19 = "多洛茜·路易斯推门进来。格雷先生点头。"
    ledger.resolve_chapter(19, ch19, {"chapter": 19, "mentions": [
        {"form": "多洛茜·路易斯", "entity": "NEW:多洛茜·路易斯", "kind": "proper", "presence": "on_stage", "evidence": "多洛茜·路易斯推门进来"},
        {"form": "格雷先生", "entity": "e001", "kind": "proper", "presence": "on_stage", "evidence": "格雷先生点头"}],
        "new_entities": [{"name": "多洛茜·路易斯", "kind": "person", "named": True, "description": "作家", "evidence": "多洛茜·路易斯推门进来"}], "claims": []}, workers=1)
    assert asked == []   # 格雷先生 is shared by 莱恩·格雷 and 奥斯文·格雷: not a bridge
    ch74 = "艾琳娜·路易斯小姐坐下了。"
    ledger.resolve_chapter(74, ch74, {"chapter": 74, "mentions": [
        {"form": "艾琳娜·路易斯小姐", "entity": "e005", "kind": "proper", "presence": "on_stage", "evidence": "艾琳娜·路易斯小姐坐下了"}],
        "new_entities": [], "claims": []}, workers=1)
    assert asked == [("多洛茜·路易斯", "艾琳娜", "艾琳娜·路易斯小姐坐下了")]
    claim = [c for c in ledger.claims if c.get("source") == "bridge"][0]
    assert claim["status"] == "pending" and el.read_json(root / "entity" / "asked.json", {}) == {"e003|e005": "unsure"}
    ledger.resolve_chapter(75, "艾琳娜·路易斯又来了。", {"chapter": 75, "mentions": [
        {"form": "艾琳娜·路易斯", "entity": "e005", "kind": "proper", "presence": "on_stage", "evidence": "艾琳娜·路易斯又来了"}],
        "new_entities": [], "claims": []}, workers=1)
    assert len(asked) == 1   # remembered


def test_a_form_on_a_stranger_record_is_relinked_and_a_label_never_absorbs_a_person(tmp_path, monkeypatch):
    """路易斯小姐 put on 那位女士 (no shared name piece) is asked about once and moved to 多洛茜·路易斯; a text claim
    that 杰克·德昂 is 六秘环师 (a rank in the bible's cast list) waits for a person instead of merging."""
    root = novel(tmp_path)
    (root / "story_bible.json").write_text(json.dumps({"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "那位女士", "role": ""}, {"name": "六秘环师", "role": ""}, {"name": "多洛茜·路易斯", "role": "作家"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": "并列主语"})
    links = []
    monkeypatch.setattr(el, "judge_link", lambda form, evidence, assigned, options, forms: (links.append((form, assigned["canonical"], [o["canonical"] for o in options]))
                                                                                          or {"entity": "e004", "why": "路易斯就是多洛茜·路易斯"}))
    ledger = el.Ledger(root)
    text = "路易斯小姐坐下了。六秘环师杰克·德昂笑道。"
    raw = {"chapter": 20, "mentions": [
        {"form": "路易斯小姐", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "路易斯小姐坐下了"},
        {"form": "杰克·德昂", "entity": "NEW:杰克·德昂", "kind": "proper", "presence": "on_stage", "evidence": "六秘环师杰克·德昂笑道"}],
        "new_entities": [{"name": "杰克·德昂", "kind": "person", "named": True, "description": "秘环师", "evidence": "六秘环师杰克·德昂笑道"}],
        "claims": [{"type": "same_as", "subject": "NEW:杰克·德昂", "object": "e003", "scope": "reality", "hidden_from_reader": False, "evidence": "六秘环师杰克·德昂笑道"}]}
    ledger.resolve_chapter(20, text, raw, workers=1)
    assert links == [("路易斯小姐", "那位女士", ["多洛茜·路易斯"])]
    rows = {m["form"]: m["entity"] for m in el.read_json(root / "entity" / "mentions" / "ch_0020.json", [])}
    assert rows["路易斯小姐"] == "e004" and ledger.forms["e004"] == {"多洛茜·路易斯", "路易斯小姐"} and "路易斯小姐" not in ledger.forms["e002"]
    assert ledger.asked["路易斯小姐|e002"] == "e004"
    claim = [c for c in ledger.claims if c["type"] == "same_as"][0]
    assert claim["status"] == "pending" and "泛称" in claim["why"] and ledger.merges == []
    ledger.resolve_chapter(21, "路易斯小姐又来了。", {"chapter": 21, "mentions": [
        {"form": "路易斯小姐", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "路易斯小姐又来了"}], "new_entities": [], "claims": []}, workers=1)
    assert len(links) == 1 and {m["form"]: m["entity"] for m in el.read_json(root / "entity" / "mentions" / "ch_0021.json", [])}["路易斯小姐"] == "e004"
    # a person accepting the rank claim keeps the name, not the label, as the survivor
    assert ledger.decide(claim["id"], "accepted") and ledger.by_id["e003"]["merged_into"] == "e005" and ledger.by_id["e005"]["canonical"] == "杰克·德昂"


def test_generic_bible_records_are_deduplicated_by_description(tmp_path, monkeypatch):
    root = novel(tmp_path)
    (root / "story_bible.json").write_text(json.dumps({"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "那位女士", "role": "", "appearance": "莱恩脑海里说话的女声"},
        {"name": "秘女", "role": "", "appearance": "脑内的神秘女性声音"}, {"name": "白猫", "role": "", "appearance": "一只白猫"}]},
        ensure_ascii=False), encoding="utf-8")
    votes = {"e002": ["e003"], "e003": ["e002"], "e004": []}
    monkeypatch.setattr(el, "ask_json", lambda parts, schema, name, max_tokens: {"same_ids": votes[parts[0]["text"].split("目标记录：[")[1][:4]], "why": "都是脑内女声"})
    ledger = el.Ledger(root)
    made = el.dedup_generic(ledger, workers=1)
    # the bible's descriptions are invented by the card builder: a match is a question for a person, never a merge
    assert [(r["subject"], r["object"], r["status"], r["verdict"]) for r in made] == [("e003", "e002", "pending", "supports")]
    assert ledger.by_id["e003"]["status"] == "active" and ledger.merges == []
    assert ledger.decide("c0000-01", "accepted") and ledger.by_id["e003"]["merged_into"] == "e002"


def test_a_reading_made_before_its_people_existed_is_made_again(tmp_path, monkeypatch):
    """Chapter 21 was extracted while the ledger did not yet hold 多洛茜·路易斯 (added by chapter 19's resolution);
    her name is in chapter 21, so the chapter is read again before it is resolved."""
    root = novel(tmp_path)
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": ""})
    ledger = el.Ledger(root)
    calls = []

    def fake_extract(chapter, text, candidates, forms):
        calls.append((chapter, [e["canonical"] for e in candidates]))
        fresh = any(e["canonical"] == "多洛茜·路易斯" for e in candidates)
        return {"chapter": chapter, "mentions": [{"form": "路易斯小姐", "entity": "e005" if fresh else "e002", "entity_name": "多洛茜·路易斯" if fresh else "薇奥拉公主",
                                                  "kind": "proper", "presence": "on_stage", "evidence": "路易斯小姐坐下了"}],
                "new_entities": [], "claims": [], "relations": []}

    monkeypatch.setattr(el, "extract_chapter", fake_extract)
    ch21 = "路易斯小姐坐下了。"
    early = ledger.extract(21, ch21)                       # read ahead: the ledger ends at e004
    assert early["known_through"] == 4
    ledger.resolve_chapter(19, "多洛茜·路易斯推门进来。", {"chapter": 19, "mentions": [
        {"form": "多洛茜·路易斯", "entity": "NEW:多洛茜·路易斯", "kind": "proper", "presence": "on_stage", "evidence": "多洛茜·路易斯推门进来"}],
        "new_entities": [{"name": "多洛茜·路易斯", "kind": "person", "named": True, "description": "作家", "evidence": "多洛茜·路易斯推门进来"}],
        "claims": []}, workers=1)
    monkeypatch.setattr(el, "judge_link", lambda *a, **k: {"entity": "UNCERTAIN", "why": "should not be asked"})
    summary = ledger.resolve_chapter(21, ch21, early, workers=1)
    assert summary["reread"] == ["多洛茜·路易斯"] and calls[-1][0] == 21 and "多洛茜·路易斯" in calls[-1][1]
    assert {m["form"]: m["entity"] for m in el.read_json(root / "entity" / "mentions" / "ch_0021.json", [])} == {"路易斯小姐": "e005"}
    assert el.read_json(root / "entity" / "raw" / "ch_0021.json", {}).get("fresh") is True


def test_settling_asks_two_judges_with_the_whole_book(tmp_path, monkeypatch):
    """A pending same_as is judged again with the book's evidence; two 'supports' merge it, two 'contradicts'
    reject it, a split leaves it pending with both answers noted; a person's decision is never re-asked."""
    root = novel(tmp_path)
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "insufficient", "why": "单章不够"})
    ledger = el.Ledger(root)
    ledger.resolve_chapter(7, CH7, {**RAW7, "claims": [
        {"type": "same_as", "subject": "NEW:作家小姐", "object": "e002", "scope": "reality", "hidden_from_reader": False, "evidence": "原来作家小姐就是薇奥拉"},
        {"type": "same_as", "subject": "NEW:脑内女声", "object": "e003", "scope": "reality", "hidden_from_reader": False, "evidence": "脑内的女声说：别信她"},
        {"type": "lookalike", "subject": "e001", "object": "e004", "scope": "reality", "hidden_from_reader": False, "evidence": "莱恩点头"}]}, workers=1)
    assert all(c["status"] == "pending" for c in ledger.claims) and len(ledger.claims) == 3
    packs = []

    def judge(pack, rules):
        packs.append(pack)
        if "作家小姐" in pack.split("\n")[0]:
            return {"verdict": "supports", "why": "同一人"}
        if "脑内女声" in pack.split("\n")[0]:
            return {"verdict": "contradicts", "why": "不同"}
        return {"verdict": "supports" if "审稿人" not in rules else "insufficient", "why": "分歧"}

    monkeypatch.setattr(el, "judge_settle", judge)
    ledger.decide([c for c in ledger.claims if c["type"] == "lookalike"][0]["id"], "rejected", "人定的")   # a standing decision: not re-asked
    rows = el.settle_pending(ledger, workers=1)
    by = {(r["subject"], r["object"]): r["status"] for r in rows}
    assert by[("作家小姐", "薇奥拉公主")] == "accepted" and by[("脑内女声", "艾琳娜")] == "rejected" and len(rows) == 2
    assert ledger.by_id["e005"]["merged_into"] == "e002" and ledger.by_id["e006"]["status"] == "active"
    assert all("两人同时在场的章" in p and "记录甲" in p for p in packs) and len(packs) == 4
    statuses = {c["type"]: c["status"] for c in ledger.claims}
    assert statuses["lookalike"] == "rejected" and all("全书复判" in c.get("settled", "") for c in ledger.claims if c["type"] == "same_as")
    # the settled outcomes are remembered as corrections, so reading chapter 7 again keeps them
    assert {(k["decision"], k.get("by")) for k in ledger.corrections} == {("rejected", "human"), ("accepted", "settle"), ("rejected", "settle")}
    ledger.resolve_chapter(7, CH7, {**RAW7, "claims": [
        {"type": "same_as", "subject": "NEW:脑内女声", "object": "e003", "scope": "reality", "hidden_from_reader": False, "evidence": "脑内的女声说：别信她"}]}, workers=1)
    assert {c["status"] for c in ledger.claims if c["type"] == "same_as" and "脑内女声" in ledger.name_of(c["subject"])} == {"rejected"}


def test_settling_never_lets_a_label_absorb_a_person_or_a_body_claim_without_a_name(tmp_path, monkeypatch):
    root = novel(tmp_path)
    (root / "story_bible.json").write_text(json.dumps({"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "秘女", "role": ""}, {"name": "黛芙妮", "role": ""}, {"name": "艾琳娜", "role": ""}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "insufficient", "why": ""})
    monkeypatch.setattr(el, "judge_settle", lambda pack, rules: {"verdict": "supports", "why": "两个判官都同意"})
    ledger = el.Ledger(root)
    text = "红月秘女黛芙妮小姐来了。薇奥拉通过我的身体和你见面。"
    ledger.resolve_chapter(46, text, {"chapter": 46, "mentions": [
        {"form": "黛芙妮", "entity": "e003", "kind": "proper", "presence": "on_stage", "evidence": "红月秘女黛芙妮小姐来了"},
        {"form": "艾琳娜", "entity": "e004", "kind": "proper", "presence": "on_stage", "evidence": "薇奥拉通过我的身体和你见面"}],
        "new_entities": [], "claims": [
            {"type": "same_as", "subject": "e003", "object": "e002", "scope": "reality", "hidden_from_reader": False, "evidence": "红月秘女黛芙妮小姐来了"},
            {"type": "occupies_body", "subject": "e001", "object": "e004", "scope": "reality", "hidden_from_reader": False, "evidence": "薇奥拉通过我的身体和你见面"}]}, workers=1)
    packs = []

    def judge(pack, rules):
        packs.append(pack)
        # the two settling judges agree; the targeted body question (no quotation names the owner) says no
        if rules is el.SETTLE_RULES_BODY:
            return {"verdict": "contradicts", "why": "原文没点名身体主人"}
        return {"verdict": "supports", "why": "两个判官都同意"}
    monkeypatch.setattr(el, "judge_settle", judge)
    rows = {r["type"]: r for r in el.settle_pending(ledger, workers=1)}
    # a label in the bible (秘女) is asked about as a label: the pack says it must denote one person in the whole book
    assert any("「秘女」是称呼或身份" in p for p in packs)
    assert rows["same_as"]["status"] == "accepted" and ledger.by_id["e002"]["merged_into"] == "e003"   # judges said yes: the label folds into the name
    # no human queue: the body claim is decided by the targeted question, not parked
    assert rows["occupies_body"]["status"] == "rejected" and "换身体专项复核 contradicts" in rows["occupies_body"]["why"]
    assert all(c.get("priority") for c in ledger.claims if c["type"] == "occupies_body")
    assert not [c for c in ledger.claims if c.get("status") == "pending"]


def test_a_split_vote_goes_to_a_third_judge_and_nothing_stays_pending(tmp_path, monkeypatch):
    root = novel(tmp_path)
    (root / "story_bible.json").write_text(json.dumps({"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "秘女", "role": ""}, {"name": "黛芙妮", "role": ""}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "insufficient", "why": ""})
    ledger = el.Ledger(root)
    ledger.resolve_chapter(46, "红月秘女黛芙妮小姐来了。", {"chapter": 46, "mentions": [
        {"form": "黛芙妮", "entity": "e003", "kind": "proper", "presence": "on_stage", "evidence": "红月秘女黛芙妮小姐来了"}],
        "new_entities": [], "claims": [
            {"type": "same_as", "subject": "e003", "object": "e002", "scope": "reality", "hidden_from_reader": False, "evidence": "红月秘女黛芙妮小姐来了"}]}, workers=1)
    asked = []

    def judge(pack, rules):
        asked.append(rules)
        if rules is el.SETTLE_RULES:
            return {"verdict": "supports", "why": "像"}
        if rules is el.SETTLE_RULES_B:
            return {"verdict": "contradicts", "why": "不像"}
        return {"verdict": "contradicts", "why": "终审：分开处理"}
    monkeypatch.setattr(el, "judge_settle", judge)
    rows = {r["type"]: r for r in el.settle_pending(ledger, workers=1)}
    assert el.SETTLE_RULES_TIEBREAK in asked
    assert rows["same_as"]["status"] == "rejected" and "三审定 contradicts" in rows["same_as"]["why"]
    assert ledger.by_id["e002"].get("merged_into") is None
    assert not [c for c in ledger.claims if c.get("status") == "pending"]


def test_merge_keeps_the_numerically_earlier_record(tmp_path, monkeypatch):
    root = novel(tmp_path)
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": ""})
    ledger = el.Ledger(root)
    for k in range(5, 1001):  # push the ledger past e999 so lexical order would be wrong
        ledger._add_entity(f"路人{k}", "person", False, "", 1)
    ledger.resolve_chapter(7, CH7, {**RAW7, "mentions": RAW7["mentions"][:2]}, workers=1)
    assert ledger.by_id["e1001"]["merged_into"] == "e002" and ledger.by_id["e002"]["status"] == "active"


def test_settling_closes_what_it_cannot_ask_about(tmp_path, monkeypatch):
    root = novel(tmp_path)
    (root / "story_bible.json").write_text(json.dumps({"characters": [{"name": "莱恩·格雷", "role": "主角"}, {"name": "黛芙妮", "role": ""}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "insufficient", "why": ""})
    monkeypatch.setattr(el, "judge_settle", lambda pack, rules: {"verdict": "supports", "why": ""})
    ledger = el.Ledger(root)
    ledger.claims.append({"id": "c-1", "chapter": 3, "type": "death", "subject": "e001", "object": "", "scope": "reality", "hidden_from_reader": False,
                          "evidence": "他死了", "status": "pending"})
    ledger.claims.append({"id": "c-2", "chapter": 3, "type": "same_as", "subject": "e002", "object": "e002", "scope": "reality", "hidden_from_reader": False,
                          "evidence": "同一人", "status": "pending"})
    rows = {r["id"]: r for r in el.settle_pending(ledger, workers=1)}
    assert rows["c-1"]["status"] == "closed" and rows["c-2"]["status"] == "accepted"
    assert not [c for c in ledger.claims if c.get("status") == "pending"]
