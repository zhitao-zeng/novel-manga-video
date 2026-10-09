# Repair playbook

Use this for semantic input failures and post-render review/repair. These are
distilled lessons from31 Guangming cases referenced154 times, not chapter-specific
defaults. Choose a source-supported remedy from the current host capabilities;
the playbook does not require a fixed repair sequence or prevent a better remedy.

## Locate the first wrong artifact

Use `input/current-task.json` for targets and exact choices, and
`input/current-status.json` for the pending candidate and full allowances. Trace
the issue through original passage, adopted shot, selected picture, submitted
English request, selected take and final acceptance. A verdict is a claim to
check against evidence. Repair the earliest confirmed conflict; later retakes
cannot repair wrong dialogue ownership or a wrong reference card.

| Current evidence | Useful action and boundary |
| --- | --- |
| An old speech flag conflicts with the selected take's current analysis. | Compare the planned words, raw chunks and audible audio. Exact Mandarin homophones, names, punctuation and expected silence are not missing lines. Use the supported current-take recheck before a new generation; real missing meaning, added lexical speech and inaudible planned lines still need repair. |
| Video exists but subtitle/audio analysis is missing. | Treat this as an analyser-to-assembly handoff, not a failed performance. Let the host hydrate the selected take's analysis and assemble; do not fake empty chunks or retake for a missing analysis field. |
| Source state conflicts with the actual selected card. | Repair the card/binding before sampling. Use a phase for an existing actor, a character for a missing identity, and a location for a source-supported empty setting. A picture of a ring is not a person card; an animal must retain its animal role. If source and base card disagree, do not retake to satisfy the wrong card. |
| Start, action and end states conflict, or a change reaches shared cuts. | Correct the earliest source-supported field and genuine dependent shots together. Preserve valid cuts, words and timings. Inspect returned changed fields to distinguish true continuity dependencies from compiler drift; do not narrow away a real dependency to dodge its budget. |
| The take speaks words hinted by unbound narration/directions. | Inspect the actual request and audio. Replace the conflicting speaking/naming description with the authored visual action or silent reaction; keep protected dialogue, speaker tags and its slots. Do not append a contrary instruction while retaining the old cause. An English phrase is not automatically a proven cause. |
| Speaker, question/statement, group thought or private/public delivery is wrong in the script. | Use source-backed input turns to fix ownership, meaning or delivery at its source. A collective inner cry is not a lead actor's public line. Preserve what listeners can know, original causal meaning and unaffected shots. |
| An English request is empty or loses dialogue slots/directions. | Repair the final request inside the existing output contract, check it, then continue preparation. Reuse valid English and bound dialogue. A format error alone does not justify re-authoring an episode. |
| An apply batch succeeds on some clips and fails on others. | Read per-clip changed, accepted and blocked. Keep committed partial work and continue only current unresolved targets. Batch ok=false does not undo other clips' changes; the same clip still blocked is not a verified success. Already-retained clips need no redundant keep. |
| Frames show a wrong actor, clone, missing story actor, broken body or generated text. | Verify the actual selected frames and compare correct source/request/reference inputs. Use a permitted local edit or bounded retake for a real generation deviation, then inspect the new take; it may introduce a different error. No single prompt or seed change is a universal cure. |
| A risk or exhausted pending candidate prevents progress. | Use exact current risk_choices only for the offered trial. Read remaining attempts, revision, resume and dependent take counts separately: a remaining input attempt does not renew a spent pending revision. Trial permission is not acceptance. Report the precise missing capability and preserve the candidate/assets; do not repeatedly retry an exhausted operation or rewrite a true dependency. |

## Make the operation concrete

A helper that fails before a request is made (for example, an invalid endpoint
port caused by a dotenv comment) has a host/configuration problem. Report the
specific helper error and preserve the source-backed candidate and completed
assets. Changing wardrobe/source wording cannot fix that endpoint, and the
helper exit alone does not establish a failed image generation. Phase portraits,
closed views and expressions must follow the character route already configured
for base cards; local location/prop support does not move characters to that route.

Write the required file, call `check`, correct its specific report and submit
`apply`, `adopt` or `render` as appropriate. JSON in a reply is not execution.
If the host requires `review`, complete that step before `finish`; do not treat
missing review as a content defect. A yielded job is already submitted: end the
turn with its ID and continue when the host returns its result.

Use `resume:true` only for the unchanged saved proposal. New source fields/assets
belong to a new or permitted revised proposal. Reuse images already generated
by an incomplete operation instead of drawing variants to escape a compiler bug.
The host enforces the existing no-progress and actual-generation ceilings;
spending more text time does not reopen a clip's input or video allowance.

## Use and record experience honestly

