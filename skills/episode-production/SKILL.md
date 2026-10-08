---
name: episode-production
description: Author, execute and repair one novel episode from original text or an approved screenplay, using actual assets and the supplied host workflow client in a persistent workspace.
---

# Episode production

When present, begin with `input/current-task.json`: it identifies the current
phase, targets, exact operation choices and evidence files. Full live state is
in `input/current-status.json`; older failures are history and do not expand
the current target set. Provisional drafts still use episode.json/check/adopt;
adopted repairs use repair.json/check/apply. Replying with JSON executes nothing.

Read `input/manifest.json` for the input mode, authority, assets, duration limits
and the installed `skill_references`. Read [the output contract](references/output-contract.md)
before writing final requests. Prefer the structured H3 form in that contract;
read [H3 format](references/h3-format.md) when supplying a complete request string.
Use [authoring](references/authoring.md) when creating shots or resolving identity,
wearing, visibility, action, sound or continuity. Use [review and repair](references/review-and-repair.md)
when executing tools or assessing a take. Read [case lessons](references/case-lessons.md)
only when a matching failure needs explanation.

For semantic input repair or post-render review, use the
[repair playbook](references/repair-playbook.md) to choose a remedy from current
evidence and permissions. It distils shared repair experience; `related_history`
provides the matching individual cases. Preserve correct work, complete the
required host steps, and record the actual result for subsequent agents.

In source mode, read the complete original chapter and own the screenplay, cuts
and final H3 requests. Preserve identities, action ownership, who knows or thinks
each fact, and the questions, causes, motivations and decisions needed for the ending.
Use confirmed aliases and source evidence; animals, objects, self-actions and empty
targets retain their actual roles. Inspect the supplied images and state their
reference role, wearing relations and actual visible contents.
In script mode, keep the approved shots, words, owners, camera, groups, seconds
and ordered references; write only `output/requests.json`. Report a source or asset
contradiction through the host's supported path instead of inventing a resolution.
That output is prefilled with existing English. The manifest's `request_draft`
lists reusable requests and specific missing or invalid clips. Reuse existing text
after checking its source and pictures; edit only supported defects or missing work.
Use the host's diagnostics instead of inventing extra Python or shell validators.
If script mode has `manifest.preparation_exception`, the existing screenplay is
provisional for the recorded local defect. Retain cuts, words and valid choices;
use `input` for source-backed bindings/cards and `edit` for English requests.
Do not author a replacement episode. The host can stage incomplete English,
then recheck the source, fill missing cards and requests before admitting video.
An offscreen source actor can retain a descriptive name without a portrait card;
do not rename a bartender to an unrelated generic traveller to fit the catalogue.
Batch related reads and independent reference inspections. Save the draft, run one
`check` covering all clips, correct its report together and submit `render`.
Use structured shot bodies to avoid repeatedly testing labels, durations or speech
grammar. A turn-limit handoff retains this session and files; resume the current
draft rather than re-reading every guide or rewriting accepted work.
Open reference images using the catalog's `image` paths. Its `path` values are
host-side book addresses, not files to search for inside the sandbox.
For a confirmed input conflict, use the `input` repair described in the output
contract. The host validates source evidence, corrects only supported input
fields, builds episode phase cards and recompiles dependent clips. Keep the
valid actions, camera and durations. For source-confirmed dialogue defects,
shots[].turns can correct words, owners and private/public delivery; independent
source checking remains mandatory. Do not repeatedly retake an
unchanged contradictory input.
When a source draft needs an existing actor's phase or another input correction
before first filming, use `adopt` to save the validated draft without video,
then `apply` the source-backed correction and `render` the prepared plan.
Task-specific editorial instructions remain authoritative on every continuation.
For a missing or incorrect setting, `input.locations` can draw a source-supported
empty scene and bind it to this episode's shots. Use `status.input_repairs` to
inspect partial progress. When it provides `resume_action`, submit that action
once to continue with already-generated images instead of starting a new repair.

Write each event once, with an opening state, chronological action and persistent
ending state that connects to the next authored cut. A correction replaces the
affected conflicting description. Physical sounds follow their scene sources and
action timing; speech, private thought and environment sound remain distinct.
For postmix, separate a pure single-thinker clip and keep its words in structured
turns for recording; its H3 visual request contains visual acting and scene sound.
Use the manifest's limits and the selected profile, while choosing cinematic rhythm
within the authority of the input mode.

Prefer affirmative English in all six H3 sections. Style warnings alone do not
require rewriting an existing valid request. Keep approved Chinese dialogue
exactly inside its language-tagged `<d>` marker and preserve the supplied reference
and speaker bindings. Use `None.` for an empty audio section. The supplied pictures
are references; concrete first-frame/keyframe use requires explicit host support.

Use `/work/tools/episode.py` to submit your authored output through `render`.
It validates the draft before generating, and returns independent review evidence
with the render result. Automatic evidence expands `attention_clips`; the current
verified `retained_clips` need no repair decision. Use the complete screenplay and
all-clip summary for adjacent context. Use `check` for draft diagnostics, and
`review` when evidence is missing/stale or full media context is needed. Use the current host state and operation
result supplied in your prompt instead of immediately querying `status` again.
When an operation returns `accepted` or `yield`, end this turn with its job ID and
pending operation. The host waits and resumes this same session with the result;
continue from that result rather than polling or submitting the pending operation again.
Inspect actual frames, ASR, submitted requests and current verdicts before `apply`.
Use the reported remaining budget, explain each keep/edit/retake/stop from current
evidence, and keep edits coherent with adjacent clips. Tool errors and incomplete
reviews remain unresolved; use `finish` to report an accepted or stopped outcome.
Assess the complete assembled story and audio as well as individual clips: format,
ASR or technical passes alone do not establish a complete, accepted episode.

The host owns checks, generation, extraction, independent acceptance, caches,
budgets and publication. Keep work and helper scripts in this episode workspace.
With file tools alone, return the requested JSON for the host to execute.
