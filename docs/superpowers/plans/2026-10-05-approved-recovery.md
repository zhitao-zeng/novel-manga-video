# Approved book recovery implementation plan

> Implement inline with executing-plans; one final scoped code review.

**Goal:** Re-evaluate existing media under the user's explicitly selected silence
policy, then allow at most one additional take only after review and valid inputs.

**Architecture:** Extend the existing EpisodeTools, renderer and Conductor. Keep
all original histories and sessions. Book configuration controls QC; a persisted
per-clip conditional authorization controls preparation and final generation
admission. No private per-chapter director workflow or second queue.

**Tech stack:** Existing Python environment, FFmpeg QC, native DS Agent, three H3
services at544P, existing per-episode and per-clip budget/history files.

**Spec:** User replies on2026-10-05: “累计静音占比改为提示，其他检查保持严格” and
“先复核并修好输入，只给确需重拍的片段追加1次”. Durable authorization and profile
backups are in runtime `approved-recovery-20261005/`; scope snapshot is
`remaining-plan-20261005/plan.json` (153unfinished,85chapters/111spent clips).

## Constraints

- Only silence_ratio becomes advisory. Speech,volume,long silence,black frames,
  source correspondence and final publication remain enforced.
- Preserve existing media,counts,attempt history and passed work. Candidate76
  remains isolated;chapter125 lacks sufficient source.
- Conditional authorization is not an immediate general budget increase. Store
  its fixed ceiling once,validate inputs before activating it,and never refill it.
- No commits,pushes,H3 service restarts or manual content patching are authorized.

## Implementation and verification

- [x] Existing-media QC: reapply book policy to saved measured verdicts when
  waivers change; inspect_media is needed only when removing a legacy waiver
  whose raw verdict was not saved. Do not render or re-encode. Add a native
  finish-existing task route for the existing Conductor. Tests must show the
  silence measurement retained,other failed gates still failing,media unchanged,
  and no model/provider invoked for mere technical re-evaluation.
- [x] Conditional repair: expose the pending extra allowance to Agent status and
  permit bounded preparation despite spent video allowance. Final H3 admission
  must require independent failure evidence/a valid repair decision and the
  existing source/reference/request checks before activating one frozen extra
  slot. Test rejected input,no authorization,other clips unchanged,and no refill
  after completion or restart. Reuse original artifacts and existing validators.
- [ ] Agent workflow: prioritize current evidence and valid reuse,then finish.
  Run the existing Conductor in batches;first cached-QC cases,then pending
  publication checks and authorized repair candidates. Record actual acceptance,
  physical new takes and unsuccessful terminal results separately.
- [ ] Run meaningful RED→GREEN regressions,full suite and final review. Validate
  real production outcomes and updated policy separately from code tests;the new
  QC policy makes earlier unchanged-policy throughput figures non-comparable.

Review focus: cached waiver removal;stale media;other technical failures;
concurrent clip grants sharing a ceiling;failed preflight must not consume or
activate extra generation;interrupted operations must not obtain another grant.

## Evidence and execution decisions

- Existing-media batch completed: 12 chapters re-evaluated, 11 passed the newly
  approved policy, total accepted 257. No new physical takes, model calls or
  changed non-silence measurements; chapter 47 still fails precise publication.
- The existing Agent handles publication rechecks through `keep`; no additional
  per-chapter controller is needed. The same native batch will cover those cases.
- Each qualified unresolved clip gets a fixed pending allowance. Preparation and
  recheck ceilings are fixed at authorization time; successful repair and actual
  request/reference preflight are required to activate the extra video take.
- Already adopted episodes use the repair prompt even with a fresh Agent
  conversation. This batch archives old conversations and preserves all media,
  source, repair evidence and actual generation history.
- Final local suite: 2426 passed, zero failures/errors. Scoped independent review
  found no concrete blockers. Native repair batch started at 2026-10-05 12:42:15:
  141 chapters, 12 host processes, DS shared 8 slots, H3 shared 6 slots across
  three 544P residents. 115 spent clips in 87 chapters have one pending extra;
  runtime before/admission/launch evidence lives in `repair-batch/`.
- Follow-up after the user asked to keep handling all remaining work: live
  old-code hosts consumed operation time while waiting for DS, then began turns
  with only 6/8/13 seconds left. Queue time is now measured separately and excluded
  from operation deadlines, including cancellation; active work, counters and
  grants remain spent. Two regressions RED→GREEN, full suite 2428 passed, scoped
  independent review no findings. Newly dispatched hosts use the fix without an
  H3/controller restart. Prior terminal failures have not been blindly reopened.
- Task125 is original chapter126. Both local source copies are incomplete
  (213 body characters). User asked for online lookup; the official Hongxiu page
  lists2178 words but requires VIP subscription. Exact URL and access evidence are
  in `source-125-lookup.json`; source has not been restored or fabricated.
- User subsequently requested a common budget increase, not per-case exceptions.
  The project already used90-minute operation windows; this batch's600-second
  override was too short. Restore5400-second windows across general/book/live
  task configs, with600-second repair turns and1200-second source-authoring turns.
  Existing longer overrides and all generation/input/recheck limits remain.
  New code defaults match. Already running hosts drain; queued continuation
  checks actual final reasons across the full existing scope and retries only
  timeouts/the confirmed fixed format failure once. No-progress/content-only
  failures and accepted episodes do not gain another run.
