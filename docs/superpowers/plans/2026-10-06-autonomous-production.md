# Autonomous Production Implementation Plan

> **For agentic workers:** Use executing-plans inline; one independent review after implementation.

**Goal:** Start 401–800 and make the existing repair loop diagnose, learn and act without repeated user prompting.
**Architecture:** Extend EpisodeTools/flow, existing speech evaluation and review evidence; persist diagnosis/outcomes in the existing run and book runtime directories.
**Tech Stack:** Python, current project venv, existing DS/Qwen services and H3 pool.
**Spec:** docs/superpowers/specs/2026-10-06-autonomous-production-design.md

## Global constraints

- Preserve all prior source bodies, accepted media, physical take counts and user work.
- 544P H3, 唯美3D, landscape 16:9; existing three services and shared capacity.
- No commits/pushes, no service/model restart; no per-chapter manual screenplay patches.
- User directly authorized implementation and launching; do not request a second approval for these changes.

## Tasks

1. [ ] Register the next 400 local source bodies, retain original numbering/title mapping and old metadata backup; launch native hybrid production with 6 hosts and record live PID/config/evidence. Verify old 400 bodies unchanged and endpoints available.
2. [ ] Add current diagnosis + durable evidence-backed production memory to the host prompt and outcomes. Tests: publication-only failure becomes actionable; no-progress retains concrete cause; successful/failed remedies stay distinct; recalled lessons do not duplicate entire history.
3. [ ] Add one refusal-triggered DS→Qwen switch. Tests: explicit refusal switches; ordinary completion, timeout and tool error do not; one switch only; original deadline/take history retained; session provenance recorded.
4. [ ] Relax demonstrable speech/review false positives. Tests: 箭/剑 and protected-name homophones pass with audible evidence; genuinely missing/extra speech still fails; nonverbal laughter remains distinct; current phase overrides contradictory base gender; slight clothing/color differences advisory.
5. [ ] Prevent overfull new source drafts and expose a bounded source replan for existing duration failures; connect missing asset/shared-shot/publication repairs. Tests retain source ownership, unaffected clips and consumed take history.
6. [ ] Run focused then full tests, independent review, fix material findings, roll new hosts onto tested code, and resume old failures using current permissions. Record actual accepted media and remaining blockers.

## Review focus

- Repeated refusal must not create unlimited sessions or reset the deadline.
- Phonetic matching must not clear absence of audio or long invented speech.
- Phase/source evidence must not become unconditional visual acceptance.
- Replanning must retain used generation budget and unaffected media.
- Memory must distinguish attempted from verified repairs and stale from current failures.

## Continuation: preparation handoff (2026-10-06)

7. Restore merged location records to the book, append names without renumbering existing assets,
   and use recorded chapter membership in planner location menus. Design a required bare location
   from its registered source chapters before drawing it; merged narrative descriptions are evidence,
   not empty-scene image prompts. Regressions: descriptive nonliteral locations, city/beach distinction,
   idempotent adoption, stable old IDs, source-grounded empty scene design.
8. Use existing Conductor `input_mode=auto` to run native preparation per chapter. An existing failed
   preparation or missing plan goes directly to the exception Agent; ready scripts execute unchanged.
   Preparation children count against host concurrency and resume across coordinator restarts.
   Preserve started runs and physical take history. No batch-wide image barrier and no new queue.
9. Export normal planning candidates, audit and preparation errors into the source exception sandbox;
   skip a second normal writer call and let the existing Agent reuse valid choices. Keep actual source,
   asset, request and media checks. Make preparation subprocesses use the loaded feature checkout.
10. Run focused/full regression and one independent review, deploy a new immutable snapshot, adopt
    reading locations, supersede the old whole-batch continuation without cancelling active image
    requests, and resume the existing production coordinator. Record actual handoffs and limitations.

## Continuation: DS and Qwen full production

11. Add persistent model assignment from allocated `planning.models` and endpoint-keyed
    `agent.model_configs`. `agent_models.configure/assign/observe/options` retain existing sessions,
    cap assignments by model capacity, balance new chapters and record refusal switches. Test
    DS8/Qwen4 dispatch, exhaustion of one model, restart ownership, external adoption and all
    role/credential overrides. No credentials in state or generated configuration.
12. Add `agent.normal_parallel=4` as a reservation in this same queue. Re-evaluate admission after
    every launch; prioritize normal preparation/ready first production until its quota is filled,
    and use otherwise-idle capacity for existing repair. Test a full deferred backlog, ready/failed
    preparation transitions and an empty normal queue.
13. Run focused/full regressions and independent review. Deploy tested snapshot with DS8/Qwen4,
    two provider-specific Agent configurations and Qwen helper allowance4. Resume the existing
    paused coordinator/state and verify real Qwen requests and native preparation, preserving
    original run/session/take histories. Report live use and actual media acceptance separately.

## Continuation: local preparation repair and source bindings

14. Classify unstarted preparation failures: keep usable locally-fixable plans in script mode;
    missing/structurally unplannable or stage0 missing-event cases use source; unreadable source
    blocks. Export source/audit/reading evidence to script workspaces, stage editable inputs without
    requiring completed English requests, and run current source/reference/request gates before
    any video. Reuse existing input/edit/accept_risk tools and unchanged cuts. Tests: missing English,
    missing image, source conflict cannot render, cached passing source check invalidates after edits,
    only affected requests translated, started histories/mode/budgets retained.
15. Remove the upstream identity/menu causes: offer chapter source descriptors as actual anonymous
    speaker names instead of forcing generic labels; keep source fine location candidates available
    and append only selected new places with source evidence and stable old IDs. Clarify reading and
    audit instructions that separate room/scene from building, and presence from speaking visibility.
    Under the existing Agent-managed preparation ownership, local audit failures hand off directly
    instead of invoking the legacy reframe path that treats every issue as a speaker correction.
    Test source descriptor normalization, fine scene availability/adoption and local audit handoff.
16. Run targeted and full regressions, independent review and fixes. Replay representative stored
    inputs read-only to verify routing and menu changes. Roll only this batch's coordinator and use
    tested code for newly-dispatched workers; existing workers finish their accepted operations.
    Preserve DS8/Qwen4/H3six and all film budgets; record real local repair/preparation progress.
## Follow-up tasks17–19: pre-adoption checks and summary loops

17. Make draft-asset check use the same side-effect-free argument validation as
apply before adoption; correct CLI examples to positional file paths. Test that
valid missing-card requests pass check without drawing or spending attempts.
18. Record summary-only endings and expose real preparation errors in recovery
diagnosis. After two consecutive summary endings without host handoff, use the
existing configured fallback once in a fresh session, retaining files, histories
and remaining time. A completed handoff never triggers this recovery.
19. Admit already blocked summary loops once through the same native retry path,
preserving their remaining active time instead of issuing a fresh timeout.
Validate, independently review and roll only the task coordinator. Do not
manually edit chapter content, relax media checks or grant physical takes.
