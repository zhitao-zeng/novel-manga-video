# Execution, evidence and repair

Read this before using the host workflow client or assessing a failed take.

## Host operations and errors

Use current-task.json before reading long historical conversations. Its
`risk_choices` gives exact names/paths: `wearer_card` contains the wearer name
alone, not the clothing name or a copied error sentence. `check` and `apply`
share deterministic risk/input validation; source/image judgments still happen
at execution. A check failure spends no input attempt and names allowed values
or shot indexes when relevant.

The first turn under this task view starts a fresh conversation; old transcript
files and the prior session pointer are retained for targeted history lookup.
Subsequent turns continue the new session. This does not renew operation time or
any input/video allowance. Do not read the entire archived conversation again.

`resume:true` runs the saved decision unchanged. Supply only clip_id, action,
resume and reason. To change shots/assets/source_quote, omit resume and use the
existing revision allowance. Do not attach changes to a resume: they are rejected.
An unused numerical input attempt does not renew a spent candidate revision.

Compiler failures distinguish changed clip order from changed dependent clips;
inspect the supplied error_details. The running host source is available at
`/opt/novel-code/novel_manga` read-only. Use it for a precise tool diagnosis;
workspace edits cannot change that host code. Record the concrete failing
fields instead of repeatedly submitting the same operation.

Use `/work/tools/episode.py --help` for current arguments. `check` validates the
mode's output, or validates repair arguments with `python3 /work/tools/episode.py check output/repair.json`.
Run that quick check before apply; it uses no model, image or input attempt.
`doctor` requests host service diagnostics after an endpoint error. It is a host
job: yield and read its result on resumption. Service readiness is not content
acceptance, and the report does not change repair or generation allowances.
`adopt` validates and saves an unfilmed draft without starting media. Use it when
an existing actor needs a source-backed phase or a scene correction before first
generation; then `apply` may repair those unfilmed inputs, and `render` uses the
prepared plan. Adoption cannot replace a plan after physical generation starts.
`render` validates and adopts requests, submits allowed generation
and exports its independent review evidence; `review` refreshes missing or stale
media evidence and exports full-episode detail when needed; `apply` validates proposed repairs;
`finish` records accepted or stopped delivery. The host owns service calls, caches
and remaining allowances. An accepted tool operation is not an accepted episode.

When an operation returns `accepted` or `yield`, save the reported job ID and
end this turn with the pending operation. The host waits and resumes this same
session with its result. On resumption, read that result and the supplied artifact
before selecting the next operation. Polling, sleeps and another submission while
that operation is pending are unnecessary. A connection or tool error is not a
generation or review result; report the concrete error and pending job, and use
the host's result to determine whether work completed before retrying.

The host can automatically resume a source-checked interrupted asset operation
using its one existing continuation. Keep the saved decision and generated images;
do not paraphrase every character's wardrobe to restart the same work. Registered
states can be selected with `phases.characters.<name>.reuse_asset_id`; inspect the
existing card and reuse its saved design rather than requesting a new variant for
each clip. A completed tool is followed by the ordinary render/acceptance chain
when the task enables `complete_started_repairs`, even if the Agent's time window
ends while that submitted tool runs. This does not grant more takes or another
Agent turn, and a pause still prevents new operations.

Automatic render results expand only `attention_clips`; `retained_clips` already
pass current host checks and need no keep decision or repeated frame inspection.
If `episode_technical_issues` is present, inspect the
listed clips to locate the responsible source video/audio problem; choose only
supported affected clips for repair. Do not write keep decisions merely to cover
all technical inspection targets. Existing take/input/recheck limits still apply.

Use the complete screenplay and all-clip summary for continuity. Explicit `review`
still supplies detailed evidence for every clip when the pending repair needs
that wider visual/audio context. Missing or stale evidence remains unresolved.

Fix deterministic input/format errors in your own allowed output, then run `check`.
A missing image, incompatible reference, provider limit or unsupported source
correction stays an input problem. Report it instead of changing approved words,
identity or timing to force acceptance. A failed or incomplete review remains
unresolved. Preserve the host's history and budget; changing an ID or moving a
request to another file does not create another allowance.

## Classify from current material

Locate the first supported defect across original source, approved screenplay,
clip plan, submitted English and actual references, raw take, and assembled media.
Use historical findings to explain earlier attempts, and use this candidate's
inputs, frames and audio evidence to judge its current state.

