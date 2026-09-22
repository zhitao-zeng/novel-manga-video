# 道具资产（Props）完整版设计 · 2026-09-22

分支：`feat/props-assets`（worktree：`.claude/worktrees/props-assets`）。
状态：设计稿，待审。审完出实施计划。

## 背景与目标

现状（2026-09-22 核实）：

- `StoryBible` 只有 `characters` / `locations` 两类实体；道具只以三种形式存在：角色
  `signature_prop` 文本字段、分镜画面描述里的自然语言、`story/fields.py` 的画内物件处理。
- `references` 的 role 在真实 clip_plan 里只有 `character` / `location` / `voice` 三种。
- 后果：同一道具跨集外观不一致；贯穿性武器/信物无法绑定参考图；道具特写镜头没有锚点。

目标：剧情道具成为一等资产——跨集外观一致、特写可绑参考、不稀释人物身份、在产三书零影响。

非目标：老书道具回填、道具 QC 审查判定、道具音色。

## 核心分界规则

一个物件归属哪里，按它的戏判断：

| 物件 | 归属 | 判据 |
|---|---|---|
| 普通衣服/军装/礼服 | 角色 **phase**（已有机制） | 没有独立身份，戏在"谁穿着" |
| 武器/信物/法器/工具 | **道具资产** | 独立存在、独立镜头 |
| 战甲/机甲/法衣等可穿戴物 | **道具 + phase 联动** | 满足任一：有独立名字、会升级换代、会易手、有无人戏份 |

联动方式：可穿戴道具有本体卡（道具卡）；某角色穿上它 = 该角色的一个 phase，phase 卡以
道具卡为参考图生成（"画这个人，穿着这件道具"）。装甲外观的权威来源只有道具卡一份；
谁穿它只是派生视图。升级换代（Mark III → XLII）= 新道具条目 + 对应新 phase。

已有先例：phases 机制在生产中（"沈玄川 1406 章起白发，引用当章阶段的卡"），
`character_001-p2/-p3` 即阶段卡。本设计给它补的是"phase 由穿戴物驱动"的入口。

## 数据模型（`models/bible.py`）

```python
class Prop(BaseModel):
    name: str                    # "玄天宝录" / "青铜短剑"
    category: str                # 武器 | 信物 | 法器 | 工具 | 其他
    appearance: str              # 外观稳定特征（同人物外貌规则：不写当下状态）
    material: str = ""           # 材质质感（建卡提示词用）
    owner: str = ""              # 初登场持有者（易手戏由分镜表达，不跟卡走）
    first_chapter: int
    quote: str                   # 原文连续复制的证据（归并 v3 规则平移）
    closeup: bool = False        # 需要特写 → 建卡加材质细节视图
    wearable: bool = False       # 可穿戴（战甲类）→ phase 卡以它的卡为参考图生成
```

`StoryBible` 新增 `props: list[Prop] = []`。缺省为空——老书没有 `props` 键时全链路行为
必须与现状逐字节一致（回归测试钉死）。

phases.json 的 phase 条目加可选键 `wears: "<道具名>"`，指向可穿戴道具；装配时该 phase 的
clip 同时携带角色卡（人物位）与道具卡（道具位），预算规则见 §5。

## 读书提取（`application/review/bible.py`）

归并阶段多判一类"道具"，走 `judge_settings()`（与人物判定同源的 27B 判官）。收录规则
（平自读书归并试点 v2/v3 已验证的规则）：

1. 有独立名字的必收；
2. 没名字但跨 ≥3 章反复上镜的收；
3. 剧情事件驱动的收：易手 / 认主 / 被毁 / 开启什么；
4. 每条必须带 `quote`——从原文**连续复制**，不得改写/概括/拼接；
5. 每条判 `closeup`（是否需要特写镜头）与 `wearable`（是否可穿戴）。

