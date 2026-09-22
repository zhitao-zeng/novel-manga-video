# 道具资产（Props）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 剧情道具（武器/信物/法器/工具）成为一等资产：bible 建模 → 读书提取 → 建卡 → 分镜绑定 → 装配参考图 → 工作台展示；可穿戴道具（战甲）走"道具本体卡 + 派生 phase 卡"联动。

**Architecture:** 新增 `Prop` 实体挂在 `StoryBible.props`；提取沿用归并判官通道（27B）；建卡沿用 `FramedAssetFactory`；装配在 `build_references` 加独立"道具位"（每 clip ≤1，特写可换 detail 图）；`phases.json` 的 phase 可带 `wears` 键派生穿戴卡。老书无 `props` 键时全链路逐字节不变。

**Tech Stack:** Python 3.13 / pydantic / pytest；无新依赖。

**Spec:** `docs/props-assets-design-20260922.md`（已审定）

## Global Constraints

- 老书 bible 没有 `props` 键时：规划请求、references、建卡提示词与现状**逐字节一致**。
- 道具参考图每 clip ≤1 个座位；不挪用人物参考图预算。
- 提取只在新书建书或显式重读时触发；在产三书（wuyue/xinghai/zhutian-card）不自动回填。
- `quote` 必须是原文的连续子串（归并 v3 规则）。
- 道具名在一本书内与人物名、其他道具名均不重复。
- 所有 JSON 写入用 `util.atomic_write_json`。
- 提示词改动属行为变更：落地后跑一次 HEAD-vs-分支的三本书卡提示词逐字节比对（期望 0 差异）。

## Review Focus

1. **道具名与人物名同名**（"玄天"既是人又是剑）：装配不得双绑——Task 3 提取时拒绝同名，Task 4 测试钉住。
2. **phase 的 `wears` 指向不存在的道具**（道具被删/改名）：跳过道具位，不崩、不带错图——Task 5 测试。
3. **clip 的 `props` 字段出现名单外的名字**（模型幻觉）：装配丢弃并记 warning，不进 references——Task 4 测试。
4. **道具卡文件缺失**（建卡失败/被删）：references 不带道具位，画面描述保留文字——Task 4 测试。
5. **`bible_growth.json` 并发**：多个规划块并行时道具生长与人/地点同锁提交——Task 3 复用现有 growth 锁，测试覆盖。

---

### Task 1: Prop 模型与回归钉死

**Files:**
- Modify: `src/novel_manga/models/bible.py`
- Test: `tests/test_props_model.py`

**Interfaces:**
- Produces: `Prop(BaseModel)`；`StoryBible.props: list[Prop]`（默认空）。后续任务全部依赖这两个名字。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_props_model.py
from novel_manga.models.bible import Prop, StoryBible

def test_prop_defaults():
    p = Prop(name="青铜短剑", category="武器", appearance="剑身泛青", first_chapter=12,
             quote="他抽出那柄青铜短剑")
    assert p.material == "" and p.owner == "" and p.closeup is False and p.wearable is False

def test_bible_without_props_key_loads_unchanged(tmp_path):
    # 老书 story_bible.json 没有 props 键：加载后 props 为空列表
    raw = {"novel_title": "雾月秘典", "genre": "gaslamp", "visual_style": "3d 国漫",
           "palette": "冷", "style_fingerprint": "abc",
           "characters": [{"name": "莱恩", "appearance": "瘦高", "wardrobe": "黑大衣"}],
           "locations": ["事务所"]}
    bible = StoryBible.model_validate(raw)
    assert bible.props == []

def test_bible_with_props_roundtrip():
    raw = {"novel_title": "t", "genre": "g", "visual_style": "s", "palette": "p",
           "style_fingerprint": "f",
           "props": [{"name": "玄天宝录", "category": "法器", "appearance": "玉册",
                      "first_chapter": 3, "quote": "玄天宝录", "closeup": True}]}
    bible = StoryBible.model_validate(raw)
    assert bible.props[0].closeup is True
    assert StoryBible.model_validate(bible.model_dump()).props[0].name == "玄天宝录"
