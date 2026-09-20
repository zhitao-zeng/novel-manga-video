# 画风包重构计划（2026-09-20）

把"画风"从一个 `3d|2d` 开关改成三个成套的画风包：**3D 国漫 / 唯美 / 真人**。
只改组织方式，不改任何一本在产小说生成的提示词字节。

## 1. 为什么要改

三种画风实际都在用，但只有一种进了流水线：

| 画风 | 提示词现在在哪 | 在流水线里吗 |
|---|---|---|
| 3D 国漫 | `application/profiles.py:37-41` `STYLE_VISUAL["3d"]` + `media/asset_style.py:6` `CARD_STYLE_SUFFIX_3D` | 是，三本书都用 |
| 唯美 | 沙箱 10 个文件各写一份 `STYLE`（otome-game cutscene），另有 `assets/STYLE_APPROVED.md` | 否 |
| 真人 | 沙箱 `render/live2_round.py:32,37`、`assets/gen_fts_live.py:32,66,69` | 否 |

`STYLE_VISUAL` 只有 `2d` / `3d` 两档，而 `2d` 的 `card_style_suffix_2d` 在五个 genre 文件里**全是空字符串**。

后果已经发生：复用诸天画风时把唯美的措辞（`luminous skin`、`soft catchlights`）混进了国漫卡，
见 `experiments/agent-skill-sandbox/HANDOFF-20260920.md` §2.3。画风没有单一归属，靠人记得抄哪份，必然串。

genre 文件同时兼着画风（`card_style_suffix_3d`/`_2d`）和题材（era、soften、anonymous_roles），是混乱的另一半。

## 2. 两个必须守住的约束

### 2.1 画风文本进了 style_fingerprint，动文本等于让全书卡片失效

```
bible.py:123   payload = title + style + "|".join(f"{c.name}:{c.appearance}:{c.wardrobe}" ...)
bible.py:124   return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
```

`style_fingerprint` 出现在每一条卡片提示词里（`media/asset_prompts.py:65` 和 `:96`）。
**`STYLE_VISUAL["3d"]` 的文本改动一个字，三本书所有卡片的提示词都变**，进而触发重画。
雾月 2043 集、星海 1600 集已交付，诸天 1309 张卡在用。

→ 重构必须保证 `STYLE_VISUAL["3d"]` 和 `STYLE_VISUAL["2d"]` 的字符串**逐字节不变**，只改它们存放的位置。

### 2.2 有两处靠字符串嗅探判画风

```
media/asset_builder.py:74        if "3D" in bible.visual_style or "三维" in bible.visual_style:
application/assets/phase_cards.py:116   同上
media/asset_prompts.py:7-11      if any(token in style for token in ("二维", "卡通", "赛璐璐", "2d")):
```

新增画风包必须显式声明自己走哪条分支，不能指望文本里碰巧有没有"3D"。
真人包的文本里没有"三维"，嗅探会把它判成非 3D——**结果恰好是对的，但这是巧合，不是设计**。

## 3. 目标结构

```
configs/styles/
  3d-guoman.json
  weimei.json
  live.json
```

每包四个键，对应现在散落的四处：

| 键 | 用在哪 | 现在的来源 |
|---|---|---|
| `visual_style` | `bible.visual_style`，进卡片提示词和指纹 | `profiles.py` `STYLE_VISUAL[...]` |
| `card_suffix` | 角色卡提示词尾巴 | `asset_style.py` `CARD_STYLE_SUFFIX_3D` / genre 的 `card_style_suffix_3d` |
| `scene_suffix` | 场景板提示词 | `asset_style.py` `LOCATION_EMPTY_SUFFIX` |
| `video_line` | H3 视频提示词的风格句 | `visual_grammar.json` 的 `style_line`（三本书现在全空） |

外加一个显式字段替代字符串嗅探：

| 键 | 取值 | 替代 |
|---|---|---|
| `render_family` | `3d` / `2d` / `photo` | `asset_builder.py:74`、`phase_cards.py:116`、`asset_prompts.py:7` 的嗅探 |

`profile.json` 的 `style` 从 `3d`/`2d` 改成认包名，并保留旧值别名：

```
"3d" → 3d-guoman      "2d" → 2d-cel（把现有 STYLE_VISUAL["2d"] 原样搬过去）
```

## 4. 分步

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | 建 `configs/styles/3d-guoman.json` 和 `2d-cel.json`，`visual_style` 从 `STYLE_VISUAL` **逐字节复制** | 断言两边字符串 `==`；三本书 `style_fingerprint` 不变 |
| 2 | `profiles.py` 改成从 `configs/styles/` 读，`STYLE_VISUAL` 保留为别名映射 | 现有 991 项回归全过 |
| 3 | 加 `render_family`，把三处嗅探换成读字段；`3d-guoman` 填 `3d`、`2d-cel` 填 `2d` | 对三本书跑提示词快照对比，要求字节一致 |
| 4 | genre 文件移除 `card_style_suffix_3d`/`_2d`，移进画风包 | 同上，字节一致 |
| 5 | 新建 `weimei.json`（从 `STYLE_APPROVED.md` 定稿）和 `live.json`（从 `live2_round.py` + `gen_fts_live.py` 收敛），`render_family` 分别填 `3d` 和 `photo` | 不影响在产书；新包单独出一张卡验收 |
| 6 | 沙箱 10 处 `STYLE` 常量改成读 `weimei.json`；`live2_round` 读 `live.json` | 沙箱重跑一镜，与旧输出对比 |

第 1–4 步的验收标准是**同一个**：三本书任意抽 20 个角色和 10 个地点，重构前后生成的提示词字符串逐字节相等，
`style_fingerprint` 不变。做不到就是改错了，不是"可以接受的小差异"。

## 5. 不做什么

- 不改 `STYLE_VISUAL["3d"]` 和 `["2d"]` 的文本内容。要调画风文案是另一件事，要单独评审，因为它会重画全书卡片。
- 不动 `visual_grammar.json` 的四轴和 `location_time`（诸天 733 条、雾月 364 条、星海 217 条），
  本次只接管 `style_line` 这一个空着的键。
- 不给在产的三本书改 `style` 值。
