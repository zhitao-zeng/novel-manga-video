# Remaining 65 Episode Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Implement shared tools inline; native production Agents perform episode repairs. No per-episode manual authoring.

**Goal:** 给剩余 65 章完整的自动恢复路径，补齐已采纳镜头的角色/素材修复，保持有界生成和实际验收。

**Architecture:** 扩展现有 EpisodeTools 的 input/apply、状态与恢复提示、现有审核和条件授权；复用原生 Conductor、素材供应商与编译链路。不新建调度系统，不清空旧会话、历史视频或预算。文档与运行盘点是本次交付；代码与新拍摄在执行阶段进行。

**Tech Stack:** Python、pytest、现有 Docker episode sandbox、DS OpenAI-compatible endpoint、Qwen Flash Next、H3 pool、FFmpeg。

**Spec:** [剩余 65 章方案](../specs/2026-10-05-remaining-65-recovery-design.md)

## Global Constraints

- 335 个已通过章节继续保留；当前范围为台账中的 65 章。
- 保留全部原始来源、片段标识、历史视频、物理生成次数与既有授权记录。
- DS 负责 Agent、核源、图片门禁和最终审核；Qwen Flash Next 负责 H3 英文成稿，任务共享并发为 2。
- 最终通过仍以实际媒体验收为准；最终交付保持 1920×1080、25 fps、H.264/AAC 和 JPEG 首尾图。
- 累计静音占比仅提示；其他语音、音量、连续静音和黑屏检查保持现行严格口径。
- 新补拍额度是本方案建议，尚未发放；执行前必须明确批准范围。不得默认补发已经用掉的旧授权。
- 不提交、不推送、不重启 H3/模型服务，不安装环境。使用项目现有 Python 环境。

## Review Focus

1. 错误的旧角色引用不能阻止核源后的新人物绑定；单人和群体必须保持不同语义（Task 1）。
2. 素材/翻译失败不得留下半发布的候选；其他通过片段及累计次数保持原样（Task 1、4）。
3. 旧会话说“已经停止”而当前存在合法修复路径时，恢复提示必须反映当前能力；服务等待不算无进展（Task 2）。
4. 画面通过不能覆盖语音失败；旧版本审核不能放行新 take；整集技术失败不能凭片段审核通过而消失（Task 3）。
5. 同一轮重启、并发激活、原有 wildcard 上限和用完的新授权都不能造成再次充值（Task 4）。

## Task 1: 已采纳镜头的核源重绑定与缺失素材

**Files:**
- Modify: `src/novel_manga/application/agents/episode/input_repair.py`
- Modify: `src/novel_manga/application/agents/episode/draft_assets.py`
- Modify: `src/novel_manga/application/agents/episode/tools.py`
- Modify: `src/novel_manga/application/agents/episode/contracts.py`（仅实际需要的输入契约）
- Modify: `skills/episode-production/references/review-and-repair.md`
- Test: `tests/test_episode_agent_input_repair.py`, `tests/test_episode_draft_assets.py`, `tests/test_episode_agent_composition.py`

**Interfaces:**
- Consumes: 现有 `input_repair.repair(ctx, row)`、`draft_assets._asset(ctx, role, name, design)`、`draft_assets.export(ctx, assets)`、`rebuild_clips(..., preserve_timing=True)`。
- Produces: `action=input` 允许经核源的 `characters` 素材申请和 `shots` 中的 `in_frame`、`extras`、`listeners`；派生角色集合由宿主重算。现有 `changed`、`input_repaired`、`blocked` 返回结构保持可用。

- [ ] 增加会失败的参数回归，在现有 setup/decision fixture 中写入：

```python
def test_adopted_input_accepts_explicit_cast_correction(setup):
    row = decision(
        shots=[{'index': 1, 'in_frame': ['甲'], 'extras': ['门口访客'], 'listeners': []}],
        characters={'甲': {'appearance': '成年人物', 'wardrobe': '黑色披风'}},
    )
    parsed = input_repair.normalize_arguments(row)
    assert parsed['shots'][0]['in_frame'] == ['甲']
    assert parsed['characters'] == row['characters']
```

- [ ] 用现有项目环境运行该用例，观察当前 unsupported fields 失败。
- [ ] 扩展参数形状校验并在产生图片前核对源文、候选入镜名单与真实说话人。列表字段必须为非空名称字符串的列表，不能按普通文字字段处理。

