# Execution, evidence and repair

Read this before using the host workflow client or assessing a failed take.

## Host operations and errors

Use `/work/tools/episode.py --help` for current arguments. `check` validates the
mode's output; `render` adopts accepted requests and submits allowed generation;
`review` supplies current media evidence; `apply` validates proposed repairs;
`finish` records accepted or stopped delivery. The host owns service calls, caches
and remaining allowances. An accepted tool operation is not an accepted episode.

When an operation returns `accepted` or `yield`, save the reported job ID and
end this turn with the pending operation. The host waits and resumes this same
session with its result. On resumption, read that result and the supplied artifact
before selecting the next operation. Polling, sleeps and another submission while
that operation is pending are unnecessary. A connection or tool error is not a
generation or review result; report the concrete error and pending job, and use
the host's result to determine whether work completed before retrying.

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

For postmix, assess the silent visual take, the separate thinker recording, and
the mix as different material. Private thoughts remain audience-only; ordinary
offscreen speech retains its actual scene delivery. A lips-closed direction alone
has not proved that H3 follows it. Keep the approved camera, and use the host's
reported recording result before requesting another take.

## Choose an operation

`keep` requests an independent current-take recheck when the alleged failure is
unsupported or contradicted by current evidence. It does not clear a failure by
declaration. `edit` supplies a complete six-section request with the affected
conflicting wording replaced and protected words, bindings, cuts and timing kept.
Keep action sound and ending constraints consistent with the change. Adjacent
shots provide causal context, but a local edit is not permission to rewrite them.

`retake` uses an already executable request when current evidence supports a
sampling or performance failure. Explain that basis and the expected difference.
For unplanned speech, correct a supported request cause rather than repeatedly
requesting the same wording with another seed. If there is no usable correction
or allowance, choose `stop` and preserve the actual failure. A particular sound,
seed or reference image is not established as the unique cause by one outcome.

Before `finish`, assess the assembled episode's questions, explanations, decisions
and ending, its transitions, speaker/knowledge continuity, planned words and extra
audio, alongside the host's independent technical/content results. A format pass,
ASR pass, good frame or written output alone does not prove complete delivery.
Report pending or missing checks and remaining failed clips. `finish` records the
host's actual accepted or stopped result; it does not authorize bypassing a gate.