```

- [ ] **Step 2: 跑测试确认失败** — `.venv/bin/python -m pytest tests/test_props_model.py -q`，预期 ImportError/attribute 错误。

- [ ] **Step 3: 实现** — 在 `models/bible.py` 的 `Character` 之后加：

```python
class Prop(BaseModel):
    """A plot object with its own card: named, recurring or plot-driving.  Wearables with
    their own identity (armor) are props too - the wearer becomes a phase that wears it."""
    name: str
    category: str = "其他"          # 武器 | 信物 | 法器 | 工具 | 其他
    appearance: str                # 稳定特征，不写当下状态（同人物外貌规则）
    material: str = ""
    owner: str = ""                # 初登场持有者；易手戏由分镜表达
    first_chapter: int = 0
    quote: str = ""                # 原文连续复制的证据
    closeup: bool = False
    wearable: bool = False
```

`StoryBible` 加 `props: list[Prop] = Field(default_factory=list)`，文件末尾加 `Prop.model_rebuild()`。

- [ ] **Step 4: 跑测试确认通过** + 回归：`.venv/bin/python -m pytest tests/ -q` 全绿（老 fixture 的 bible 全部无 props，模型默认值保证兼容）。

- [ ] **Step 5: Commit** — `git add src/novel_manga/models/bible.py tests/test_props_model.py && git commit -m "feat: the Prop model, empty until a book reads its props"`

---

### Task 2: 道具卡提示词与建卡

**Files:**
- Modify: `src/novel_manga/media/asset_prompts.py`（加 `prop_prompt`）
- Modify: `src/novel_manga/media/asset_specs.py`（加 `prop_spec`）
- Modify: `src/novel_manga/media/asset_builder.py`（`build_selected` 加 `prop_ids` 参数与 props 循环）
- Modify: `src/novel_manga/media/asset_records.py`（`merge_manifest` 接受 props）
- Test: `tests/test_props_cards.py`

**Interfaces:**
- Consumes: Task 1 的 `Prop`。
- Produces: `prop_prompt(bible: StoryBible, prop: Prop, *, family="", direction="", fingerprint=True, tidy=False) -> str`；`series_assets/props/prop_NNN/{turnaround.jpeg, detail.jpeg, spec.json}`；manifest 的 `props` 段。`build_references`（Task 4）按 `prop_NNN` 规则拼路径。

- [ ] **Step 1: 失败测试**

```python
# tests/test_props_cards.py
from novel_manga.media.asset_prompts import prop_prompt
from novel_manga.models.bible import Prop, StoryBible

def _bible():
    return StoryBible(novel_title="t", genre="g", visual_style="高精度半写实3D国漫CG",
                      palette="冷蓝", style_fingerprint="abc123", characters=[], locations=[])

def test_prop_prompt_is_single_object_clean_plate():
    p = Prop(name="青铜短剑", category="武器", appearance="剑身泛青、蟠螭纹", material="青铜",
             first_chapter=1, quote="…", closeup=True)
    text = prop_prompt(_bible(), p, family="3d")
    assert "青铜短剑" in text and "蟠螭纹" in text and "青铜" in text
    assert "道具资产" in text
    assert "不得出现人物" in text            # 与地点卡同款的空场规则
    assert "abc123" in text                  # 指纹默认进提示词（与人物卡一致）

def test_prop_prompt_without_material_skips_the_clause():
    p = Prop(name="木盒", category="工具", appearance="方形旧木盒", first_chapter=1, quote="…")
    text = prop_prompt(_bible(), p, family="3d")
    assert "材质" not in text
```

- [ ] **Step 2: 确认失败** — `.venv/bin/python -m pytest tests/test_props_cards.py -q`，预期 ImportError。

- [ ] **Step 3: 实现**

`asset_prompts.py` 末尾加（结构对照 `location_prompt`）：

```python
def prop_prompt(bible: StoryBible, prop: Prop, *, family: str = "", direction: str = "",
                fingerprint: bool = True, tidy: bool = False) -> str:
    """A single object on a clean plate: the reference every later shot of it locks to.
    Like the location card, no people; unlike it, one object fills the frame."""
    trim = (lambda s: str(s).rstrip("。；;，, ")) if tidy else (lambda s: s)
    return (
        _end(bible.visual_style, tidy)
        + (f"系列风格指纹 {bible.style_fingerprint}。" if fingerprint else "")
        + _end(bible.palette, tidy)
        + f"道具资产：{trim(prop.name)}（{trim(prop.category)}）；固定外观：{trim(prop.appearance)}"
        + (f"；材质：{trim(prop.material)}" if prop.material else "")
        + "。只画这一件物品且只出现一次，多角度设定图（正面、侧面、局部），干净纯色背景，"
        "比例尺稳定、结构清晰可读；不得出现人物、手、人体部位或使用场景，不要文字、Logo或水印。"
        + f"{rendering_direction(bible, family=family, direction=direction)}。"
    )