```python
SHOT_NAME_LISTS = {'in_frame', 'extras', 'listeners'}
# 在现有 normalize_arguments 的字段分支内：
if field in SHOT_NAME_LISTS:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise RepairArgumentError(f'{path}.{field} must be a list of names')
```

- [ ] 复用现有素材核源、缓存、图片检查和导出逻辑，使已采纳镜头也能请求缺失的真实角色卡；调用方仍经 input 回执与原有预算控制。不要直接把 draft_assets 的草稿整体采纳路径开放给已拍摄章节。
- [ ] 基于候选镜头和核源结果选择角色/群演，而非强制限定为旧参考图中的名字。重新建立引用、声线和身份映射；无法登记的实际说话人返回明确缺失身份，不得默认为另一主角。
- [ ] 保持局部事务：候选素材、剧本、编译和英文全部通过才发布；失败继续保留原计划。说话归属的纠正须有独立核源结论，不放开对白内容和时间约束。
- [ ] 增加真实编译回归：普通换人、缺卡说话人、无具名群体、拒绝原文不存在人物、翻译失败恢复原计划；检查非目标片段逐字段一致、累计生成次数不变。群体用例取 397 的数据形状，不将其固化为章节特例。
- [ ] 跑上述 3 个测试文件；在候选副本中验证第 397 章的绑定路径可以完成编译，检查发给 H3 的实际引用列表。该检查本身不拍视频、不发布生产候选。

## Task 2: 当前状态驱动的恢复与明确停止原因

**Files:**
- Modify: `src/novel_manga/application/agents/episode/flow.py`
- Modify: `src/novel_manga/application/agents/episode/tools.py`
- Modify: `src/novel_manga/application/production/episode_agents.py`（仅恢复调度所需部分）
- Modify: `skills/episode-production/references/review-and-repair.md`
- Test: `tests/test_episode_agent_scheduling.py`, `tests/test_episode_agent_conductor.py`, `tests/test_episode_agent_inputs.py`

**Interfaces:**
- Consumes: Task 1 的已采纳输入能力；当前 `EpisodeTools.status`、`repair_status`、`remaining_input_attempts`、`_made_progress`。
- Produces: 状态中的可执行操作和明确阻塞原因；既有连续两轮无进展、5400/600/1200 秒默认、资源等待计时和 job 所有权保留。

- [ ] 增加回归：旧 session 文本为 stopped、当前剩 1 次条件拍摄且新输入能力可用，下一轮提示必须列出 input 操作和激活条件；不得直接返回通过。
- [ ] 增加回归：完全相同的参数错误连续两轮仍停止；修复成果已经发布、宿主正等待媒体工作时不得记为无进展。
- [ ] 扩展现有恢复提示，使用当前 status 字段生成如下含义的指令，禁止把这一段作为模型自由总结的替代：

```python
recovery_instruction = (
    'Use the current host allowances and supported input operations below. '
    'A previous stopped result is not acceptance and does not revoke a current allowance. '
    'Submit one supported correction, resume a pending operation, or identify the exact '
    'missing capability, rejected source decision, exhausted allowance or external dependency. '
    'Do not repeat the previous completion narrative as an operation.'
)
```

- [ ] 在已有错误返回中区分工具缺能力/参数错误、源文拒绝、服务中断和额度耗尽；包含失败步骤及可用下一步。已支出的候选不能因重新分类而自动退次数，确属宿主异常且未产生内容工作的记录按现有 tool_error 语义处理并保留原记录。
- [ ] 本轮仅对修好能力覆盖的 task 续接一次，保留旧批次的 `issue_retry_started` 事实，不删除它来循环排队。当前没有可执行动作的章报告具体依赖，允许其他章继续。
- [ ] 跑上述 3 个文件，并回放 123/257 最后两轮“只复述已结束”的状态。只读回放不调用 H3。

## Task 3: 从当前媒体证据进入正确修法

**Files:**
- Modify only when a reproduced defect requires it: `src/novel_manga/application/review/execution.py`, `src/novel_manga/application/review/evidence.py`, `src/novel_manga/media/speech.py`, `src/novel_manga/application/agents/episode/tools.py`
- Test: `tests/test_agent_keep_recheck.py`, `tests/test_speech_evaluation.py`, `tests/test_episode_agent_assembly.py`, `tests/test_review_snapshot.py`

