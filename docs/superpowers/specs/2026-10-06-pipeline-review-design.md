# Fixed production pipeline with Agent review

Approved by the user after the concrete four-step proposal: reuse GPU16 Qwen
Image, prepare assets, run fixed production, and use Agents only for film review
and repair with shared repair memory. Implement inline, preserving existing work.

Normal work uses direct model calls and existing native preparation/rendering
functions. The persistent episode host still owns scheduling, state, budgets and
publication. In `production_mode=pipeline_review`, ordinary production uses fixed
steps. Missing plans use the normal planner; prepared inputs and media are reused.
User clarification: confirmed semantic defects, including wrong dialogue,
speaker, inner voice or knowledge boundary, belong to the repair Agent even when
found before filming. This exception gets the existing draft, source and concrete
issues; it does not become normal Agent authoring. Missing files and mechanical
request errors stay in the native pipeline. Source-backed `turns` repair can
correct words/delivery while preserving cuts/durations and independent source
checking. A normal replan needs an explicit Agent decision, not an automatic
fallback from a difficult dialogue correction.

Characters use GPT Image2. Locations use existing Qwen-Image-2.1 on GPU16:8770;
props retain the established local route. Existing accepted images are reused.
Bulk asset preparation runs ahead of episodes using shared per-asset locks. It
includes the current batch's reading cast/places and referenced assets, not future
book entries. Actual scene generation is tested before launching the batch.

The same Conductor and episode workspaces continue to own the book. Existing live
workers finish their accepted operation/slice; new hosts use the new mode. No
accepted episode, paid image or physical take count is reset. Unfilmed source
workspaces may use native planning while retaining their original draft files.

Only repair work contributes to this mode's shared memory; pre-render semantic
repairs stay unverified until media acceptance. Cases
store observed issue/evidence, actual actions, outcome and applicability; failures
remain failures. Per-episode JSON/Markdown records and a locked type index support
parallel writers. Relevant cases are supplied without another summarizing model.

Acceptance: routing/phase/budget/memory regressions, full tests, independent review,
actual scene-card generation, ordinary native production without an authoring Agent,
targeted semantic exception routing, and post-render Agent handoff. Throughput and final acceptance are measured
separately from successful deployment.

2026-10-07 authorized refinement: first production and repair have independent
host capacities. Global model/video request limits are unchanged. Production
hosts in the interleaved mode hand off explicit semantic defects or completed
video by a persisted deferral. The later first-pass-priority policy keeps input
exception repair with first production until missing first videos exist.
Images queued in the existing bulk
manifest may defer a chapter without occupying a production host. Asset-ready
chapters go first. Queue time is outside image request timeouts: remove the
shorter helper-wide wall limits, retaining request/poll deadlines and bounded
retry counts. Do not alter the90second target/120second planning ceiling or
physical take limits as part of this request.

2026-10-07 later user steering: first_pass_priority uses physical per-clip video
availability to classify production, including semantic input repair and mixed
partially filmed episodes. These use the12 production hosts. Runnable/active first
work precedes new post-render repair dispatch. First hosts generate only missing
video, defer review/audio repair/assembly of existing video, and allow the exception
Agent only to fix missing-clip inputs. All formal final acceptance stays in the
post-render queue. Known source/input failures and existing take budgets remain.

2026-10-07 user approved reuse of main's standard batch producer after code
comparison. This supersedes first-pass Agent hosts as the normal producer.
Standard planning/preparation and render lanes operate independently. Batch
preparation builds a bounded stock of executable clips; render uses workers=0
and the existing global H3 cap. Agent review/exception repair has its own small
queue and cannot occupy a production host.

Existing isolated episode books remain authoritative. A workspace index maps
registered chapter IDs to their existing local book/episode and original lock
directory. Batch routing uses these directories without copying movies or
merging differing local bibles. Normal prepare/render and the Agent share the
original chapter lock; current workers drain before their chapter is claimed.
Preparation uses native planning, selected cards and H3 translation, writes a
readiness receipt and returns on failure rather than starting an Agent. Failed
input preparation is handed off separately. Render consumes prepared work only,
records actual takes through existing managed history, never retakes already
filmed clips, and defers formal review/assembly. Preparation receipts are reused
until actual inputs/assets change; failed work does not loop unchanged.

The standard coordinator retains main's planning blocks/render lanes. Its
independent review coordinator reuses existing Agent workspaces/sessions and
only admits producer handoffs. It preserves budgets, completed acceptance and
current operations. Generation completion and film acceptance stay distinct.
First-pass supply remains higher priority than admitting new repair work.

2026-10-07 user approved using idle text capacity for input repairs and review.
Batch-review admission counts text turns separately from live host tools; the
existing100-task pool bounds both, and actual requests retain DS8/Qwen4 limits.
First-video input exceptions precede film review and return corrected inputs to
the standard producer. H3 admission gives waiting first passes priority over
repairs; existing requests are never interrupted. Text repairs do not depend on
a minimum ready-video stock. Refusal fallback, same-session continuation,
remaining time, take counts and the two-empty-slice stop remain unchanged.

2026-10-07 later repair-interface approval: the current task identifies actual
targets, evidence and executable choices. Check/apply share deterministic input
and risk validation; resume cannot silently discard supplied changes. Compile
errors name dependencies and changed fields. Long history stays accessible but
does not masquerade as current work. Two completed turns without changed output
or host handoff can switch the configured model once, retaining time and budgets.
Matching old stops may resume once under this corrected interface. Sandbox
access to the running code is read-only; source judgments and final media gates
remain authoritative, and no new take/input grants are implied.

2026-10-08 user approved all audit fixes: executable stock is clip-granular and
already includes the final actual-request/reference check. A chapter's failed
clip is isolated; only admitted IDs go to first generation. Standard renderer
refills continuously, preserving original chapter ownership and physical takes.
Two text positions are available to film review so completed raw video reaches
acceptance while first supply retains video priority. Metrics distinguish
unowned executable work, active work, pending assets, blocked clips and formal
acceptance. Shared template/compiler/asset defects are fixed once through their
existing owners. Add a validated H1002GPU instance on currently free GPU52cards
0/3without changing other services or the production544P8stepquality setting.