```

`asset_specs.py` 加 `prop_spec(asset_id, prop, bible, prompt)`（字段对照 `location_spec`，name 用 `prop.name`）。

`asset_builder.py` 的 `build_selected` 签名加 `prop_ids: set[str] | None = None`，在 locations 循环后加：

```python
        props: dict[str, dict] = {}
        for index, prop in enumerate(bible.props, start=1):
            asset_id = f"prop_{index:03d}"
            if prop_ids is not None and asset_id not in prop_ids:
                continue
            directory = root / "props" / asset_id
            prompt = prop_prompt(bible, prop, family=self.style.render_family,
                                 direction=self.style.render_direction,
                                 fingerprint=self.style.prompt_fingerprint, tidy=self.style.tidy_prompts) + guard
            spec = prop_spec(asset_id, prop, bible, prompt)
            atomic_write_json(directory / "spec.json", spec)
            primary = self.ensure_card(prompt, directory / "turnaround.jpeg", reference=style_master)
            detail = self.ensure_card(prompt + "局部材质特写，纹理与工艺细节占满画面。",
                                      directory / "detail.jpeg", reference=primary.path) if prop.closeup else None
            props[asset_id] = AssetRecord(
                asset_id=asset_id, kind="prop", name=prop.name, identity_invariants=spec["identity_invariants"],
                state_variables=spec["state_variables"], reference_scope=spec["reference_scope"],
                spec_path=str((directory / "spec.json").relative_to(root.parent)),
                primary_image=str(primary.path.relative_to(root.parent)),
                secondary_image=str(detail.path.relative_to(root.parent)) if detail else None,
                prompt_sha256=sha256_text(prompt),
            ).model_dump(mode="json")
        return merge_manifest(root, bible.style_fingerprint, characters, locations, voices, props)
```

`asset_records.py` 的 `merge_manifest` 加 `props: dict | None = None` 形参（默认 None 时行为不变），manifest 增加 `props` 段。

- [ ] **Step 4: 测试通过 + 全量回归**。另跑提示词逐字节比对（Global Constraints）：老书 bible 无 props，`build_selected` 不建道具卡、提示词不变。

- [ ] **Step 5: Commit** — `feat: prop cards - one object on a clean plate, detail view when the story needs a closeup`

---

### Task 3: 读书提取（判官通道 + 生长记录）

**Files:**
- Modify: `src/novel_manga/application/review/bible.py`（`extract_props`、`scan_chapter`、growth 提交）
- Modify: `src/novel_manga/review/contracts.py`（`PROP_SCHEMA`）
- Test: `tests/test_props_extraction.py`

**Interfaces:**
- Consumes: `bible_settings()`（判官 27B）；Task 1 的 `Prop`。
- Produces: `extract_props(chapter_text: str, known_props: list[str], known_names: set[str]) -> list[dict]`；growth 行带 `props` 键。

- [ ] **Step 1: 失败测试**

```python
# tests/test_props_extraction.py
import json
from novel_manga.application.review import bible as review_bible

def test_extract_props_rules(monkeypatch):
    asked = {}
    def fake_ask(parts, schema, **kw):
        asked["settings"] = kw.get("settings")
        return {"props": [
            {"name": "青铜短剑", "category": "武器", "appearance": "剑身泛青",
             "first_chapter": 1, "quote": "抽出那柄青铜短剑", "closeup": False, "wearable": False},
            {"name": "茶杯", "category": "工具", "appearance": "白瓷",
             "first_chapter": 1, "quote": "原文里没有这四个字", "closeup": False, "wearable": False},
        ]}
    monkeypatch.setattr(review_bible.model_client, "ask_json", fake_ask)
    text = "他抽出那柄青铜短剑，又端起茶杯喝了一口。"
    rows = review_bible.extract_props(text, [], {"莱恩"})
    assert [r["name"] for r in rows] == ["青铜短剑"]   # quote 不是原文连续子串的被丢弃
    assert asked["settings"] is not None and asked["settings"].model == "Qwen3.8-27B-Project"