`related_history` supplies relevant case evidence and retrospective conclusions.
Use a case only when its concrete cause and remedy fit current facts. Its
`changed` receipt proves an operation took effect; `accepted_clip` proves that
clip passed independent recheck. An accepted episode does not validate every
attempt, and unresolved attempts are lessons about failure, not verified recipes.
`not_recorded` means the retained log cannot establish a concrete remedy.

After a supported operation, explain the changed field, affected clips, observed
result and remaining uncertainty through the host result/decision fields. These
feed the shared memory automatically. Recording a new description, repeating a
keep without new evidence or obtaining a trial permission is not a new accepted
episode. Keep the original evidence, and let formal finish determine acceptance.
# 姿态和联动续接（2026-10-09）

姿态读取的镜号、人名由宿主Schema固定为对象键，Agent不需要重新抄写名单。旧台账仍按原格式保存、复用。名单报错必须给期望和实际名字；不能把名单对齐当作画面已出现这些人物。

只改光线、场景等字段时，复用未改变的画面/动作文字中的姿态事实，重新判断时间和入场关系，再计算继承；不要靠模型重新解释未改文字制造新的重拍依赖。真正改变动作、入镜名单的候选仍重新读取。

当前工具明确提供`resume_action`时才续原候选；`posture_protocol_recovery`只允许原文核对已通过的旧名单协议失败再续一次，保留图片和原计数。它不放行真实坐站矛盾，也不能追加第二轮拍摄。`dependency_budget`会列出请求片段、实际联动片段、当前拍摄次数和额度；依据这些信息修范围或使用已有条件额度，不能反复猜片段。

骨架的start_state/event/end_state也是姿态证据。场次与承接确有错误时，可按原文提交scene_id/scene_time/scene_transition；它们沿用同一源文核对，不能编造跳时绕过姿态矛盾。704的名单协议修复后，真实模型读出了局部地点纠正与前后旧地点的冲突，接下来应修源级场次或确实必要的地点范围，不能把协议通过当成全部问题消失。

已有片尾记录且片尾JPEG有效时，黑帧检查测量剧情时间范围，仍要求剧情连续黑帧少于1秒。413的完整检测区间135.56–139.16秒跨入4秒深色“未完待续”卡；剧情内部分0.792秒。保留完整区间和片尾边界，不豁免故事内的真实长黑屏，也不为统计误报重拍内容。


## 审计复核后的执行经验（2026-10-09）

输入能力以当前`input_operations`的new/revise/resume为准，它们与宿主执行共用额度判断。剩余输入次数只表示账面余额，不能证明同一候选仍可修订。所有入口关闭时记录具体候选、错误字段及上游动作；不要为了花掉余额重交相同提案。

`apply`受理会保存本次提交的副本，job_id与submission_id可对账。等待后续resume-inputs是另一笔操作；比对不同阶段文件不能证明“吞提交”。`check valid=true`只证明确定性参数校验通过，源核对、图片和编译仍可能明确拒绝。看最终changed/blocked和对应输入回执；不能用提交已受理代替实际生效。

未拍过的超长旧计划先由原打包器拆到当前H3上限，沿用原剧本。存在已拍次数的计划保留镜号和额度，不通过重新打包重置计数。英文请求缺失已有edit补建入口，必须保留实际绑定台词、参考关系与切点。

专名校正可容忍有明确邻近台词依据的近音误写；保留raw_hypothesis和corrections。词库校正不证明画面身份，不允许用已知另一角色名替换成期望角色；额外语音、漏掉关键含义和音量不够仍严格。无speech_issues键本身不证明语音门错误：先核对selected.passed与真实函数返回值。

复核角色性别、外貌、在场指控时，实际图片和原文是证据，旧评审前提需要重新核对。两者冲突先通过已有phase/location/source修复，不重拍以迎合错误卡面。无法确定原文事实时保留不确定，不编造统一身份；已确认的真实缺陷仍须新候选通过正式验收。

## 上游与耗尽任务闭环

当前repair_assets/repack是单独apply操作，check只验证不执行。repair_assets要求实际注册asset_id、原文引用和具体字段；独立判官先区分绑定、设计、图片和误判，不明身份停于证据。共享候选审核后才放入当前快照；图片通过不代表视频通过。宿主返回input_decision并在有依据的输入次数用完时开放一次恢复，正常输入与视频预检继续执行。

repack仅改明确源范围，保留其他请求和视频；新子段共用原来源额度和正在提交的占位，不能靠新镜号拿默认次数。同一范围只续这一轮，已用的条件补拍不能再自动加。英语翻译和输入预检由正常准备程序完成。

原活动时间耗尽时，只有宿主确认的新图片/输入/误判或明确已修故障才能恢复一次。局部600秒、源稿1200秒为整个专项活动窗口，最多两提案；等资源不计时，重启不重发，旧input/video/voice计数保留。候选仍失败必须报告具体字段和当前回执，不能仅写no_progress或把未完成修法记成成功。