**Interfaces:**
- Consumes: 现有 `recheck_current_clip(directory, cid)`、`media_matches`、`speech.recheck`、技术 `media_qc.checks` 与具体 take。
- Produces: 当前 take 的有效审核；明确区分输入修复、已有视频复核、拼接修复和实际重拍的下一步。

- [ ] 冻结真实失败记录，核对现有回归是否覆盖画面通过但语音失败、新旧 take、黑屏失败、累计静音提示但连续静音失败四种情况。不为已正确且已覆盖的规则重复写测试。政策检查使用真实接口和字段：

```python
from novel_manga.qc import apply_check_policy

def test_ratio_advisory_keeps_long_silence_strict():
    result = apply_check_policy({
        'silence_ratio': {'passed': False},
        'long_silence': {'passed': False},
    }, ['silence_ratio'])
    assert result['checks']['silence_ratio']['passed'] is True
    assert result['checks']['long_silence']['passed'] is False
    assert result['passed'] is False
```

同一 take 的画面、语音、技术状态分别断言；使用已有 fixture 中的真实字段，不以最终布尔值替代各层证据。
- [ ] 29 章语音报警先调用已有核验：专名对齐只使用已知别名与原文；计划内笑声不能当任意对白豁免；额外实词或漏对白保持拦截。需要第二次真实识别时只对争议片段做一次，不把对文本的推断写成听过原音频。
- [ ] Agent 在 Task 1 的输入路径修复无对白镜头的发声矛盾；不删真实对白，不静音掩盖错误。已有独立复核次数用完的任务列入本轮明确授权范围，不能重复无限复核。
- [ ] 7 章技术失败定位具体时间范围：原始片段 vs 合成边界。前者进入局部修复，后者重组装。现有 QC 阈值不修改；不靠填充噪声通过静音检查。
- [ ] 对缺失/过期审核和交付中断使用已有 review/finish 续接；通过必须核对 manifest、技术/内容结果与有效视频，不能依赖历史 Agent 的文字结论。
- [ ] 对任何需要修改的公共逻辑先加入实际字段形状的失败回归，再最小修复；通过上述测试文件后结束本任务，不为 65 章分别加特例。

## Task 4: 本轮一次条件授权，不自动重复充值

**Files:**
- Modify: `src/novel_manga/application/repair/history.py`（如登记/保留新旧条件授权需要共用函数）
- Test: `tests/test_conditional_repair_budget.py`, `tests/test_episode_agent_input_repair.py`
- Runtime artifacts after authorization: 同一剩余清单、登记前快照、逐片段批准记录及实际物理次数审计。

**Interfaces:**
- Consumes: `generation_counts`、`generation_limit`、`preparation_limit`、`mark_extra_prepared`、`activate_extra_take`。
- Produces: 如需新增公共入口，固定为 `grant_conditional_extra(directory, cid, *, authorization, reason) -> dict`；相同 authorization/cid 幂等，返回已登记的资格，不能随调用再次增长。

- [ ] 增加失败回归：同一轮重复登记和执行重启不提升上限；新一轮保留旧授权事实，不继承旧 prepared/activated；耗尽后仍不得因再次调用登记而加一次。

```python
def test_new_round_grant_is_not_refilled(budget):
    history.mark_extra_prepared(budget, ['clip_01'])
    assert history.activate_extra_take(budget, 'clip_01')
    record = history.load(budget)
    record['prior_generation_counts']['clips']['clip_01'] = 3
    history.save(budget, record)
    args = {'authorization': 'approved-recovery-round', 'reason': 'one reviewed replacement'}
    first = history.grant_conditional_extra(budget, 'clip_01', **args)
    again = history.grant_conditional_extra(budget, 'clip_01', **args)
    assert first == again
    assert first['prepared'] is False
    assert first.get('activated', False) is False
    assert history.generation_limit(budget, 'clip_01') == 3
    assert history.preparation_limit(budget, 'clip_01') == 4
    history.mark_extra_prepared(budget, ['clip_01'])
    assert history.activate_extra_take(budget, 'clip_01')
    record['prior_generation_counts']['clips']['clip_01'] = 4
    history.save(budget, record)
    assert history.grant_conditional_extra(budget, 'clip_01', **args)['limit'] == 4
    assert not history.extra_take_ready(budget, 'clip_01')
```

