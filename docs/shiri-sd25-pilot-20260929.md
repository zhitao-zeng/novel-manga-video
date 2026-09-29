# 十日终焉 SD2.5 接入：新书准入与首集验收经验（2026-09-29）

第一本走 SD2.5 30 秒档的新书（shiri，1–100 章）。本条记录接入和首集验收中踩出的可复用经验；
当时的具体数据（章节数、卡面、耗时）以 outputs/shiri/ 与交付报告为准。

## 新书唯一入口是 setup_book_thin.py

`build_bible_thin.py` 单独初始化不会产出 `reading_cast.json`。新书 profile 带 `props: true`，
身份绑定校验（source actors need catalogue bindings）会把"有未命名人物在场"的章全部拦停——
只有 setup_book 的"逐章精读→沙箱归并→按名单重建圣经"链路会写这个名单。README 已改。
setup_book 还内置端点预检和圣经端点 fallback，比手工拼接安全。

## 归并必须链上"前期指称 → 后期实名"

归并 agent 把"清冷女人/白大褂/健壮年轻人/尖叫女生"标成路人（declined），没有链到后文自报
家门的章晨泽/赵海博/李尚武/林檎。后果是连锁的：前期章节这四人是"无名路人"，不绑卡、
不绑声线——第 1 集里女律师的台词被配了男声。别名补上后，卡（脸）和声线一起归位。
归并提示词已加规则（lean_reading.py MERGE_PROMPT）：**获得实名的前期指称必须建别名**。
注意另一个假阳性：台账里 `人龙 same_as 牛头人` 的 claim 配了"我是地牛"的证据（张冠李戴），
审查正确地判了 insufficient——证据和结论对不上的 claim 不如没有。

## 别名表与实体台账是两套账，会悬空

bible_aliases.json 写 牛头人→地牛，但实体台账（entity/entities.json）里没有地牛实体
（圣经后来长出来的人不进台账），绑定沿别名走到空处，UNKNOWN，章 21 卡死。
修法（留痕、可走查）：用 Ledger 自身的机制补——`_add_entity` 补建实体，再补一条
same_as claim 并 `decide(accepted)` 触发合并，人的决定写在 corrections.json。
合并后注意幸存者名字：`generic_name` 不认"牛头人"是泛称，e023 幸存，要手工把 canonical
改成实名"地牛"，否则规划器拿到的名字圣经里没有。

## 姿态台账拦的是"剧本没写",不是"模型不会引"

ch39 卡死 8 次：规划器写"齐夏蹲在铁板前"，前一镜他站着，任何一镜都没写他怎么蹲下的——
原文也没写。台账要求 transition 逐字摘自本镜文字，本镜没有就失败。两个模型都选择编造引文
而不是留空。导演注第一次写错方向（要求引用原文，原文根本没有），第二次才对：
**姿态要变，就把改变动作写进本镜自己的 start_state/motion_prompt 里**。这成了有效写法。
另：`--notes-json` 的导演注对单章纠偏有效（ch29 的换场问答也是这样救回的）。

## 渲染就绪检查的"人甲分离"拦截是有效的

人羊面具是穿戴物（wears），底卡画成"山羊骷髅头长在身上"。就绪检查拦下 4 段：
面具没有"被穿着"的阶段卡，成片出过人甲分离。修法：phases.json 加 wears 条目 +
build_phase_cards.py 建阶段卡。**改了 phases.json 之后必须重打包**
（build_clip_plan_thin.py，无模型调用），clip 里的参考绑定不会自己更新。

## 声线库自举有 8 秒门槛

build_voices_thin.py 从已渲染集的 ASR 对齐音频建声线，每人至少 8 秒有效语音，
不足不建（弱参考比没有更糟）。首集台词少的角色（心声为主的齐夏 6.2s）入不了库是正常的，
随集数增长补建即可。也意味着：**第一集的声音就是全书基准，第一集的声线缺陷必须修干净再放量**。

## 时长下限是软门（floor waiver 是设计）

decide_validation 的 `floor_waived`：重试到最后一轮仍只剩"时长不达标"时放行并留报告。
设计意图是宁可短、不许注水。所以 `min_seconds` 驱动重试但不锁死；要硬约束得另想办法。

## SD2.5 接入的基础设施备忘

- 本地 18012（NOVEL_LLM）已死：命令级覆盖到 18120（本地 Qwen）或 Flash-Next 即可。
- ngrok 域名被外来会话占用（ERR_NGROK_334）时：`PHANROUTER_ASSET_PUBLIC_BASE=` 置空 +
  `PHANROUTER_REFERENCE_ASSETS=0`，参考图走图像任务的自托管 URL，不再需要隧道。
- PhanRouter 分组权限是按 key 分开的：视频 sd2.5 一个 key、图像 gpt-image-2 另一个 key
  （`PHANROUTER_IMAGE_API_KEY`），资产库两个都没权限。模型清单端点对新 key 返回空表，
  不可信——以实际任务提交为准。
- SD2.5 的内容审核比产线自己的规则宽：带血迹的卡照常过。血迹门已按实测拆档
  （血迹级 report-only，伤口级硬拦，见 test_blood_gate.py 的历史记录）。
