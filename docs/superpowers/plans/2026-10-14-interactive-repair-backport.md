# Four-item interactive repair backport

Goal: integrate the user-approved compact evidence entry, failed-take evidence fallback, SD2.5 request repair and reference-image scope into novel main, preserving newer novel behavior.

Source: interactive-video-studio main 018e392. Target baseline: novel main ae4b878, including the existing image/context fixes saved unchanged as a prerequisite. Other main work (lean_reading and unrelated session notes) stays untouched.

## Implementation

1. Add regressions for primary.json selecting actual current frames/local source, an unselected failed video, and retained complete novel evidence. Add compact entry without dropping novel bible/entity documents or existing dedup/runtime options.
2. Add the existing Seedance stage protection and dispatch in the original managed repair loop. Select the grammar from the active renderer, including SD requests with stale H3 text; use a scoped context, not a global environment change. Keep H3 validate_prompt including dialogue_bindings and the current budget/publication APIs.
3. Carry declared identity_portrait scope into final-request checking, frame evidence and card descriptions. Invalidate caches on changed scope, retain old full-card caches, preserve novel uncertainty/advisory/casting/watchability rules and current wardrobe phases.
4. Run new regressions before/after, affected suites, full regression, then independent code review. Merge local main only after tests pass. Preserve unrelated WIP, current providers, model settings, generated data and task counters.

## Non-goals

No interactive CLI/topic adapter, fixed romance scene, QC waiver, split-view policy, seed/resolution/provider rewrite, new model call, video generation, service deployment or push.

## Progress

Existing related image/context WIP preserved at ae4b878; baseline 41 targeted tests passed. Worktree isolated on codex/interactive-repair-backport-20261014. User's explicit instruction is the implementation/merge approval; no repeated permission gate.

## Completed implementation and validation

- Compact current-take/local-source entry implemented with full chapter/entity/history evidence retained. Unselected failures retain real frame/request/ASR handoff.
- Scoped actual-renderer SD2.5 selection and protected stage edits use the existing managed publication/render loop. No interactive application dependency or global lane environment setting was added.
- Declared identity portrait scope reaches request checking, picture review and card descriptions; scope changes invalidate affected caches. Unmarked card-description caches retain their old key. Current-phase clothing is not overwritten by old base wardrobe.
- Existing H3 validate_prompt AST, provider recovery, adjudication and model configuration compared unchanged.
- 95 targeted regressions and 3438 full regressions passed. Independent read-only code review found no blocking defect. No model/video/service call or push.
