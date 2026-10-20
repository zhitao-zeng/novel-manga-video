# Episode output contract

Read the manifest's `input_mode`. For **script** input, read the authoritative
`input/chapter_script.json` and `input/clip_plan.json` and write:

```json
{"prompts": {"clip_01": {"shots": ["<Subject 1> lifts the cup. <DIALOGUE_1> The cup settles on the table."], "soundscape": "Wind outside the window."}}}
```

Include every existing video clip ID exactly once. Nonvideo cards retain their
existing data. The tools keep the original screenplay, words, owners, shot grouping,
reference selection/order, duration, seeds and source evidence. Existing English
may be replaced before a clip is filmed; later repairs use `output/repair.json` and
the existing request protections. Keep the supplied plan's timing and selected
picture/audio identifiers, and inspect the actual images when drafting. Saved
plans do not need to adopt the source-mode episode-length budget below.

The following full episode structure applies to **source** input:

Draft: write `output/episode.json` as one JSON object:

```json
{
  "title": "Episode title",
  "adaptation_notes": ["Meaningful compression or staging choices"],
  "chapter_script": {"shots": []},
  "clips": [
    {"shot_indexes": [1], "request_seconds": 10,
     "reference_ids": ["asset-id"],
     "h3": {"shots": ["<Subject 1> lifts the cup. <DIALOGUE_1> The cup settles on the table."],
            "soundscape": "Wind outside the window."}}
  ]
}
```

Each `chapter_script.shots` entry uses the existing chapter-script fields:
`index` (consecutive, starting at 1), `segment_ids` (source segments), `characters`,
`in_frame` (actually visible characters), `location`, `start_state`, `event`,
`end_state`, `camera`, `turns`, plus `duration_seconds`.
Optional fields include `light`, `actions`, `extras`, `props`, `scene_objects`,
`wears`, `sfx`, `listeners`, `independent_props` and verbatim `source_quote`.
`sfx` accepts a string or a string array in authored order; the host joins an
array with `; ` for the existing sound field and preserves the authored array.
`extras` accepts a string array or one complete string description; the host
keeps a single description as one array entry. `adaptation_notes` accepts a
string or a string array and retains that metadata unchanged.
Action `actor`/`target` may refer to a character, animal, prop, self or empty target;
use the original ownership. Each action is `{"actor":"name", "action":"authored action text", "target":"name or empty string"}`.
The host also accepts `description` for a missing `action` key, retaining its exact text.
Each turn requires `speaker_name`, `text`, `delivery_mode`;
`emotion` and `chat_target` are optional. For audience-only inner voice, set
`delivery_mode: "offscreen_dialogue"` and `inner_monologue: true`.
Use `visible_dialogue` for speech whose owner is visible in this shot, and
`offscreen_dialogue` for speech from a character outside the shot.
Use `turns: []` for a silent shot. The host supplies the precise turn/asset metadata
and original segment identifiers.

Clips cover every shot once in chapter order, and `reference_ids` uses actual manifest
IDs in submission order. Picture numbering counts image references; audio numbering
counts voice references. A clip's shot durations sum to its `request_seconds`; a
single-shot clip gives that shot the clip's duration. Total request seconds follow
the manifest target (normally 120–150); each clip follows its provider's limit
(normally 3–15 seconds). Dialogue in `prompt_h3` matches its covered shot turns.
Each clip uses one `location`; put a location transition in the following clip.

## Preferred H3 form: author shot bodies, let the host bind the syntax

For source input, replace `prompt_h3` with `h3` (supply exactly one):

```json
{"shot_indexes": [1], "request_seconds": 10, "reference_ids": ["asset-id"],
 "h3": {"shots": ["Close view of <Subject 1> lifting the cup. <DIALOGUE_1> The cup settles on the table."],
        "soundscape": "Wind outside the window."}}
```

For prepared script input, a `prompts` value may be the same shots/soundscape
object. Existing complete strings remain valid and should be reused.
Provide one English body per covered shot. Use `<DIALOGUE_1>`, `<DIALOGUE_2>`, etc.
exactly once in playback order within each shot, restarting numbering at one for
the next shot. Place each marker where that utterance belongs relative to actions.
Keep vocal delivery, physical sound and acting instructions beside its marker.
Silent shots and postmix visual shots have zero native dialogue markers.

