# Execute the approved pipeline/review split

Spec: ../specs/2026-10-06-pipeline-review-design.md
Worktree: feat/episode-agent. Preserve substantial pre-existing modifications.
No commits, pushes or media/take resets. User has authorized implementation and
deployment; do not repeat approval gates. Execute inline, one final fresh review.

1. Add tested per-kind location routing; retain GPT characters and existing cache.
   Verify a real required scene card on GPU16; handle measured service problems.
2. Add native production mode to existing host/admission: direct planner,
   preparation and renderer precede Agent review; stage failures do not author.
   Preserve existing prepared plan, source candidates, media and allowances.
3. Restrict shared memory to repair in this mode (including the task6 exception). Write short case
   documents and concurrent-safe type index; retain verified/unverified distinction.
4. Build missing batch assets through native workers, including reading-based
   candidates for not-yet-planned chapters; reuse compatible existing cached cards.
5. Focused/full tests, fresh independent review, fix actual findings. Roll the
   coordinator and existing model configs, retaining live workers. Verify native
   first-generation and subsequent Agent review with real artifacts.

6. User clarification during rollout: do not delegate dialogue semantics back to
the ordinary planner automatically. Add source-checked `turns` patches to the
repair interface, scope checks to changed/dependent shots, and route confirmed
pre-render semantic defects into targeted Agent repair. The normal path remains
fixed. Replan only on explicit repair-Agent request; preserve all take histories.

7. User approval2026-10-07: separate first-production admission from repair
   admission. Add `production_parallel` to the existing controller; retain
   `parallel` as repair-host capacity in this mode and keep DS8/Qwen4/H3-6 actual
   request semaphores shared. Production-only hosts defer at semantic/review
   handoff, preserving remaining time and files. Prioritize chapters with usable
   assets. A manifest from the existing bulk asset job identifies queued shared
   cards; chapters waiting for those cards yield their production host and resume
   only when those files exist. No extra pipeline, media resets or planning-length
   change. Test full repair queue + ready production, model assignment by lane,
   no Agent call in first-production host, and asset wait/resume without retry.
8. Remove the image helper600/1200second wall timers that include shared-image
   queue waits and multiple bounded requests; retain provider request/poll timeouts
   and bounded generation/review retries. Native preparation, Agent phase repair
   and the existing bulk job use the same rule. Retain known bulk results on
   restart and export its asset manifest. Run focused/full regressions and one
   independent review; roll coordinator and asset dispatcher while letting
   current child operations drain. Requeue only prior image-wrapper timeouts once
   with original remaining allowance. Verify live independent queues and asset
   waits, and record actual throughput without claiming a target in advance.

9. Subsequent user steering: keep a100-task ready/waiting workset, refill on
   completion, and exclude unavailable-resource queue time from execution budgets.
   Use the existing400chapter registry, not100unbounded model calls. Persist the
   workset; retain active and already selected tasks; dispatch ready native and
   repair work under independent host caps. Track shared-resource wait intervals
   per episode (union, never sum overlapping waits); native/tool and planner
   helper budgets charge active time. Model request retry/fallback budgets and
   H3 polling start/continue after resource admission, preserving request limits
   and retry counts. Image interfaces remain bounded per request. Test queue wait
   longer than a request budget, overlapping intervals, workset refill, and actual
   host handoff. Runtime target:100queued,12productionhosts,8repairhosts; real
   DS8/Qwen4/H3-6 concurrency unchanged.

10. Live03:27progress audit:100accepted, H3idle, only1nativehost despite13asset-ready
    unstarted chapters in repair lane. Correct native lane classification from
    actual host/semantic/media evidence rather than stale production_lane tags;
    allocate fresh native hosts across current model capacity instead of pinning
    a stale endpoint. Separately, owned qwen-image-service single-threadHTTPServer
    usesHTTP/1.1keepalive: one idle previous caller blocks all following clients
    (serviceGPU4idle,lastimage02:13,healthtimeout,acceptedidleTCPandqueuedPOST
    observed). PreserveserialGPUexecution and closeconnections afterresponses.
    Reproduce two-client service failure without loading a model, and stale-lane
    failure with actual scheduler fixtures. Retain original budgets/content/media,
    roll controller only and restart owned idle image service to load the fix.