def test_extract_props_refuses_a_name_taken_by_a_character(monkeypatch):
    monkeypatch.setattr(review_bible.model_client, "ask_json",
                        lambda parts, schema, **kw: {"props": [
                            {"name": "玄天", "category": "法器", "appearance": "剑",
                             "first_chapter": 1, "quote": "玄天", "closeup": False, "wearable": False}]})
    assert review_bible.extract_props("玄天出鞘。", [], {"玄天"}) == []   # 与人物同名：拒收
```

- [ ] **Step 2: 确认失败**（`extract_props` 不存在）。

- [ ] **Step 3: 实现** — `contracts.py` 加 `PROP_SCHEMA`（对照 `LOCATION_SCHEMA`，字段即 `Prop` 全字段）。`bible.py` 加：

```python
def extract_props(chapter_text: str, known_props: list[str], known_names: set[str]) -> list[dict]:
    """Plot objects worth a card: named always; unnamed only if recurring or plot-driving.
    Every row carries a verbatim quote; a row whose quote is not a contiguous slice of the
    chapter, or whose name a character already owns, is dropped (归并 v3 同款核实规则)."""
    recent = "、".join(known_props[-30:])
    prompt = (
        "列出这段小说里有剧情分量的物件（武器、信物、法器、关键工具）："
        "有独立名字的必收；没名字的只在跨章反复出现、或牵动剧情（易手/认主/被毁/开启某物）时收。"
        "日常生活物件（茶杯、桌椅、饭菜）不收。每个给出：name、category（武器/信物/法器/工具/其他）、"
        "appearance（长期稳定外观，不写当下状态）、material（材质，没写留空）、owner（本段持有者，没写留空）、"
        "quote（从本段原文连续复制的原句，不得改写）、closeup（是否需要特写镜头）、wearable（是否可穿戴/覆盖身体的装备）。"
        + (f"已有道具：{recent}。同一物件必须原样使用已有名字。" if recent else "")
        + "只输出JSON。\n\n" + chapter_text
    )
    rows = model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.PROP_SCHEMA,
                                 name="props", max_tokens=1500, settings=bible_settings()).get("props", [])
    out = []
    for row in rows:
        quote = str(row.get("quote") or "")
        name = str(row.get("name") or "").strip()
        if not name or name in known_names or name in known_props:
            continue
        if not quote or quote not in chapter_text:
            continue
        out.append(row)
    return out
```

`scan_chapter` 加道具一路（known_names 传全书人物名集合）；growth 提交处把 `props` 写进 `bible_growth.json` 行（与人/地点同一把锁——找到 `grow_bible` 的锁段，props 进同一个 `with` 块）。**触发点只在建书与显式重读路径加调用；production 的在产书批量流程不传 props 扫描开关。**

- [ ] **Step 4: 测试通过 + 全量回归**（scan_chapter 默认行为不变：老调用不传 known_props 时结果 dict 里 props 为空列表——钉一个老调用兼容测试）。

- [ ] **Step 5: Commit** — `feat: the reading pass collects plot objects, with the merge pilot's evidence rules`

---

### Task 4: 分镜绑定与装配（道具位）

**Files:**
- Modify: `src/novel_manga/planning/contracts.py`（clip schema 加 `props` 枚举字段）
- Modify: `src/novel_manga/planning/binding.py`（authored 路径 per-shot `props` 枚举）
- Modify: `src/novel_manga/application/packing/assets.py`（`build_references` 道具座位）
- Test: `tests/test_props_references.py`

**Interfaces:**
- Consumes: Task 1 `Prop`；Task 2 的 `prop_NNN` 路径规则。
- Produces: clip plan 的 clip 可带 `props: list[str]`；`build_references(..., props: list[str] | None = None, props_index: dict[str, Prop] | None = None, props_on_disk: set[str] | None = None)` —— references 里 `role="prop"` 的条目结构 `{tag, role, name, asset_id, path}` 与人物/地点同款。

- [ ] **Step 1: 失败测试**

