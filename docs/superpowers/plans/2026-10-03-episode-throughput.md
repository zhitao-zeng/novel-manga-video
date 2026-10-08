# Episode throughput repair implementation plan

> Execute in the existing feat/episode-agent worktree. Preserve all pre-existing changes; do not commit or push.

**Goal:** Reduce time spent adapting creative output to mechanical H3 syntax and prevent a long repair from monopolizing a production worker.

**Architecture:** Reuse the existing H3 composer, episode session, saved tool operations and batch dispatcher. Agent-authored shot text and dialogue placement remain authoritative. Deferred episodes resume their existing session and physical-take budgets.

**Tech Stack:** Python, pytest, existing DS and H3 services.

**Spec:** The user approved the in-chat design with “修吧”: fix speech-parser false blocks, generate fixed labels/timing in the host, and let exceptional work yield production capacity.

## Constraints

- Keep source traceability, selected references, dialogue ownership and independent acceptance.
- Keep the current 544P sampling and physical-take limits; never mark deferred work accepted.
- Retain full English requests already saved or deliberately supplied by an Agent.
- Use the project environment and existing services. No new queue, database or dependency.
- Changes to the running task must retain submitted H3 receipts and Agent session IDs.

## Tasks

- [x] Speech parser: extend the current delivery-modifier grammar to coordinated adverbs. Regress the observed `says gravely and clearly` failure and reject a second unbound utterance.
- [x] Mechanical composition: accept structured `h3: {shots, soundscape}` alongside the existing complete request. Reuse `story.h3.compose`, retaining exact authored dialogue punctuation, marker placement, timing, reference/voice numbering and postmix behavior. Cover both source and prepared-script input; preserve existing full requests verbatim.
- [x] Scheduling: add bounded Agent slices with an explicit deferred result and same-session continuation. Execute accepted host operations before yielding. In the running batch, prioritize fresh prepared chapters and process deferred work through a limited recovery lane using the existing result files.
- [x] Live rollout: run focused and full regression tests, replay actual failed drafts without submitting videos, restart only the authorized batch controller after draining host operations, then observe actual submissions/results.

## Review focus

Tests must catch dialogue duplication or reassignment, mismatched dialogue markers, offscreen/postmix mistakes, deferred episodes losing deadlines/history, and accepted tool operations being dispatched twice. Local tests do not prove a new hourly throughput; record live measurements separately.

## Execution record

- Before changes: 176 results / 70 accepted at 12:46. Chapter 176 spent 27 minutes before rendering; chapter 171 used eight Agent invocations. Full request parsing rejects coordinated adverbs despite an explicit speaker.

- 2154 regression tests passed (40.148 s); actual saved source drafts 115/176/186 passed local adoption. DS CLI max-turns probe returned resumable error_max_turns after 3 s. Empty-setting source-check probe returned consistent=true.
- Independent review: fixed soundscape speech injection with RED→GREEN tests; removed the runtime fresh-parent duplicate result write that could erase a recovery claim.
- Runtime claim test: simultaneous recovery claims select distinct episodes and cannot steal a live claim.

- Runtime deployed at 13:21:06, preserving six interrupted sessions, videos and take counts. Real new chapter 205 produced 9 structured clips (106 s) by 13:26:01 and H3 returned media by 13:28:57.
- Follow-up capacity correction uses existing locks: reserve two of eight DS slots for host calls, six per-episode render workers under unchanged H3 pool caps. Agent slices cap at 300 s/24 turns and four total invocations, preserving prior attempts.

- Final validation: 2158 tests passed in 41.462 s, 0 failures/errors. All three live H3 services showed two queued tasks and recent completions at 544P. Current processes retain their original call settings; later episode processes use reserved DS capacity, 300-second slices and six render workers without cancelling submitted video work. No commits or pushes.

- 14:27 live correction: the initial 300-second limit starved draft completion (41 timeouts / 51 deferred; 32 without a draft). Source authoring before adoption now uses 1200 s / 80 turns, repairs retain 300 s / 24 turns. Normal lanes claim unfinished source drafts before opening fresh chapters; existing draft files go first. No session/take reset.
- 2160 regression tests passed; isolated old/new dispatcher replay proves unfinished-only backlogs are resumed once by normal lanes. Reloaded at 14:41:07 after submitted media drained; 148 and 200 accepted before reload.