The host supplies six sections, reference definitions, retention, exact authored
speech, `(Sx)`/audio bindings, `[Shot n]`, planned durations and cut times. Do not
repeat these fields in shot bodies. Subject numbering remains unique character
names in image-reference order, followed by noncharacter image assets. The host
does not invent or reorder action, camera, dialogue placement or reference choices.
Full request strings retain the original checks and are never silently rewritten.

H3 `detailed_description` labels clip-local stages `[Shot 1]`, `[Shot 2]`, etc., with
`Planned duration: X seconds.` for each. From the second stage, write its cut as
`At 00:SS.mmm,` using the cumulative preceding durations. Number unique character
subjects first in image-reference order, then noncharacter image assets.
Quote native speech as `<Subject N> (Sx) says <d>[Chinese] chosen text</d>`;
offscreen speech uses `says in an off-screen voiceover`, or `An off-screen voice (Sx)`
when its owner has no visual subject. Bind audio reference, owner and the same `(Sx)`
in the audio definition and first spoken voice-timbre instruction.
When the supplied mode is postmix, a pure inner-voice clip has one thinker and its
H3 request stages the visual action and scene sounds, with zero native dialogue
and zero Audio tags/definitions. Select the supplied thinker card and voice for
recording; its inner voice remains in the shot turns for the host's separate mix.
Keep the authored camera. Split mixed private/native delivery explicitly when
creating the screenplay, or report it through the host's supported correction.

Repair: write `output/repair.json` for the clips that need attention in the supplied episode evidence:

Confirmed dialogue defects use `action=input`, a verbatim `source_quote`, and a
targeted shot patch such as `{"index":7,"turns":[{"speaker_name":"林齐",
"delivery_mode":"offscreen_dialogue","text":"有原文依据的内心想法。","inner_monologue":true}]}`.
Replacing turns can correct words or delivery, or remove invented speech; cuts,
duration, factual meaning and untouched shots remain protected. Ordinary English
`edit` does not authorize changing dialogue. Before filming, a source false alarm
can use `action=recheck_input`; a whole-plan change needs explicit `action=replan`
with `reason`, `source_quote` and `instructions`. These are separate operations,
not mixed with clip patches in one decisions array.

```json
{
  "decisions": [
    {"clip_id": "clip_01", "action": "edit", "reason": "Observed cause and proposed change",
     "prompt_h3": "Complete replacement six-section H3 request"},
    {"clip_id": "clip_02", "action": "keep", "reason": "Current evidence supports this take"}
  ]
}
```

Return one decision per `attention_clips` entry. `retained_clips` already pass the
host's checks for the current requests and selected takes; leave them unchanged
without writing repetitive keep decisions. Automatic render evidence includes
detailed `targets` only for pending clips, with a complete chapter screenplay and
an all-clip summary for context. Explicit `review` exports full media evidence
when needed. If an older result has no attention list, use its supplied targets.
Actions are `edit`, `retake`, `keep`, `input`, `accept_risk`, `stop`.
`edit` replaces `prompt_h3`; `retake` proposes another take of the existing request;
`keep` retains the current take; `stop` explains why the host should halt this clip.
The host decides whether a proposal fits the remaining budget and passes acceptance.

`keep` requires an existing video. It cannot approve an input warning for an
unfilmed clip. Prefer `input` when source or reference material is wrong. For a
current pre-render wearer-card or scene-people uncertainty that you judge worth
trying with the existing references, use the main pipeline's explicit risk choice:

```json
{"decisions":[{"clip_id":"clip_06","action":"accept_risk",
 "reason":"Explain the actual reference uncertainty and why this authored shot can be attempted",
 "risks":{"wearer_card":["Existing wearer name"]}}]}
```

`risks.card_people` may list the current scene reference's book-relative path when
its existing people are the acknowledged uncertainty. Only currently reported
wearer/location risks are supported. The choice is scoped to this episode and
clip; it changes no screenplay, prompt, references or video allowance. The host
then renders through its normal checks. Actual speech, identity, content and
technical acceptance still decide whether the take can be used. This is not a
way to pass missing assets, invalid H3 bindings, source contradictions or a failed
video. Repeating an identical choice creates no new progress or extra take.