道具名在一本书内必须唯一（绑定按名字解析，与人物名同款约定）；名字冲突时归并阶段
合并或改名，不留到装配期。

phases 机制判定出"某角色自某章起穿戴某可穿戴道具"的形态时，在该 phase 条目写
`wears: "<道具名>"`；形态不由可穿戴道具驱动的 phase 不写此键，行为与现状一致。

`bible_growth.json` 增加 `props` 键，与 characters/locations 同级。

**生效范围：只在新书建书或显式重读（`--rescan` 类开关）时提取。在产三书不自动回填。**

## 建卡（`media/asset_builder.py` 与画风包）

- 目录：`series_assets/props/prop_001/`，内含 `turnaround.jpeg`（单物多角度、干净背景）、
  `spec.json`；`closeup=true` 加 `detail.jpeg`（材质特写）。
- 资产 id 顺序与 `StoryBible.props` 顺序一致（与 characters 同款约定，id 稳定）。
- 画风包：道具卡措辞走默认模板；画风包可带自己的 `prop_card_suffix` 覆盖。不改现有
  `card_suffix` 语义，不加 `prompt_fingerprint` 到道具提示词以外的任何东西。
- 提示词属行为变更，落地前按项目惯例做 HEAD-vs-工作树逐字节比对（老书无 props，期望 0 差异）。

## 分镜绑定（`planning/`）

- 规划候选枚举加道具名单（与人物/地点同款）：模型只能从名单选，产出 `role="prop"` 的
  reference 绑定；同一道具按 distinct 名字整章解析一次（与 binding.py 现有的人名/场景名
  规则同款，防止第 3 镜和第 7 镜解析成不同道具）。
- 人写分镜表（XLSX）结构不变。

## 装配（`application/packing/assets.py` `build_references`）

- clip 命中道具 → 追加 `{tag: "@图片N", role: "prop", name, asset_id, path:
  "series_assets/props/<asset_id>/turnaround.jpeg"}`；`closeup` 道具的特写镜可换用
  `detail.jpeg`。
- **预算硬上限：每 clip 道具参考图 ≤1；道具特写镜放宽到 2。** 人物参考图规则一行不动
  （现有注释：超过两个人物各带双视图就会混脸——道具位是新预算，不是挪用人物预算）。
- phase 带 `wears` 时：角色卡占人物位，所穿道具卡占道具位，合计规则不变。
- 道具卡缺失/建卡失败 → 降级为纯文本描述（即现状），不阻塞出片。

## 工作台

- 资产页加"道具"分区（`_asset_dirs` 扫描器通用，接近白送）。
- bible 页加道具表（name/category/owner/first_chapter/quote/closeup/wearable）。

## 测试

- `Prop` schema 与默认值；
- 提取规则单测：四条收录规则、quote 必须是原文连续子串；
- 装配：含道具的 clip references 顺序与预算上限；`wears` phase 的双引用；
- 回归：无 `props` 键的老书 bible，规划请求与 references 逐字节不变；
- 建卡：道具卡提示词构造（画风包默认模板 + 覆盖）。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 参考图变多导致混脸 | 道具位硬上限 ≤1（特写 2）；人物规则不动 |
| 提取把杂物都收进来（满屏"桌子上的茶杯"） | 四条收录规则全部满足其一才收 + quote 可核实兜底 + 归并报告里列出每条收录依据（沿用试点做法） |
| 老书行为漂移 | 无 props 键时逐字节不变的回归测试；提取不在在产书触发 |
| 战甲 phase 卡与道具卡不一致 | 建卡顺序约束：phase 卡以道具卡为参考图生成，`wears` 指向的道具必须先建 |

## 里程碑（实施计划前的粗排）

1. 模型 + 回归钉死（无 props 老书不变）
2. 读书提取 + 生长记录
3. 建卡 + 画风包措辞
4. 绑定 + 装配 + 预算
5. 工作台两个分区
6. 用一本新书（或沙箱拷贝）端到端验证
