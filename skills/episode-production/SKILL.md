---
name: episode-production
description: Author, execute and repair one novel episode from original text or an approved screenplay, using actual assets and the supplied host workflow client in a persistent workspace.
---

# Episode production

Read `input/manifest.json` for the input mode, authority, assets, duration limits
and the installed `skill_references`. Read [the output contract](references/output-contract.md)
and [H3 format](references/h3-format.md) before writing final requests.
Use [authoring](references/authoring.md) when creating shots or resolving identity,
wearing, visibility, action, sound or continuity. Use [review and repair](references/review-and-repair.md)
when executing tools or assessing a take. Read [case lessons](references/case-lessons.md)
only when a matching failure needs explanation.

In source mode, read the complete original chapter and own the screenplay, cuts
and final H3 requests. Preserve identities, action ownership, who knows or thinks
each fact, and the questions, causes, motivations and decisions needed for the ending.
Use confirmed aliases and source evidence; animals, objects, self-actions and empty
targets retain their actual roles. Inspect the supplied images and state their
reference role, wearing relations and actual visible contents.
In script mode, keep the approved shots, words, owners, camera, groups, seconds
and ordered references; write only `output/requests.json`. Report a source or asset
contradiction through the host's supported path instead of inventing a resolution.

Write each event once, with an opening state, chronological action and persistent
ending state that connects to the next authored cut. A correction replaces the
affected conflicting description. Physical sounds follow their scene sources and
action timing; speech, private thought and environment sound remain distinct.
For postmix, separate a pure single-thinker clip and keep its words in structured
turns for recording; its H3 visual request contains visual acting and scene sound.
Use the manifest's limits and the selected profile, while choosing cinematic rhythm
within the authority of the input mode.

Write all six H3 sections in affirmative English. Keep approved Chinese dialogue
exactly inside its language-tagged `<d>` marker and preserve the supplied reference
and speaker bindings. Use `None.` for an empty audio section. The supplied pictures
are references; concrete first-frame/keyframe use requires explicit host support.

With `/work/tools/episode.py`, use its help and run `check` on the requested output.
Correct reported input errors, then request `render` and `review` as needed.
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