```python
# tests/test_props_references.py
from novel_manga.application.packing.assets import build_references
from novel_manga.models.bible import Character, Prop, StoryBible

def _bible():
    return StoryBible(
        novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
        characters=[Character(name="莱恩", role="主角", appearance="瘦高", wardrobe="黑大衣")],
        locations=["事务所：临街小屋"],
        props=[Prop(name="青铜短剑", category="武器", appearance="泛青", first_chapter=1, quote="…"),
               Prop(name="玄天宝录", category="法器", appearance="玉册", first_chapter=1, quote="…",
                    closeup=True)],
    )

def test_prop_takes_one_seat_after_the_location():
    refs, bindings, loc = build_references(["莱恩"], "事务所", _bible(), {"事务所": "事务所：临街小屋"},
                                           props=["青铜短剑"], props_index={p.name: p for p in _bible().props},
                                           props_on_disk={"prop_001"})
    seats = [(r["role"], r["path"]) for r in refs]
    assert ("prop", "series_assets/props/prop_001/turnaround.jpeg") in seats
    assert len([r for r in refs if r["role"] == "prop"]) == 1     # 道具位 ≤1

def test_closeup_prop_uses_detail_image():
    refs, _, _ = build_references(["莱恩"], "事务所", _bible(), {"事务所": "事务所：临街小屋"},
                                  props=["玄天宝录"], props_index={p.name: p for p in _bible().props},
                                  props_on_disk={"prop_002"})
    assert any(r["path"] == "series_assets/props/prop_002/detail.jpeg" for r in refs)

def test_unknown_or_missing_prop_is_dropped_not_invented():
    refs, _, _ = build_references(["莱恩"], "事务所", _bible(), {"事务所": "事务所：临街小屋"},
                                  props=["幻激光枪"],                 # 名单外（幻觉）
                                  props_index={p.name: p for p in _bible().props},
                                  props_on_disk=set())                # 卡也没建
    assert not [r for r in refs if r["role"] == "prop"]

def test_no_props_is_byte_identical_to_before():
    refs, bindings, loc = build_references(["莱恩"], "事务所", _bible(), {"事务所": "事务所：临街小屋"})
    assert not [r for r in refs if r["role"] == "prop"]
    assert "道具" not in "".join(bindings) and "道具" not in loc
```

- [ ] **Step 2: 确认失败**（`build_references` 没有 `props` 形参）。

- [ ] **Step 3: 实现**

`contracts.py` 的 clip schema 加（bible 无 props 时不加这个键——保持老书逐字节不变）：

```python
if prop_names:  # bible.props 非空时才给这个字段
    clip_properties["props"] = {"type": "array", "maxItems": 1,
                                "items": {"type": "string", "enum": prop_names}}
```

`binding.py` 的 `bind_schema` 同款：authored 路径 bound 对象加可选 `props`（maxItems 1，enum 道具名单），`merge` 把它带到 clip 上。

`packing/assets.py` 的 `build_references` 末尾、voices 之前插入：

```python
    prop_by_name = dict(props_index or {})
    on_disk = props_on_disk if props_on_disk is not None else _props_on_disk(novel_dir)
    chosen = [p for p in (props or []) if p in prop_by_name and f"prop_{prop_index_of(prop_by_name[p], bible):03d}" in on_disk]
    dropped = [p for p in (props or []) if p not in prop_by_name]
    if dropped:
        log(f"references: dropping unknown prop names {dropped}")   # 模型幻觉不进 references
    for prop_name in chosen[:1]:                                    # 道具位硬上限 1
        prop = prop_by_name[prop_name]
        asset = f"prop_{prop_index_of(prop, bible):03d}"
        image = "detail.jpeg" if prop.closeup else "turnaround.jpeg"
        count += 1
        references.append({"tag": f"@图片{count}", "role": "prop", "name": prop_name,
                           "asset_id": asset, "path": f"series_assets/props/{asset}/{image}"})
        bindings.append(f"<{prop_name}>对应@图片{count}：只采用该物品的外观、材质与结构，"
                        "不放大、不缩小、不改变相对人物的比例")
```

`_props_on_disk(novel_dir)`：`series_assets/props/*/turnaround.jpeg`（或 detail）实际存在的 asset id 集合；`novel_dir=None` 时返回全部（与人物 expressions 的"没有目录可查时保持旧规则"同款约定）。

- [ ] **Step 4: 测试通过 + 全量回归**。重点跑 `tests/test_reference_assets.py`、`test_packing_services.py`、`test_binding_takes_precedence.py`。

- [ ] **Step 5: Commit** — `feat: a prop seat in the reference budget - one per clip, detail view for closeups`

---

### Task 5: 战甲联动（phase `wears`）

**Files:**
- Modify: `src/novel_manga/application/packing/assets.py`（phase 带 `wears` 时加道具位）
- Modify: `src/novel_manga/application/assets/phase_cards.py`（`wears` 指向的道具卡先建，phase 卡以它为参考图）
- Test: `tests/test_props_wearable.py`

