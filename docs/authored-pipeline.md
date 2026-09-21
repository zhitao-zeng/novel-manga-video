# 读书 → 人写分镜 → 出片

这条链路和原来的流水线共用圣经、卡和渲染，区别在中间那一段：分镜不是规划器写的，是六套写作技能各写一版，人挑一版，再绑定成 `clip_plan.json`。

三段各自的入口、各自能出什么错、错了怎么看见，都在下面。

## 0. 检查器先说

```bash
python scripts/pipeline_audit.py --novel-dir outputs/<书> [--storyboard <目录>] [--judge flashnext|none]
```

提示词里写过的规则，逐条变成断言。**结构用代码，语义用模型**：

| 层 | 检查什么 | 代价 |
|---|---|---|
| 结构 | 地点是不是 `名字：描写`；地点名唯一；镜头的地点和角色在不在圣经里；片段时长在不在 4–15 秒；分镜九列齐不齐、镜号唯不唯一、台词格式对不对 | 免费、可复现 |
| 语义 | 空场描写里有没有人、有没有可读文字、有没有一场戏的临时状态、有没有交代时段与主光源 | 一次 Flash-Next 调用／地点 |

有「错」就 `exit 1`。**结构那一层已经接进 `thin_batch.py`**，`--stage all|render` 之前自动跑，不通过就不渲（`--no-audit` 可以强渲）。

判官的每一条结论都要原样抄出它依据的那段原文，所以意见可以被反驳，不是一个可以调的分数。

## 1. 读书

```bash
python scripts/lean_bible_thin.py --novel-dir outputs/<书> --chapters 1-100 [--into-bible]
```

逐章各读各的、并行打到本地 Qwen（100 章 300 秒）；再把所有章一起交给一个 agent 做归并。

```bash
python scripts/lean_bible_thin.py --novel-dir outputs/<书> --chapters 1-100 --agent-input runs/<名字>
# 沙箱里：AGENT_MODEL=Qwen3.8-Flash-Next bash run_skill_keyed.sh <名字> runs/<名字>/prompt.txt
```

- **归并必须走 agent**，因为它逐个写文件。一本书的条目装不进一次响应：超品相师 1-100 同一份候选表，
  agent 写出 54 个人物 183 个地点（87 KB），单次调用只拿到 44 个人物、地点为空（输出截断）。
  判断需要"一次看完全书"说的是**输入**；写出答案不需要。
- **模型必须是 Flash-Next。** 试点在它上面成功四次（12–20 分钟）；用本地 27B 的两次都失败了——
  一次跑满三小时超时，一次 391k 输入撞上 262k 窗口报错退出。**那是选错模型，不是架构问题。**
- `--into-bible` **只并人物和别名**。它的长处是跨章认人；地点不如流水线自己的逐章提取——`诸葛庐` 会被当成一个地方（那里有 155 间房），描写里还会混进人。
- 但它逐章读地点，而圣经只学规划器恰好用到的那些，所以它能报出**圣经压根没有的地方**。超品相师第 4 章从食堂走到寝室联谊晚会，圣经两个都没有，于是那场重逢被排在了酒店门口。这类缺口它会列出来，`--add-missing-locations` 按名字加进去，剩下的交给下一步。

## 2. 分镜

**输入由圣经生成，不要手工维护副本。**

```bash
python scripts/agent_brief_thin.py --novel-dir outputs/<书> --chapter 3 --out runs/x3/input --skill drama
```

写出四个文件：`任务说明.md`（九列规范 + 台词格式）、`人物地点与画风.md`（本章人物 + **本章能用的地点，附描写**）、`source.txt`（本章原文）、`prompt.txt`（启动提示词）。

之所以要生成：原来这些是每集一份的手工副本。台词格式规则 00:30 加进源文件，而三十份分镜的输入是前一天 18:59 和 22:57 拷的——**全部按旧规范写**，364 条审查问题里 363 条源于此。副本里也从来没有地点表，六套技能给四个地方起了 32 个名字，有的把时段写进名字（`诸葛庐碑林深处·黄昏` 和 `·夜`）。

地点名单**留了出口**：这一章真出现名单外的地方，就自己起名并写进 `output/新增地点.md`。封闭名单会逼模型把新地方硬塞进旧名字，就像封闭演员表逼出错误的人。山音那一版正是这么用的——它认出第 1 章开场是白天的山门游客场，和傍晚的碑阵广场不是一个空间，另起了 `诸葛庐前广场` 并附上合规的空场描写。

跑六套技能：

```bash
/mnt/disk1/zengzhitao/tmp/agent-sandbox/queue_briefed.sh v3 1 2 3     # 前缀 章号…
```

每份出来先过检查器：

```bash
python scripts/pipeline_audit.py --novel-dir outputs/<书> \
    --storyboard <runs 目录> --storyboard-glob "v3-*/output/*.xlsx" --judge none
```

## 3. 绑定与出片

```bash
python scripts/plan_chapter_thin.py --novel-dir outputs/<书> --chapter 1 \
    --bind-storyboard <某份>/output/分镜表.xlsx
python scripts/thin_batch.py --novel-dir outputs/<书> --chapters 1 --no-eager-cards
```

绑定用的是**只填空的 schema**：九个人写的列不在 schema 里，模型只能填 `segment_id`、`source_quote`、`location`、`turns` 这些。给全量 schema 再叮嘱"别改"，14 个镜头只保住 1 个；只填空保住 14 个，文字相似度 86–98%。

## 地点没描写怎么办

```bash
python scripts/fill_locations_thin.py --novel-dir outputs/<书> [--from-audit 审查结果.json] [--only 地点名]
```

- 没有 `--from-audit` 就补空白；给了就按检查器报出来的逐条改写，保留旧描写里写对的建筑和陈设。
- 找证据时优先在 `cast_index.json` 记录的那几章里找。在全书里按名字的片段搜会搜到别的地方：`楼走廊` 的六次命中全是后面章节某栋别墅的二楼。
- 地点名本身说清了是哪类场所（车站出口、宿舍走廊、公路）时，允许按这类场所固定有的建筑和光线写——那是布景，不是情节。故事专属的地方仍然宁可留白。

## 已知还没解决的

- **长度门挡住重排**：一章一集，章有长有短。超品相师第 4 章估算 247 秒，上限 105 秒；第 5 章 108.5 秒也被判失败（超 3%）。重试次数用完就放弃，**旧排期原样保留**——所以一次修复之后的重排可能悄悄没生效，要看日志确认。`NOVEL_PLANNER_MAX_REVISIONS` 可以加，但根子是长章装不进一集。
- 圣经在重排跑的时候会被写，别在那期间手工改 `story_bible.json`。