Before a source draft is adopted, `apply` accepts `input` requests for missing
assets. Name an existing draft `clip_01`, `clip_02`, etc., give a reason and a
verbatim `source_quote`, then supply `characters` and/or `locations`:

```json
{"decisions":[{"clip_id":"clip_01","action":"input","reason":"Missing source actor",
 "source_quote":"A verbatim passage from input/source.txt",
 "characters":{"Source character name":{"gender":"男","age":"中年","appearance":"Source-supported appearance","wardrobe":"Source-supported clothing"}},
 "locations":{"Draft location name":"Description of the empty setting"}}]}
```

Characters and locations must already occur in the target draft shots. The host
checks source support, builds and checks actual images, and returns new asset IDs
in `assets`; inspect their images through the updated `input/catalog.json`.
Then edit the provisional draft's references and H3 bodies and call check/render.
Asset creation does not adopt a plan or start video. Image-service interruptions
may use the existing one `resume:true` continuation; accepted cards are reused.
Use `characters` for a character without a current card; existing adopted
character states continue to use `phases` below. Edit provisional shot fields
directly instead of passing adopted-plan `shots` patches to this path.

An `edit` may instead provide `h3: {shots, soundscape}`, using the same structured
shot-body and dialogue-slot form as authoring. The host retains the current
reference associations, retention modes and music, and binds the protected words
and timing. Supply exactly one of `h3` and `prompt_h3`. A spent clip is reported
individually; it does not cancel another clip's permitted repair.

For a confirmed source/input defect, use `input` instead of changing the final
English to conceal a wrong reference. Include a verbatim `source_quote` from
`input/source.txt`. Optional `shots` contains patches at this clip's existing
shot indexes; supported fields are `location`, `light`, `visual_prompt`, `motion_prompt`,
`start_state`, `event`, `end_state`, `sfx`, `props`, `scene_objects`, `wears`,
`in_frame`, `extras`, `listeners`, `actions` and `speaker_map`.
`in_frame`/`extras`/`listeners` are lists of nonempty names. `actions` contains
objects with text `actor`, `action`, `target`; these correct source event ownership.
`speaker_map` maps existing shot speaker names to source-correct names, for example
`{"Wrong actor":"Actual source speaker"}`. The host changes only the names,
retaining dialogue text and delivery mode. Source events, camera, timing and
grouping stay intact; reattribution requires independent source support.

After adoption, `characters` can also request a missing card for a named visible
actor or actual speech owner in the corrected shots, using the design fields in
the asset example above. A wrong old reference set does not restrict the correct
new cast. Existing actor cards use `phases` for changed appearance. A missing
identity cannot be repaired by passing its name to `phases`. Unpictured groups
remain `extras`; give their real speech owner an identity when needed.
The host validates source, checks new images, registers episode-local metadata,
recompiles and translates affected clips, then publishes only a complete valid
candidate. Read the resulting actual references before render; do not rewrite
the whole adopted draft to change these bindings.
Optional `clip_ids` explicitly scopes an asset/input correction to existing clips,
including its target; supply a nonempty list of clip ID strings. Use it when a newly received garment or badge applies only
after an event; retain earlier clips' current reference choices. The source judge
also checks the scoped shots. Omit it for a genuinely shared chapter-wide state.
Choose registered locations by the supplied StoryBible names to reuse their images.
When the needed setting is missing or wrong, include `locations`, a map from each
selected location name to its source-supported empty-setting description. A new
name is registered only in this episode's Bible; the host draws an episode-scoped
card, checks it and rebinds affected clips. Use a distinct name for a distinct
setting, such as a carriage interior versus the surrounding dock.
`shots` is a list of patch objects. Its `wears` field maps a selected character to
a registered worn prop, for example `{"Character":"Armor"}`; use `{}` or `null`
to clear it. Clothing descriptions belong in `phases.characters.<name>.base_costume`
and biological traits in `appearance`, not in a string-valued `shots[].wears`.
`props` and `scene_objects` are lists of names.

Optional `phases` uses one state object per selected character. The existing
one-item-list form is also accepted and has the identical effect and allowance.

