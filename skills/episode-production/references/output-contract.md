# Episode output contract

Read the manifest's `input_mode`. For **script** input, read the authoritative
`input/chapter_script.json` and `input/clip_plan.json` and write:

```json
{"prompts": {"clip_01": "Complete six-section English H3 request"}}
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
     "reference_ids": ["asset-id"], "prompt_h3": "Complete six-section H3 request"}
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
use the original ownership. Each turn requires `speaker_name`, `text`, `delivery_mode`;
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

H3 `detailed_description` labels clip-local stages `[Shot 1]`, `[Shot 2]`, etc., with
`Planned duration: X seconds.` for each. From the second stage, write its cut as
`At 00:SS.mmm,` using the cumulative preceding durations. Number unique character
subjects first in image-reference order, then noncharacter image assets.
Quote native speech as `<Subject N> (Sx) says <d>[Chinese] chosen text</d>`;
offscreen speech uses `says in an off-screen voiceover`, or `An off-screen voice (Sx)`
when its owner has no visual subject. Bind audio reference, owner and the same `(Sx)`
in the audio definition and first spoken voice-timbre instruction.
When the supplied mode is postmix, a pure inner-voice clip has one thinker and its
H3 request stages the visual action; its inner voice remains in the shot turns for
the host's separate recording and mix.

Repair: write `output/repair.json` for the whole supplied episode evidence:

```json
{
  "decisions": [
    {"clip_id": "clip_01", "action": "edit", "reason": "Observed cause and proposed change",
     "prompt_h3": "Complete replacement six-section H3 request"},
    {"clip_id": "clip_02", "action": "keep", "reason": "Current evidence supports this take"}
  ]
}
```

Return one decision per supplied clip. Actions are `edit`, `retake`, `keep`, `stop`.
`edit` replaces `prompt_h3`; `retake` proposes another take of the existing request;
`keep` retains the current take; `stop` explains why the host should halt this clip.
The host decides whether a proposal fits the remaining budget and passes acceptance.

When the host workflow client is supplied, keep the manifest's output file current:
`output/episode.json` in source mode or `output/requests.json` in script mode.
Invoke `check` after edits; `render` submits
the checked requests; `status` reports completion and remaining budget; `review`
returns the current media evidence. For repairs, write `output/repair.json` and use
`apply`, following the client's command help. Then render/review the affected clips
and call `finish` with the actual accepted or stopped outcome. Host results and saved
evidence determine completion; writing a JSON file alone does not complete this mode.
