# 书级 profile 键位（tier 拆解，2026-09-30）

`tier`（fast/quality）是一个捆绑开关：480p/720p、是否建表情卡、两视角、集时长形态绑在一起，
且 conductor 派单硬编码 `--tier fast`，使书级声明在批量生产中不生效。现已拆成独立键；
`tier` 保留为遗留别名（`effective_profile` 展开，显式键优先）。

## 独立键

| 键 | 取值 | 控制 | 消费点 |
|---|---|---|---|
| `resolution` | `480p` / `720p` | 视频分辨率 | `application/rendering/flow.py` |
| `expressions` | bool | 是否建/用表情特写卡；false 时批量渲染走主角色单卡复用 | `media/assets.py`、`application/assets/cards.py`、`application/production/render.py` |
| `two_views`（已有） | `off` / `leads` / `all` | 第二视角参考卡；不再被 tier 压死 | `application/packing/context.py`、`packing/assets.py` |
| `episode_shape` | `flex` / `fixed` | flex=按章长定 75–150s 目标、超上限不返修；fixed=固定 90s 目标、超上限返修 | `planning/budget.py`、`planning/authored_brief.py` |

## 遗留别名

`tier: fast` ≡ `resolution: 480p, expressions: false, episode_shape: flex`；
`tier: quality` ≡ `resolution: 720p, expressions: true, episode_shape: fixed`。
两者都不含 two_views——fast 曾把它压成 0，现在由 `two_views` 键自己决定（默认 off）。
CLI `--tier` 与 conductor 派单已不传；`thin_batch --tier` 保留兼容别名。

## 生效层级

plan 快照（`chapter_script.json`/`clip_plan.json` 的 profile）→ 书盘 `profile.json` → CLI 参数。
重打包旧 plan 时 `context_for_plan` 读快照里的 `two_views`（无则用书盘默认）。

## 五本书的迁移（已完成）

| 书 | resolution | expressions | episode_shape | two_views |
|---|---|---|---|---|
| 十日终焉 shiri | 480p | false | flex | off |
| 诸天万象录 zhutian-card | 480p | false | flex | off |
| 星海龙途 xinghai | 720p | true | fixed | off |
| 雾月秘典 wuyue | 720p | true | fixed | off |
| 美漫 meiman-daoshi | 720p | true | fixed | off |

## 组合示例

“480p 也要双视角”现在可行：`{"resolution": "480p", "expressions": true, "two_views": "all", "episode_shape": "flex"}`。
表情卡是付费生成，放量前先在单集验证（`thin_batch --stage render --rerender`）近景收益。

## 顺带清理的死代码

- `GROW_STRIDE` 采样分支（常量为 1，永不触发）；
- `max_attempts = 2 if fast else …`（两档实际都是 2）；
- `call_model(fast=…)` 参数（函数体从未使用）；
- “可读文字”的 fast 豁免（并入 genre 的 `text_on_props: gate|report`）。
