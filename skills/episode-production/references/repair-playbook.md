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