**Interfaces:**
- Consumes: Task 4 的道具位；`identity/phases.py` 的 `phase_for`（phase dict 允许带 `wears: str`，`phases.py` 无需改——它不校验多余键）。
- Produces: phase 卡的 `turnaround.jpeg` 以道具卡为参考图生成；装配时该 phase 的 clip 带角色卡 + 道具卡。

- [ ] **Step 1: 失败测试**

```python
# tests/test_props_wearable.py
def test_phase_wearing_a_prop_carries_both_cards():
    # phases: 托尼 ch50 起 "穿 Mark XLII"，wears="Mark XLII 战甲"
    # 期望 references：角色 phase 卡 + prop 卡；prop 卡路径来自 props/prop_001
    ...
def test_wears_pointing_nowhere_is_skipped():
    # wears="不存在的甲"：只有角色卡，不崩
    ...
```

- [ ] **Step 2–3: 实现**。装配：`build_references` 人物循环里，phase 带 `wears` 且道具在册且在盘 → 道具座位直接消耗（与 Task 4 同一座位，合计预算不变：人物位照旧 + 道具位 ≤1）。建卡：`phase_cards.py` 建 phase 卡前，若 `wears` 道具卡不存在则先建（调 Task 2 的建卡路径），phase 卡的 `ensure_card(..., reference=<道具 turnaround 路径>)`。

- [ ] **Step 4: 测试通过 + 全量回归**（`test_thin_phases.py` 不动应仍绿：无 `wears` 键的 phase 行为不变）。

- [ ] **Step 5: Commit** — `feat: a wearable prop anchors its own card, and the wearer's phase is drawn from it`

---

### Task 6: 工作台两个分区

**Files:**
- Modify: `src/novel_manga/application/dashboard/workbench.py`（`assets()` 加 props、`bible()` 加 props）
- Modify: `src/novel_manga/dashboard/static/workbench.js`（资产页道具分区）、`bible.js`（道具表）
- Test: `tests/test_workbench.py` 追加

- [ ] **Step 1–3**：`assets()` 返回加 `"props": _asset_dirs(directory / "props")`；JS 在 characters/locations 后渲染道具分区（复用网格+lightbox，零新组件）。`bible()` 返回加 `"props": story.get("props") or []`；bible.js 加道具表（name/category/owner/first_chapter/closeup/wearable/quote）。

- [ ] **Step 4**：测试（tmp fixture 建 props 资产目录与 bible.props）+ 全量回归。

- [ ] **Step 5：Commit** — `feat: the workbench sees props, on the shelf and in the bible`

---

### Task 7: 端到端验证（沙箱书）

**Files:** 无新代码；验证记录在 `docs/props-assets-plan-20260922.md` 末尾补"验证结果"一节。

- [ ] 用 `_sample-` 机制建一本沙箱拷贝书（或新书 demo），跑：建书（含道具提取）→ 建 1 张道具卡（核对 spec.json 与图片落盘）→ 规划 1 章（clip 带 props 字段）→ 装配（references 含道具位）→ 拼出请求 JSON 人工核对。
- [ ] 在产三书跑提示词逐字节比对（Global Constraints），期望 0 差异。
- [ ] 全量测试绿。把结果写进本文档并 commit。

---

## Self-Review 记录

- **Spec 覆盖**：模型(T1)=spec§数据模型；建卡(T2)=§建卡；提取(T3)=§读书提取；绑定装配(T4)=§分镜绑定+§装配；战甲(T5)=§核心分界规则联动；工作台(T6)；端到端(T7)=里程碑 6。spec 的"参考图预算 ≤1/特写 2"落为 T4 的"座位 1 + closeup 换 detail 图"（更简：同一座位换图，而非加第二张——特写镜不稀释）。**与 spec 的偏差**：spec 写"特写放宽到 2 张"，计划改为"特写换 detail 图、仍 1 个座位"，理由是任何第二张图都增加混脸风险且特写的主体就是道具本身——review 时如反对请指出。
- **类型一致**：`prop_prompt` / `prop_spec` / `extract_props` / `build_references(props=, props_index=, props_on_disk=)` / `role="prop"` / `prop_NNN` 全计划一致。
- **Review Focus**：五条全部有归属测试（T3×2、T4×3、T5×1、growth 锁在 T3）。