Inspect the supplied frames and actual reference cards. Compare the authored
crop, body count, wearing, faceplate state, persistent object state and action
order. A card supplies stable design, while the shot determines pose and visibility.
The same dark color under another light, a cropped part or a hidden face is not
by itself a changed identity or missing person. An empty state ledger does not
override an explicit current request.

Review ASR together with approved speech, delivery and any provided raw/whole-
audio evidence. Frames do not verify sound. ASR can omit or misrecognize short
words; subtitle suppression of short noise does not certify absence of extra
speech. For a silent shot, inspect unexpected speech as well as missing dialogue.
When evidence cannot establish a fact, retain the uncertainty rather than invent
an observation or mark it passed.
Use raw recognition, corrected text and explicit spelling substitutions as
different evidence. ASR is fallible, especially for names and terms: a spelling
difference or CER alone does not prove wrong words, a wrong speaker or a story
failure. Inspect the supplied raw ASR and native audio file; this client does
not provide an independent listening judge, so do not claim you listened from
frames or transcripts. Do not rewrite approved names to fit ASR, or silence
required words to clear a gate. A disputed result can request `keep` recheck;
unresolved audio evidence remains unresolved.

For postmix, assess the silent visual take, the separate thinker recording, and
the mix as different material. Private thoughts remain audience-only; ordinary
offscreen speech retains its actual scene delivery. A lips-closed direction alone
has not proved that H3 follows it. Keep the approved camera, and use the host's
reported recording result before requesting another take.

## Choose an operation

`input` repairs a source-supported cast/speaker/location/appearance/wearing/sound defect through
the host. Provide the exact source quote and supported shot patches or phase
fields from the output contract. The host verifies the source, builds required
phase images and recompiles dependent clips; no video is generated by `apply`.
Read the returned plan, new image references and remaining budget before the
next `render`. Stop when image generation, source checking or the unchanged
video allowance blocks the repair.

After adoption, `shots` can correct `in_frame`, `extras`, `listeners`, `actions`,
`start_state`, `event`, `end_state` and `sfx`. `speaker_map` explicitly maps a wrong
old speech owner to the source-correct name while keeping the original words.
For a real dialogue/knowledge-boundary defect, `shots[].turns` supplies the
source-correct ordered turns for that shot, including speaker_name, delivery_mode,
text and optional inner_monologue. It can remove invented speech or correct a
private thought made public. Supply a verbatim source_quote and explain the
actual defect. The host independently checks meaning and speech capacity, keeps
cuts/durations, and translates only the dependent clips. H3 `edit` still cannot
change dialogue. Use this targeted input repair rather than sending a small
dialogue problem back to the ordinary planner.

When the fixed pipeline reports semantic issues before filming, repair those
specific inputs through the same interface. `recheck_input` with source_quote
and reason requests one independent source recheck of unchanged inputs if the
finding is false. `replan` additionally needs concrete `instructions`; it is an
explicit whole-plan request for an unfilmed draft, available once. Existing
video retains its cuts and take limits. Do not adopt a replacement episode.json
to bypass scoped repair. The host resumes fixed preparation and generation.
`characters` can request a missing source actor card using appearance/wardrobe
(optional gender/age). The actor must be a named visible person or actual speech
owner in the affected shots. It need not be in the old incorrect references.
Keep unpictured groups as extras; do not dress another protagonist to imitate
a missing source actor, or use phases for an identity not yet registered.
Use phases for an existing actor's changed appearance, not a replacement identity.
All these changes undergo independent source review and actual-image preflight.
An adopted clip with no current video or pending video task may receive an input
repair before a failed render; do not generate known contradictory inputs first.

If an outfit is assigned to the wrong person in the screenplay, correct `wears`
and the contradictory visual wording first. Do not generate an image of that
person wearing the wrong garment merely to satisfy the old plan. A shared phase
or location repair may include source patches for its dependent clips in the same
proposal; correct all affected source fields together. Source rejection allows one
changed proposal with the specific attribution error fixed, not an identical retry.
An input proposal can also patch all currently blocked input clips in this episode
at once. Use this for the same wrong wearer repeated across shots; do not invent an
extra appearance phase or redraw a correct card just to reach the other shots.
One technical continuation and one changed source proposal have separate counters;
`revision_available` may remain true after the continuation was used. Completed
input repairs and physical video takes retain their original limits.
For an explicitly reported posture handoff, `visual_prompt`, `motion_prompt` and
`end_state` may express the source-supported transition, keeping the same events,
dialogue, camera and durations. Do not invent an additional event to pass a check.