11. Live07:16audit: pool100contains99repair/1normal while83pendingnativeoutside
    (40alreadyasset-ready); retainedrepairmemberspreventfirst-generationrefill.
    Ensureeachindependentlanehasrunnablemembersuptoitsconfiguredhostcapacity,
    evictonlyidleoversupplied/asset-waitmembers,retainallactiveworkandchapterstate.
    BulkassetdispatcherlostlegacyQWEN38endpointenvironmentduringV4roll: design and
    imagepeoplechecksdefaulttoinactive127.0.0.1:18120. Requireexplicitmodelendpoint
    onthisnewbatchCLIandbinditinprocesssothechildreninheritit; startupmetadata
    checkmustfailbeforepaidimageworkonunreachableendpoint. Retrysavedfailedasset
    workafterroutingfix,reuseexistingimagesandreviewthemratherthanblindredraw;
    image-presentreviewrecoveryusesthenativecardreviewentry. Preserveverifiedor
    flaggedresultsandphysicaltakebudgets. Testrepair-filledworkset,readymodel
    endpointpropagationandexistingimage-reviewswithoutgeneration;rollcoordinator
    anddrain/relaunchassetdispatcherwithcorrectmodelroute.

12. User approved2026-10-07: eliminate observed repair churn. Failed operations
    with different error text and reused input receipts must not reset the
    no-progress guard. Preserve partial real repairs and stop after two empty
    slices across process continuations. Source checks must confirm candidate
    defects once against the actual source and changed fields; positive remarks
    in a problems array cannot themselves reject a repair. Keep the raw verdict,
    confirmation and source evidence. Recover old source-only rejected receipts
    once under the corrected judge, using saved proposals, unspent operation time
    and existing input/take allowances; no blanket retry or content edits.
    Reproduce failures first, cover negative and interrupted recovery cases,
    run focused/full tests and a fresh final review. Roll only the coordinator,
    retain active hosts, and verify actual automatic recovery and throughput.

13. User steering: first video supply has priority over post-render repair. Add
    the requested first_pass_priority scheduling policy to the existing native
    mode. Classify first work by actual per-clip video files, including partially
    filmed chapters and semantic input fixes required before first filming.
    Admit this work to the12 production hosts; while runnable first work exists,
    do not start new post-render repair hosts (existing operations drain).
    First-pass hosts may invoke the exception Agent only for missing clips; their
    renderer cannot retake existing clips and hands off review after first video
    completion. Keep native source/reference checks and per-clip budgets. Do not
    block first clips on a filmed clip's missing asset or post-render review.
    Test scheduling, mixed episodes, renderer non-retake, host handoff and source
    repair permissions. Document the policy, review the complete increment, run
    full tests and enable it for this batch in the coordinator rollout.

14. User approved fixing idle DS capacity while all queued first-pass episodes
    retain Qwen assignments. When a first-pass host is idle at a completed slice,
    keep its preferred endpoint if capacity remains; otherwise use an available
    allocated model unless its persisted model_switch requires the fallback.
    Preserve the same workspace/transcript, pending operation ownership, all
    remaining time and counters. Do not move active hosts or an accepted/running
    host job. Resume the CLI with an explicit configured model so historical
    session model settings cannot override the assignment. Test8DS/4Q dispatch,
    fallback pinning, session/counter preservation, busy boundaries and restart.
    Run full tests/final review, roll only coordinator, and verify actual DS
    requests using resumed Qwen sessions and continued first-video production.

15. User approved main-based standard batch production. Add a workspace index
    adapter to Batch/Conductor for existing episode books and original chapter
    locks. Add native prepare stage (plan if absent, selected cards, H3, readiness
    receipt), separate from prepared-only render lanes. Reuse main planning
    blocks, batch workers=0 and global video/model limits; no Agent conversation
    in production. Readiness/blocked receipts are scoped to actual inputs and
    allow unchanged failures to yield. Preserve existing managed take history
    and prohibit old-video retakes during first production. Attach an independent
    Agent review/exception queue gated by actual producer handoffs; preserve
    original sessions and unspent budgets, plus accepted films. Tests must cover
    routing to distinct episode bibles/assets, original lease conflicts, prepare
    failure isolation and restart, batch-first generation without Agent/review,
    partial media preservation, budget continuity and review admission. Run the
    full suite and fresh final review, then roll the coordinator only, retaining
    active old hosts and the existing asset dispatcher. Verify actual standard
    planning/preparation/render processes and Agent handoff, plus real video.