Before describing another version of an existing state, inspect `input/phases.json`
and the actual reference picture. Reuse an already registered state explicitly:
`"phases":{"characters":{"Character":{"reuse_asset_id":"character_001-existing-state"}}}`.
This retains its saved appearance/clothing and image. Do not combine
`reuse_asset_id` with new design fields; describe a new state only when source
evidence requires a different look. The selected ID must belong to that character
and already have a primary image. Source and actual-reference checks still apply.
Unfinished card generation uses its saved `resume:true` action instead of a new
description. Reuse does not increase input or video allowances.

Describe a genuinely new source-supported state with its appearance fields:

```json
{
  "decisions": [{
    "clip_id": "clip_01", "action": "input", "reason": "Explain the current input conflict",
    "source_quote": "Verbatim supporting passage from input/source.txt",
    "shots": [{"index": 1, "location": "Carriage interior"}],
    "locations": {"Carriage interior": "Empty wooden passenger compartment with side windows and fixed benches, daylight entering from the windows"},
    "phases": {"characters": {"Selected character name": {
      "label": "Source-supported state", "appearance": "Supported appearance correction",
      "base_costume": "Supported clothing design", "wears": "Registered worn prop name"
    }}}
  }]
}
```

Omit unneeded fields. Each phase describes one selected character's state for
the current chapter; the host assigns its asset ID and keeps the ledger local
to this episode. A phase can affect several clips in this chapter: all their
existing video allowances must permit the repair. It does not alter the book's
original Bible or reference files. Use current `remaining_input_attempts` and
`repair_actions` for each clip's effective permissions, including explicit recovery
grants. Do not infer a lower hard limit from an old stop message or the original
default limits. A listed edit/input/retake may be submitted before a conditional
extra is activated: validated apply and actual-input preflight perform activation.
An incomplete operation may be resumed once using its saved decision and generated
images: write `{"clip_id":"clip_01","action":"input","resume":true,"reason":"Resume the saved repair"}`.
`status.input_repairs` and the apply result show the failed step and `resume_action`.
`argument_errors` names invalid fields before any source-model or image work.
A malformed saved request is not offered as a service continuation; use its
remaining `revision_available` allowance to correct it, or stop if exhausted.
Changing only invalid parameters/error messages without applying a repair does
not count as progress and does not extend the operation limits.

When `repair_actions` offers `crop`, submit it alone:
`{"decisions":[{"clip_id":"clip_01","action":"crop","reason":"Remove the edge artifact while keeping the important action","crop":{"x":0.13,"y":0,"width":0.56}}]}`.
Coordinates and width are fractions of the original current take. The production
aspect ratio determines height; the window must fit. The host preserves original
footage, audio, ASR and generation counts, reuses an identical crop, then reviews
the derived take through the normal acceptance path. It does not alter the source
script or request, and must retain key people and events.
`revision_available` permits one changed source proposal after rejection. Correct
the reported attribution rather than repeating it. Shared phase/location changes
may patch shots in their dependent clips; `motion_prompt` and `end_state` also
support source-backed posture handoffs without changing events, words or timing.
Use that action rather than paraphrasing and resubmitting the whole proposal.
Identical costume repairs affecting several clips are handled together. If input
repair changes a clip also named by an English edit, that old edit is deferred;
inspect the current request before proposing a new edit. Inspect new references and returned current plan
before rendering; generation and independent acceptance remain outstanding.

When the host workflow client is supplied, keep the manifest's output file current:
`output/episode.json` in source mode or `output/requests.json` in script mode.
`render` validates the authored requests before submitting and exports the
independent review and current media evidence in the same operation. Use `check`
for draft diagnostics and `review` for missing or stale evidence. `clips_ready` with
`assembly_pending=true` means the current clips await a repair/finish decision;
whole-episode encoding is deferred while issues remain. Do not render again just
because the movie is absent. Content-clear renders assemble automatically;
`finish` also assembles a stopped candidate from the current complete clip set.
Final acceptance still requires the independent content checks and final technical
QC; `content_passed` by itself is not an accepted episode. When an operation
returns `accepted` or `yield`, end the turn with its job ID and pending operation;
the host waits and resumes this same session with the result. Continue from that
result without a polling loop or duplicate submission. For repairs, write `output/repair.json` and use
`apply`, following the client's command help. Then render/review the affected clips
and call `finish` with the actual accepted or stopped outcome. Host results and saved
evidence determine completion; writing a JSON file alone does not complete this mode.