Pure laughter recognized as repeated 哈/呵/嘿/嘻 is not additional dialogue when
the authored sound event calls for it at that time. The host preserves raw ASR and
reports `nonverbal_chunks`; do not remove a planned laugh to satisfy an old verdict.
Missing words and extra lexical speech still require correction.

`keep` requests an independent current-take recheck when the alleged failure is
unsupported or contradicted by current evidence. It does not clear a failure by
declaration. `edit` supplies a complete six-section request with the affected
conflicting wording replaced and protected words, bindings, cuts and timing kept.
Keep action sound and ending constraints consistent with the change. Adjacent
shots provide causal context, but a local edit is not permission to rewrite them.

Use current `remaining_input_attempts`, `remaining_rechecks`, `conditional_extra_takes` and `input_repairs`
to choose a legal action. Historical blocked messages can describe older host
behavior. A legacy `tool_error` is excluded from input-attempt usage. A missing
saved decision requires a complete source-backed proposal using an available
revision, not repeating an invalid resume. A published correction can resume
its own remaining bookkeeping; a different correction needs a normal allowance.

A previous stopped result is not acceptance. When current capabilities and
allowances permit a correction, submit the supported operation instead of
repeating the old completion narrative. If there is no legal next operation,
identify the missing capability, rejected source evidence, exhausted allowance
or external dependency. A host programming error is reported separately from
a rejected source proposal. Service interruptions retain their partial artifacts.

The host counts actual published changes, not a new error message, as progress.
Two unsuccessful slices with no effective repair stop the current session. Rewording
an exhausted request or reusing a completed input receipt cannot extend it. When
the source judge is corrected, an eligible old source rejection exposes a saved
resume action and the host rechecks it once automatically; do not invent a new
proposal or request extra takes just to revisit that verdict. Positive remarks in
a source judge's problems list require the host's bounded confirmation, not a
screenplay rewrite. Real source contradictions remain blocking.

`remaining_takes` includes all unspent authorized takes, including conditional
extras that still require preparation. Inspect current evidence first, use `keep` for a
genuinely disputed verdict, or submit a concrete source-supported input/edit or
justified retake. Successful apply marks the preparation; the normal final H3
request and actual-reference checks must still pass before the host activates
the fixed extra take. Accepting a reference risk alone does not activate it.
The authorization cannot be replenished by restarting or changing clip IDs.

If input recompilation left a clip without `prompt_h3`, `edit` can supply its
English from the current prepared plan, using structured `h3` or a complete
request. Current references, dialogue/speakers and shot timing remain protected.
Put native speech only in the detailed shot bodies; summary and soundscape must
not add or repeat dialogue. `retake` cannot restore missing English by itself.

`retake` uses an already executable request when current evidence supports a
sampling or performance failure. Explain that basis and the expected difference.
For unplanned speech, correct a supported request cause rather than repeatedly
requesting the same wording with another seed. If there is no usable correction
or allowance, choose `stop` and preserve the actual failure. A particular sound,
seed or reference image is not established as the unique cause by one outcome.

When status reports `review_required`, run `review` before finishing. Existing
videos cannot finish using a verdict from an older review policy, even with zero
remaining generation allowance: review itself does not spend a video take.

Before `finish`, assess the assembled episode's questions, explanations, decisions
and ending, its transitions, speaker/knowledge continuity, planned words and extra
audio, alongside the host's independent technical/content results. A format pass,
ASR pass, good frame or written output alone does not prove complete delivery.
Report pending or missing checks and remaining failed clips. `finish` records the
host's actual accepted or stopped result; it does not authorize bypassing a gate.
# Source dialogue capacity and autonomous recovery

The host supplies current diagnosis and observed prior outcomes. Read these before
choosing a repair, and act through current repair_actions without asking the
operator to restate the problem. A resolved old verdict is not a current failure.

For source input with overfull dialogue, action=input may include
`shots: [{index: 7, turn_texts: ["Short complete first turn", "Short complete answer"]}]`.
Supply every original turn in order; speakers and delivery modes remain fixed.
Use the verbatim source_quote and preserve decisive questions, answers, causal
conditions, negations and numeric meaning. The host checks that this is an
overfull source shot, that text is shorter, and independently checks its meaning.
Frozen script input cannot use turn_texts. Existing take limits still apply.

Pure nonverbal laughter and exact Mandarin homophones are now evidence to retain,
not automatic reasons to reshoot. Current review refreshes saved audio under the
new policy. Use that outcome before requesting more video. Actual missing
commands, long missing passages and extra intelligible speech still need repair.