16. User approved using idle DS capacity for input repair and film review. Keep
    standard production separate. In batch_review, count ready/running Agent
    turns against text admission, but release that admission while an accepted
    host tool runs after the Agent has yielded. Keep those hosts alive under the
    existing100-task bound, original leases and shared DS8/Qwen4 request locks.
    Prefer blocked first-video inputs over post-render work, including when the
    retained workset contains only reviews. Allow idle, unpinned sessions to use
    another allocated model; preserve refusal fallback, active jobs and budgets.
    Remove the aggregate ready-video gate from text repair dispatch. Corrected
    unfilmed inputs return to standard preparation before any Agent-owned video.
    First-pass H3 waiters precede queued repairs without preempting holders.

    Files: production/batch_review.py, agent_preparation.py, agent_models.py,
    episode_agents.py; agents/episode/flow.py and tools.py; media/resources.py;
    rendering/cli.py. Tests: batch-review dispatch/host continuation, original
    session and budget preservation, fixed producer handback, and real-process
    H3 admission order. First run new tests RED, implement the bounded changes,
    run focused then full regressions and one fresh review. Roll only the
    coordinator with review.parallel=12, retaining DS8/Qwen4/H3-6 limits,100task
    pool, live children and physical take allowances. Verify actual DS requests,
    automatic input work and uninterrupted first video, then update the ledger.

17. User approved the repair-interface/current-task trial after auditing526,
    578,613,648 and related recorded sessions. Reuse existing tools: pure input
    preflight through the same repair validation path, explicit resume-versus-new
    semantics, current risk values checked before apply, and first-pass scope
    errors that name rejected and allowed clips. Give each turn one current task
    view with applicable evidence/permissions and short relevant memory; keep
    complete status/history in files. Distinguish dependency expansion from
    actual recutting in compile failures. Read-only host source is available to
    the episode sandbox for diagnosis; common code changes remain host-owned.
    Detect consecutive completed turns without output changes or host handoff,
    including prose followed by unexecuted JSON; fall back once with existing
    time. Recover only matching old protocol/no-action stops once with unspent
    time, without resetting input or physical-take counts. Use actual saved
    proposals for offline preflight/diagnostic replay, not manual chapter edits.
    RED→GREEN regressions, full suite and fresh final review precede coordinator
    rollout; retain active old workers, model services and first-video priority.

18. User explicitly requested fixing every issue in the2026-10-08 pipeline
    audit. Prepare unfilmed clips independently: one failed translation/card/check
    cannot discard other usable clips. Complete the exact final request/reference
    check before adding a clip to executable stock; reuse the same cache at
    submission. Renderer receives only admitted clip IDs and continuously refills
    chapter workers as ready work arrives, using original leases and shared H3
    limits. Retain failed reasons/unchanged inputs and existing take counts.
    Test partial translation failure, cached final checks, selected first clips,
    input invalidation and refill while an earlier chapter remains running.

19. Reserve2of12 text positions for film review when that backlog exists; unused
    reservation can serve first-input exceptions. Include review candidates in
    the100-task pool, keep active hosts and model fallback restrictions, and keep
    H3 first requests ahead of retakes. Report executable unowned stock separately
    from locked/in-process clips and blocked inputs. Derive waiting-assets from
    current producer evidence; retain563asset results while exposing existing,
    prepared, needs_review and failed separately. Test persistent first-input
    backlog plus actual review dispatch, no review backlog borrowing, stale image
    wait labels and accurate available stock.

20. Fix confirmed common code defects centrally. H3 character declarations must
    not assert every reference is human; preserve old request parsing and all
    identity/dialogue bindings, normalizing only unfilmed requests. Input repairs
    must reconcile legitimate compiler dependency expansion without recutting or
    silently modifying filmed first-pass clips; use source-backed scope and
    retain original budgets. Recheck shared needs_review assets with actual image
    evidence through the existing batch owner, then bounded repair of confirmed
    shared defects; do not redraw all flagged images blindly. Persist reusable
    outcomes and regression tests, not chapter-name exception dictionaries.

21. Verify H3 capacity using actual serial inference and queue timings. GPU52
    cards0/3were confirmed unused H100s; user authorization covers adding one
    project-owned2card H3 instance there, reusing existing models/environment and
    keeping current544P8-denoising-step production quality. Check supported GPU
    grouping, health and one minimal real service probe before adding the endpoint
    to this batch's pool. Do not stop DS or other users' services, lower steps or
    promise3600video seconds/hour without evidence. Final full suite and one fresh
    review precede software rollout; active jobs drain. Verify partial stock,
    review acceptance, continuous generation, correct metrics and new H3 routing.
