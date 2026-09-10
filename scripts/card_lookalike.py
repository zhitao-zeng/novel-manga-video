#!/usr/bin/env python
"""Which character cards look like each other?

星海's two leads were drawn as the same kind of dragon - silver, bipedal, same wings and
horns - so the generator could not tell them apart whatever the prompt said, and 981 of their
1608 shared clips failed identity.  This asks a vision model the question directly, for every
pair of characters that actually share clips: put the two cards side by side, can a viewer
tell them apart in one glance?

Only pairs that appear together often enough to matter are checked.  Writes
outputs/<novel>/card_lookalikes.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from thin_review import ask_json, image_part  # noqa: E402

MIN_TOGETHER = 30
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["distinguishable", "why", "shared_traits"],
    "properties": {
        "distinguishable": {"type": "string", "enum": ["一眼可分", "要仔细看", "几乎一样"]},
        "why": {"type": "string"},
        "shared_traits": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
    },
}
RULES = ("下面是同一部作品里两个角色的设定卡。请判断：观众在一段视频里瞥一眼，能不能把这两个角色分开？\n"
         "一眼可分：体型、姿态（四足/双足）、主色或轮廓明显不同。\n"
         "要仔细看：主色接近或体型接近，靠细节（角的形状、瞳色、配饰）才能区分。\n"
         "几乎一样：同一种造型换了细节，视频里必然混淆。\n"
         "why 一句话说明依据，shared_traits 列出两者共有的显著特征。只输出 JSON。")


def pairs_of(novel: str) -> list[tuple[str, str]]:
    base = ROOT / "outputs" / novel
    names = {c["asset_id"]: c.get("name", "") for c in
             json.loads((base / "series_assets" / "manifest.json").read_text(encoding="utf-8")).get("characters", [])}
    together: Counter = Counter()
    for d in sorted(base.glob(f"{novel}_*")):
        plan = d / "clip_plan.json"
        if not plan.is_file():
            continue
        try:
            data = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for clip in data.get("clips", []):
            cast = sorted({r.get("asset_id") for r in clip.get("references") or [] if r.get("role") == "character"})
            for i, a in enumerate(cast):
                for b in cast[i + 1:]:
                    together[(a, b)] += 1
    return [(a, b) for (a, b), n in together.most_common() if n >= MIN_TOGETHER], names, together


novel = sys.argv[1] if len(sys.argv) > 1 else "xinghai"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
pairs, names, together = pairs_of(novel)
base = ROOT / "outputs" / novel / "series_assets" / "characters"
out = []
for a, b in pairs[:limit]:
    card_a, card_b = base / a / "turnaround.jpeg", base / b / "turnaround.jpeg"
    if not (card_a.is_file() and card_b.is_file()):
        continue
    answer = ask_json([{"type": "text", "text": RULES + f"\n\n第一张是「{names.get(a)}」，第二张是「{names.get(b)}」。"},
                       image_part(card_a, 768), image_part(card_b, 768)],
                      SCHEMA, name="lookalike", max_tokens=600)
    row = {"pair": [names.get(a), names.get(b)], "assets": [a, b], "together": together[(a, b)], **answer}
    out.append(row)
    print(f"{names.get(a)} + {names.get(b)}（同框 {row['together']}）: {row['distinguishable']} — {row['why'][:70]}", flush=True)
path = ROOT / "outputs" / novel / "card_lookalikes.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
bad = [r for r in out if r["distinguishable"] != "一眼可分"]
print(f"\n{len(out)} 对里，{len(bad)} 对不是一眼可分 → {path}")