- [ ] 复用现有条件授权机制。新资格为当前有效上限之外一次且仅用于已耗尽、已定位问题的片段；已有可用次数时直接沿用，不追加。旧授权保留在同一审计记录中；同轮已登记即返回原值。
- [ ] 准备阶段看 preparation_limit，实际生成看 generation_limit；仅真实预检通过后 activate。同步保留 wildcard 对其他片段的原有约束。
- [ ] 保留输入候选一次修正、服务续接与视频次数的区别。先预检再生成，不把 draw/check 计为视频，也不把已生成但审核失败的视频当成零消费。
- [ ] 跑上述测试文件。执行阶段先确定新增授权，才写生产 grants；本次方案阶段不写。

## Task 5: 分类验证后全量自动推进

**Files:**
- Modify: `docs/episode-agent.md`，描述最终实际能力与限制。
- Runtime: 既有 Conductor 任务配置，新增本轮限定的 admission/validation 记录。

**Interfaces:**
- Consumes: Task 1–4；保存的 65 章台账、同一 canonical run_dir、现有模型和 H3 资源配置。
- Produces: 本轮 runtime 目录的 `final-validation.json`，包括 `chapters` 数组；每行有 `chapter`、`passed`、`next_action`、`external_dependency`。另记录实际新物理段和新通过。未通过行至少有一个具体下一步或外部依赖；不把任务启动或退出当成验收。

- [ ] 本地全量验证：

```bash
PYTHONPATH=src /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python -m pytest -q
```

- [ ] 执行时安排一次针对上述变更的独立代码审阅；发现重要问题先复现再修复。未获额外授权不提交或推送。
- [ ] 用原生 Agent 对 33、123、257、397、27、381 执行完整修复及验收。只选择已经符合该类别工具与预算前提的任务；仍缺新授权的任务等待对应条件，其余任务继续。
- [ ] 核对实际修正后的引用和 H3 请求、当前生成回执、当前审核与最终输出。不得手工改逐段 prompt 来“帮试验过关”。
- [ ] 按类别放行剩余普通任务，沿用 6 host / DS 8（工具保留 2）/ Qwen 2 / H3 3×2。已有 H3 工作完成时立即让准备好的任务接续，Agent 不占着模型位等视频。
- [ ] 统计真实通过数、输入修复数、使用新授权的视频数、各服务等待时间；用首批真实数据更新后续时间预估。
- [ ] 停止分类输出到台账，队列完成时明确运行数为 0；不把程序停住说成仍在生成。没有变化的失败不自动开启又一轮。

## Task 6: 特殊章节和全书收尾

**Files:**
- Runtime: 第 125 章来源证据；第 76 章安全候选与审核记录；最终全 400 章 reconciliation。

**Interfaces:**
- Consumes: 完整合法原文或安全改编条件，以及 Task 5 的同一自动执行和验收链。
- Produces: 对这两章分别给出真实可恢复结果或明确外部阻塞，不将它们从分母中删除。

- [ ] 125：检查已有合法全文来源，保存章号对应和完整性证据；得到完整正文后启动同一 Agent。未得到时保留 source_blocked，不伪造原著。
- [ ] 76：保持原候选隔离，拟定非性化、非露骨的叙事表达并先审查；不采用年龄替换规避问题，合格后才进入生成。
- [ ] 最终按 1–400 的 canonical run 复核，已通过 335 章未被修改；每个新增通过都有内容/技术通过与实际影片。

```python
from novel_manga.util import read_json

# directory 是 Task 5 当前恢复批次的 runtime 目录。
def validate_coverage(directory):
    rows = read_json(directory / 'final-validation.json')['chapters']
    accepted_chapters = [r['chapter'] for r in rows if r['passed']]
    unresolved_rows = [r for r in rows if not r['passed']]
    unresolved_chapters = [r['chapter'] for r in unresolved_rows]
    assert len(rows) == 400
    assert set(accepted_chapters) | set(unresolved_chapters) == set(range(1, 401))
    assert not (set(accepted_chapters) & set(unresolved_chapters))
    assert all(row['next_action'] or row['external_dependency'] for row in unresolved_rows)
```

最终汇报必须区分：新增通过、已完成局部修正但仍未过、待真实补拍、输入/工具阻塞、等待外部原文/内容条件；报告预算和不确定性，不保证未经验证的 400/400。

## 执行状态

本计划尚未执行。本次仅形成设计、实施步骤与覆盖 65 章的只读清单。执行方式建议由当前 Codex 会话完成通用代码修改，再由既有 DS Agent 和 Conductor 批量修复；保留已有用户“不要逐个手修、不要反复重试”的约束。
