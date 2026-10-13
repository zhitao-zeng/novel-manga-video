# Agent repair integration implementation plan

> Execute inline with executing-plans; preserve existing production state and other worktrees.

**Goal:** Make observed repair knowledge available at the existing Agent entrypoint, consolidate retry/context rules and remove obsolete skill instructions.

**Architecture:** Retain check/apply/render/finish and the existing memory records. Integrate the tested routing/context portions of 40ff740 and 97d3f37 into current main without their unrelated allowance/preview branches. Store concrete operations with their observed results; retrieve only relevant prior cases. One playbook owns procedural guidance; runtime owns permissions, clocks and routing.

**Spec:** User's approved post-delivery integration, recorded in `.runtime/guangming-four-followthrough-20261012/post-delivery-agent-integration.json`; current request confirms execution after all800 uploads.

**Constraints:** No new workflow platform, episode-specific branches, automatic allowance grants or changed acceptance standard. No production rerenders. Identity work and unfinished754 review changes remain with their owners. Configuration remains unchanged; if that changes, update the runtime fixture. All required tests must pass before any commit.

## Tasks

- [x] 1. Consolidate existing Agent invocation recovery. Files: episode/recovery.py, lifecycle.py, flow.py, session.py, bounded_recovery.py, current_task.py. Tests: test_agent_repair_integration.py and existing episode session/CLI/scheduling tests. Legacy fallback must not select Luna; transient failure retries primary within the same shared limit; explicit context restart wins over an old idle marker; completed host work is processed before retry. Preserve counters, old sessions and deadlines.
- [x] 2. Preserve and retrieve useful repair evidence. Files: episode/recovery.py, memory_summary.py, current_task.py. Tests: preserve an early successful edit after more than8 later checks; store the actual decision and failure; separate changed from accepted; exclude this run from prior-case retrieval; do not recommend unrelated clip remedies or successful finishes with no recorded fix ahead of concrete evidence. Current-task evidence links carry full saved decisions without repeatedly injecting history.
- [x] 3. Consolidate the installed Skill. Files: skills/episode-production/{SKILL.md,references/repair-playbook.md,references/case-lessons.md,references/review-and-repair.md}, episode/inputs.py and session.py as needed. Keep operational guidance in one place; remove fixed old models/limits and duplicate dated recovery instructions. Refresh skills in reused sandboxes; version changes must tell resumed agents to reread the current instructions. Use existing evidence (391/456 protocol,473 context,754 motion,560 failure propagation); never describe unreviewed generation as a successful repair.
- [x] 4. Validate normal-entry behavior and review the full change. Replay saved routing/current-task evidence in isolated state, with no model/GPU calls or copied production allowances. Run targeted regressions, then the complete suite. One independent final review under executing-plans; fix substantive issues and rerun relevant checks. Record exact results and remaining live-model uncertainty.

## Review focus

Stale activity/model/endpoint markers; failures after a host job already completed; a saved input still technically incomplete; wrong or missing speech alongside a cosmetic picture warning; skill updates on a resumed session with unchanged media/authorization. These are regression cases, not additional production gates.

## Progress

Exploration: main6bceede lacks the production routing/context fixes, while an older production worktree also carries independent budget/preview experiments. Port only the former. Memory currently drops all but8 attempts and keeps reason strings instead of complete decisions; this loses reproducible repairs. Existing `_install_skill` copies files on prepare; ongoing conversations need an explicit revision indication.

## Executed scope and rulings

- Retained the proven protocol/schema/judge-selection fixes from9dc9734/61083d1/541ca5a/7f81a1c and the H3 uncertain-submission replacement frome6081c0. These replace faulty existing behavior; temporary allowance/preview drivers were not imported.
- User explicitly asked whether relaxed review was included; integrated one shared viewer-impact criterion across request scope, frame review, classic review and existing video adjudication. Noncritical wardrobe/background count differences warn; material/unknown findings and independent speech failures remain. No added review round or authorization mode.
- Independent review found that overwriting invocation-start remaining time broke124 slice deferral. Fixed with separate current_remaining and added the600/300 normal-entry regression.
- A broad review_standard cache invalidation attempt caused unrelated rejudging; removed it. Existing historical verdicts are not silently rewritten, and existing keep/review remains the way to reassess an old claim.
- Full suite:3303 passed. Saved replay:34 actual old routing records selectGLM with budget/history fields preserved; three frozen current tasks retain targets/permissions. Live read-only validation: saved603 request becomes one clothing warning, constructed simultaneous one/three-person request stays blocked. The actual model was the already-authorized CodexLuna standby, not the configured judge label; no H3 or production writes.
- Final independent review of the relaxed-standard increment found no blocking issue; live movie-judge accuracy is not established by these input probes.
