# New-book Agent Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Run the existing new-book entry through cached reading, incremental chapter planning and normal Agent correction, with attributable usage and a live Aoshi pilot before batch expansion.

**Architecture:** Keep `setup_book_thin.py` and the native chapter planner. Extract the proven grouped merge into a book-independent planning module; make setup resumable and seed once. Reuse the sandbox Agent only for failed planning candidates, then return proposals through shared validation and native packing. Reuse append-only usage records.

**Tech Stack:** Python, pytest, existing HTTP/Phanthy transport and sandbox runner.

**Spec:** `docs/superpowers/specs/2026-10-15-new-book-agent-pipeline.md`

## Global Constraints

- DS41 only through `127.0.0.1:8789`, shared ceiling 8; no silent fallback.
- Preserve source registration, stable IDs, existing plans/media and every quota/attempt ledger.
- No new business acceptance gates, per-book recovery branches or authorization modes.
- Planning only; do not generate images/video or upload.
- No commit/push authorization; keep reviewable working-tree changes and backups.
- Do not expand the production batch before live representative success and cost review.

## Review Focus

- Restart after partial merge must reuse finished jobs without treating stale inputs as current.
- Existing adopted Aoshi reading and rich recaps must survive entry execution without whole-book reruns or seed redesign.
- Agent output must remain a proposal until ordinary source/binding validation and planning accept it.
- Repeated failed entries must retain the Agent attempt and budget; 402 must not fan out into a repair queue.
- Cached inputs and native usage must not be counted twice or merged with unrelated account activity.

### Task 1: Resumable entry and reading handoff

**Files:** `application/planning/setup_book.py`, `application/planning/lean_bible.py`, new `application/planning/reading_merge.py`; new `tests/test_new_book_pipeline.py`, `tests/test_reading_merge.py`.

**Interfaces:** Consumes registered `novel.json`, lean extract/candidates, existing `reading_cast.json` and Bible. Produces a reusable adopted roster, a single seed Bible and missing `recap.json` rows. Public setup gains `--stage setup|plan` and `--plan-parallel` while retaining setup as default.

- [x] Write failing tests for index-only startup, a single seed build, reuse of an existing adopted roster, preservation of rich recaps, grouped merge resumption and cross-group identity resolution. Minimal assertion: `assert calls == ['read', 'merge', 'seed']`; a second unchanged run must make none of those calls.
- [x] Run `python -m pytest -q tests/test_new_book_pipeline.py tests/test_reading_merge.py`; expect behavior failures before implementation.
- [x] Move grouped merge job creation, persisted input/result reuse and overlap reconciliation into `reading_merge.merge_reading(novel_dir, candidates, texts, *, settings, workers)`. Use the existing lean schemas and ordinary `ask_json` calls.
- [x] Update setup to register the source index before paid seed building, reuse adopted reading, perform merge when needed, build the seed once, and call `handoff_reading(novel_dir, chapters)` to fill missing recap rows from cached extracts.
- [x] Run the named tests and relevant lean/initialization regressions; retain actual command/results in the ledger. Do not commit.

### Task 2: Planning failure -> existing Agent -> normal planner

**Files:** new `application/planning/repair.py`; `application/identity/flow.py`, `semantic_review.py`; `application/production/flow.py`, `batch_cli.py`; existing episode-production reference if necessary; new `tests/test_planning_agent_repair.py`.

**Interfaces:** Consumes a native planning failure and its actual source/candidate. Produces a saved Agent correction proposal and ordinary validated script/clip plan. Identity proposal intake uses the same `merge_binding_changes`, source grounding and review entry, not direct passed/done writes.

- [x] Add failing tests proving a normal plan failure dispatches the sandbox Agent, a valid identity proposal must receive a fresh ordinary review, a stale/unsupported proposal is rejected, and restart does not renew the same failed Agent attempt. Assert image/video functions are never invoked.
- [x] Run `python -m pytest -q tests/test_planning_agent_repair.py`; expect failures at the missing orchestration boundary.
- [x] Implement `repair.repair_chapter(batch, chapter, *, config)` using the existing episode run_session engine (file tools only) and the existing source/candidate contracts. Persist the actual attempt and return valid proposals to normal source/binding review or fresh native --candidate validation. Preserve prior authoring budgets and failures.
- [x] Connect setup `--stage plan` to ordinary planning and this repair return path. Keep technical/credit failures outside semantic repair dispatch.
- [x] Run the named tests plus identity, batch and episode-input regressions; do not commit.

### Task 3: Attributable usage and production proof

**Files:** `llm/transport.py`, `llm/usage.py`, sandbox usage handling and tests; task-owned runtime evidence.

**Interfaces:** Consumes real HTTP request IDs, usage and sandbox assistant message IDs. Produces append-only attributed records and a per-pilot cost report; unknown usage remains unknown.

- [x] Add failing tests for HTTP ID/cache retention, native message deduplication and missing-usage reporting. Run them before implementation.
- [x] Extend existing metering only where metadata is lost; keep schema/task name, book, chapter and stage attribution.
- [x] Run the full suite, request one independent final review, and address important findings with regression tests. Respect other sessions' working files.
- [ ] Preselect unfinished Aoshi chapters from existing failures before live calls. Execute the public entry using cached reading and DS41 only. Record input/attempt/output evidence, model calls and costs. If credits are unavailable, finish all independent code/offline work and retain the pilot as unverified.
- [ ] Expand to remaining planning only after the pilot succeeds with an explainable cost report. Report exact current completion and blockers without resetting any ledger.
